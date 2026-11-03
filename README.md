# 战略任务拆解与多项目交付对账

**Strategic Delivery Reconciliation · `strategic-delivery-reconciliation`**

从战略记录到交付包、依赖、并行执行、集成和证据对账。把“代码完成”“已上线”“业务验收”分别报告，避免任务清单全绿但真正目标没有完成。

## 使用

将 [`strategic-delivery-reconciliation/`](strategic-delivery-reconciliation/) 完整放入支持 Agent Skills 的工具的技能目录。例如 Codex 通常使用 `~/.codex/skills/`。

> 使用 $strategic-delivery-reconciliation，把这份战略会议记录拆成可验收的交付包，明确依赖和并行边界，再对照现有成果列出真实缺口。

## 内容

- [技能入口](strategic-delivery-reconciliation/SKILL.md)：目标还原、拆包、协作与结项。
- [台账与证据](strategic-delivery-reconciliation/references/ledger-and-evidence.md)：状态与 JSON 字段约定。
- [并行与变更](strategic-delivery-reconciliation/references/parallel-and-change.md)：交接、集成和范围控制。
- [虚构台账](strategic-delivery-reconciliation/assets/portfolio.example.json) 与 [离线检查器](strategic-delivery-reconciliation/scripts/reconcile_portfolio.py)。

```bash
python3 strategic-delivery-reconciliation/scripts/reconcile_portfolio.py strategic-delivery-reconciliation/assets/portfolio.example.json --as-of 2026-01-15T12:00:00Z
python3 -m unittest discover -s strategic-delivery-reconciliation/scripts -p 'test_*.py'
```

Python 3.10+，仅标准库。脚本不会联网、修改项目状态或核验链接的真实性；检查结构、循环依赖、时间与完成声明。所有示例均为虚构，不包含客户或内部治理记录。
