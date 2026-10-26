# 参考工程初步审计（CP-002，开发中）

日期：2026-10-19。本轮只读参考仓库，不修改、不复制业务源码。文件 SHA-256 和依赖清单位置见 reference-inventory.json，可通过 `python scripts/inventory_references.py` 重建（需要 PLAN.md 记录的本地路径）。这份清单不是完整 SBOM，也不代表依赖许可已通过。

| 来源 | 已读取证据 | 当前结论 |
| --- | --- | --- |
| MyCareer | LICENSE、pyproject.toml、api/single_worker.py、storage/api_keys.py、部分原测试 | 根 MIT；Python >=3.11；LangGraph/DeepAgents 等依赖；进程内互斥限定单 worker；现有 API Key 含有效期和 scope，不能将其误述为无鉴权 |
| Viora_AI | 根 LICENSE、依赖清单文件哈希 | 根 MIT；算法迁移和持久化适配仍待对照实验 |
| Jobyn | 根 LICENSE、前端与后端依赖清单哈希 | 根 MIT；组件/API 复用和依赖许可仍待审计 |

MyCareer 当前的 scope、身份、存储接口并不自动等价于 CareerPilot 的 tenant/user 边界。主工程未导入其内部表，也未把静态结论当作 Agent 恢复或部署成功的证据。

运行补充：本轮只读运行原 `tests/storage/test_api_keys.py`，14 passed；仅验证 API Key 模块，不等价于完整 Agent 或应用安装成功。完整依赖未安装，原文件未复制。

后续出口：固定完整源码来源版本；生成依赖 SBOM 与许可证清单；隔离安装原运行时并运行测试；用相同 mock 输入验证工具、记忆、JD、审批与恢复；Viora 规划样例对照；再做整体复用 Go/No-Go。未进行法律结论或第三方数据授权审查，来源适配器保持未接入。
