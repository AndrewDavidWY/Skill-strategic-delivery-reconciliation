#!/usr/bin/env python3
"""Offline structural/evidence reconciliation. Does not fetch or authenticate evidence."""
import argparse
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sys

DIMENSIONS = ('code', 'live', 'business')
CLAIMS = {'complete', 'in_progress', 'blocked', 'not_started', 'cancelled'}


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: '+key)
        result[key] = value
    return result


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamps must include a timezone')
    return parsed


def unique(rows, key):
    result = {}
    for row in rows:
        value = row[key]
        if not isinstance(value, str) or not value.strip() or value in result:
            raise ValueError('Missing or duplicate '+key)
        result[value] = row
    return result


def reconcile(data, now):
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported schema_version')
    objectives = unique(data['objectives'], 'id')
    packages = unique(data['packages'], 'id')
    if not objectives or not packages:
        raise ValueError('At least one objective and package required')
    criteria = {}
    for objective in objectives.values():
        required = objective.get('required_dimensions', [])
        if not required or len(required) != len(set(required)) or any(x not in DIMENSIONS for x in required):
            raise ValueError('Objective requires explicit, unique required_dimensions')
    for pid, package in packages.items():
        if package['objective_id'] not in objectives:
            raise ValueError('Unknown objective for '+pid)
        for field in ('deliverable', 'owner', 'integration_owner'):
            if not isinstance(package.get(field), str) or not package[field].strip():
                raise ValueError('Missing '+field+' for '+pid)
        if package['claim'] not in CLAIMS:
            raise ValueError('Unknown claim for '+pid)
        if package.get('due_date'):
            date.fromisoformat(package['due_date'])
        items = unique(package['criteria'], 'id')
        if not items:
            raise ValueError('At least one criterion required for '+pid)
        applicability = package.get('dimensions', {})
        if set(applicability) != set(DIMENSIONS):
            raise ValueError('Explicit applicability required for all dimensions: '+pid)
        for dimension, decision in applicability.items():
            if type(decision.get('applicable')) is not bool:
                raise ValueError('Applicability must be boolean')
            covered = any(c['dimension'] == dimension for c in items.values())
            if decision['applicable'] != covered:
                raise ValueError('Applicable dimensions must have criteria; excluded dimensions must not')
            if not decision['applicable'] and not decision.get('reason'):
                raise ValueError('Excluded dimension requires a reason')
        for cid, item in items.items():
            if item['dimension'] not in DIMENSIONS or not item.get('description'):
                raise ValueError('Invalid criterion '+cid)
            if not item.get('expected_version') or not item.get('expected_environment'):
                raise ValueError('Criterion requires expected version and environment')
            if item['dimension'] == 'live' and not item.get('freshness_hours'):
                raise ValueError('Live criterion requires a freshness policy')
            if 'freshness_hours' in item and (type(item['freshness_hours']) not in (int, float) or not math.isfinite(item['freshness_hours']) or item['freshness_hours'] <= 0):
                raise ValueError('freshness_hours must be positive')
            criteria[(pid, cid)] = item
        seen_dependencies = set()
        for dep in package['dependencies']:
            target = dep['package_id']
            signature = (target, dep['dimension'], dep['affects_dimension'])
            if target not in packages or target == pid or signature in seen_dependencies:
                raise ValueError('Invalid or repeated dependency for '+pid)
            if dep['dimension'] not in DIMENSIONS or dep['affects_dimension'] not in DIMENSIONS or not applicability[dep['affects_dimension']]['applicable']:
                raise ValueError('Invalid dependency dimension')
            seen_dependencies.add(signature)
    visited, visiting = set(), set()
    def visit(pid):
        if pid in visiting:
            raise ValueError('Dependency cycle at '+pid)
        if pid in visited:
            return
        visiting.add(pid)
        for dependency in packages[pid]['dependencies']:
            visit(dependency['package_id'])
        visiting.remove(pid)
        visited.add(pid)
    for pid in packages:
        visit(pid)
    records = {}
    for evidence in data['evidence']:
        key = (evidence['package_id'], evidence['criterion_id'])
        if key not in criteria:
            raise ValueError('Unknown evidence target '+str(key))
        if evidence['status'] not in {'passed', 'failed', 'pending'}:
            raise ValueError('Unknown evidence status')
        for field in ('source', 'version', 'environment'):
            if not isinstance(evidence.get(field), str) or not evidence[field].strip():
                raise ValueError('Missing evidence '+field)
        observed = timestamp(evidence['observed_at'])
        if observed > now:
            raise ValueError('Evidence is in the future')
        expiry = timestamp(evidence['expires_at']) if evidence.get('expires_at') else None
        if expiry and expiry <= observed:
            raise ValueError('Evidence expires before or at observation')
        records.setdefault(key, []).append((observed, expiry, evidence))
    states = {}
    for key in criteria:
        values = records.get(key, [])
        if not values:
            states[key] = 'pending'
            continue
        latest = max(x[0] for x in values)
        newest = [x for x in values if x[0] == latest]
        signatures = {(x[2]['status'], x[2]['version'], x[2]['environment'], x[1]) for x in newest}
        if len(signatures) > 1:
            states[key] = 'conflict'
        elif (newest[0][2]['version'] != criteria[key]['expected_version']
              or newest[0][2]['environment'] != criteria[key]['expected_environment']):
            states[key] = 'mismatch'
        elif ((newest[0][1] and newest[0][1] <= now)
              or ('freshness_hours' in criteria[key] and (now-latest).total_seconds() / 3600 > criteria[key]['freshness_hours'])):
            states[key] = 'stale'
        else:
            states[key] = newest[0][2]['status']
    results = {}
    for pid, package in packages.items():
        dimensions = {}
        for dimension in DIMENSIONS:
            group = [states[(pid, c['id'])] for c in package['criteria'] if c['dimension'] == dimension]
            dimensions[dimension] = next((s for s in ('conflict', 'mismatch', 'failed', 'stale', 'pending') if s in group), 'passed' if group else 'not_applicable')
        results[pid] = {'dimensions': dimensions, 'criteria': {c['id']: states[(pid, c['id'])] for c in package['criteria']},
                        'claim': package['claim'], 'unmet_dependencies': []}
    readiness = {}
    def ready(pid, dimension):
        key = (pid, dimension)
        if key in readiness:
            return readiness[key]
        satisfied = packages[pid]['claim'] != 'cancelled' and results[pid]['dimensions'][dimension] == 'passed'
        for dependency in packages[pid]['dependencies']:
            if dependency['affects_dimension'] == dimension:
                satisfied = ready(dependency['package_id'], dependency['dimension']) and satisfied
        readiness[key] = satisfied
        return satisfied
    contradictions = []
    for pid, package in packages.items():
        result = results[pid]
        result['ready_dimensions'] = {dim: ready(pid, dim) for dim in DIMENSIONS}
        for dep in package['dependencies']:
            if not ready(dep['package_id'], dep['dimension']):
                result['unmet_dependencies'].append(dep)
        applicable = [dim for dim in DIMENSIONS if package['dimensions'][dim]['applicable']]
        result['evidence_complete'] = all(ready(pid, dim) for dim in applicable)
        if result['claim'] == 'complete' and not result['evidence_complete']:
            contradictions.append({'package_id': pid, 'reason': 'Completion claim lacks valid criterion or dependency evidence'})
    uncovered = sorted(set(objectives)-{p['objective_id'] for p in packages.values() if p['claim'] != 'cancelled'})
    dimension_gaps = []
    for oid, objective in objectives.items():
        covered = {c['dimension'] for p in packages.values() if p['objective_id'] == oid and p['claim'] != 'cancelled' for c in p['criteria']}
        for dimension in objective['required_dimensions']:
            if dimension not in covered:
                dimension_gaps.append({'objective_id': oid, 'dimension': dimension})
    return {'as_of': now.isoformat(), 'packages': results, 'contradictions': contradictions,
            'uncovered_objectives': uncovered, 'uncovered_dimensions': dimension_gaps, 'evidence_links_verified': False,
            'consistent': not contradictions and not uncovered and not dimension_gaps}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('portfolio', type=Path)
    parser.add_argument('--as-of', help='Timezone-aware ISO timestamp; defaults to now')
    args = parser.parse_args()
    try:
        now = timestamp(args.as_of) if args.as_of else datetime.now(timezone.utc)
        result = reconcile(json.loads(args.portfolio.read_text(encoding='utf-8'), object_pairs_hook=no_duplicate_keys), now)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['consistent'] else 2
    except (ValueError, KeyError, TypeError, OSError, AttributeError) as e:
        print('ERROR: '+str(e), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
