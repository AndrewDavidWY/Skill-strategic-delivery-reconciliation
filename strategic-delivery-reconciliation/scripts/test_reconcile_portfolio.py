import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from reconcile_portfolio import reconcile, timestamp


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((Path(__file__).parents[1]/'assets/portfolio.example.json').read_text())
        self.now = timestamp('2026-01-15T12:00:00Z')

    def test_code_live_business_are_separate(self):
        r = reconcile(self.data, self.now)
        self.assertTrue(r['consistent'])
        self.assertEqual(r['packages']['PKG-02']['dimensions']['live'], 'passed')
        self.assertEqual(r['packages']['PKG-02']['dimensions']['business'], 'pending')

    def test_overclaim_is_detected(self):
        self.data['packages'][1]['claim'] = 'complete'
        self.assertFalse(reconcile(self.data, self.now)['consistent'])

    def test_cycle_is_rejected(self):
        self.data['packages'][0]['dependencies'] = [{'package_id':'PKG-02','dimension':'live','affects_dimension':'code'}]
        with self.assertRaises(ValueError):
            reconcile(self.data, self.now)

    def test_future_evidence_is_rejected(self):
        self.data['evidence'][0]['observed_at'] = '2027-01-01T00:00:00Z'
        with self.assertRaises(ValueError):
            reconcile(self.data, self.now)

    def test_stale_evidence_is_not_passed(self):
        r = reconcile(self.data, timestamp('2026-01-17T00:00:00Z'))
        self.assertEqual(r['packages']['PKG-02']['dimensions']['live'], 'stale')

    def test_latest_failure_overrides_pass(self):
        later = copy.deepcopy(self.data['evidence'][0])
        later.update(status='failed', observed_at='2026-01-15T11:00:00Z')
        self.data['evidence'].append(later)
        r = reconcile(self.data, self.now)
        self.assertEqual(r['packages']['PKG-01']['dimensions']['code'], 'failed')
        self.assertTrue(r['packages']['PKG-02']['unmet_dependencies'])

    def test_conflicting_simultaneous_evidence(self):
        later = copy.deepcopy(self.data['evidence'][0])
        later['status'] = 'failed'
        self.data['evidence'].append(later)
        self.assertEqual(reconcile(self.data,self.now)['packages']['PKG-01']['dimensions']['code'], 'conflict')

    def test_missing_business_scope_detected(self):
        p = self.data['packages'][1]
        p['criteria'] = [c for c in p['criteria'] if c['dimension'] != 'business']
        p['dimensions']['business'] = {'applicable': False, 'reason': 'removed for test'}
        self.assertFalse(reconcile(self.data,self.now)['consistent'])

    def test_wrong_version_or_environment_does_not_pass(self):
        self.data['evidence'][1]['version'] = 'old'
        self.data['evidence'][1]['environment'] = 'preview'
        self.assertEqual(reconcile(self.data,self.now)['packages']['PKG-02']['dimensions']['live'], 'mismatch')

    def test_nonfinite_freshness_rejected(self):
        for value in (float('nan'), float('inf'), json.loads('1e999')):
            self.data['packages'][1]['criteria'][0]['freshness_hours'] = value
            with self.assertRaises(ValueError):
                reconcile(self.data,self.now)

    def test_missing_live_freshness_policy_rejected(self):
        del self.data['packages'][1]['criteria'][0]['freshness_hours']
        with self.assertRaises(ValueError):
            reconcile(self.data,self.now)

    def test_dependency_readiness_propagates_transitively(self):
        p = copy.deepcopy(self.data['packages'][0])
        p['id'] = 'PKG-03'
        p['dependencies'] = [{'package_id':'PKG-01','dimension':'code','affects_dimension':'code'}]
        self.data['packages'].append(p)
        e = copy.deepcopy(self.data['evidence'][0])
        e['package_id'] = 'PKG-03'
        self.data['evidence'].append(e)
        self.data['packages'][1]['dependencies'][0]['package_id'] = 'PKG-03'
        self.data['evidence'][0]['status'] = 'failed'
        r = reconcile(self.data,self.now)
        self.assertFalse(r['packages']['PKG-03']['ready_dimensions']['code'])
        self.assertTrue(r['packages']['PKG-02']['unmet_dependencies'])

    def test_cancelled_upstream_does_not_satisfy_dependency(self):
        self.data['packages'][0]['claim'] = 'cancelled'
        self.assertTrue(reconcile(self.data,self.now)['packages']['PKG-02']['unmet_dependencies'])

    def test_cli_rejects_duplicate_claim_without_changing_input(self):
        raw = json.dumps(self.data).replace('"claim": "in_progress"', '"claim": "complete", "claim": "in_progress"')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'portfolio.json'
            path.write_text(raw)
            before = path.read_bytes()
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name('reconcile_portfolio.py')), str(path), '--as-of', self.now.isoformat()],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
            self.assertIn('Duplicate JSON key: claim', result.stderr)
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
