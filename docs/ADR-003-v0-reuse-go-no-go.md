# V0 底座复用 Go/No-Go（架构 ADR-001 实现记录）

- 日期：2026-10-19，Asia/Shanghai
- 决策记录/技术验证：Codex，依据本项目用户要求完成 V0
- 状态：V0 工程决策已执行；不构成第三方平台或生产法域批准
- 关联：CP-002/004，收敛 ADR-001 的候选复用 Spike

## 决策

**No-Go：V0 不整体嵌入 MyCareer 运行时或复制其存储/业务源码。Go：独立领域接口、SQLAlchemy/Alembic、单主任务执行器，保留 ScopedProfileAdapter 防腐契约。**

这不是“安装失败”的回退：隔离容器已成功安装 MyCareer 的完整 `[test]` 依赖；`career_agent.cli --help` 正常；原 API Key 与单 worker 两组测试共 **35 passed**；真实身份映射/scope/撤销 PoC 成功。复验使用 mycareer-installed.lock 与 scripts/mycareer_poc.py，临时产物用完删除。

拒绝整体接入的工程理由：

1. `api/single_worker.py` 明确以进程锁防止共库多 worker，部分会话互斥在进程内；直接共享运行时会把这一部署约束带入 CareerPilot。
2. 原存储由多个组件直接拥有 SQLite 文件；CareerPilot 当前的 user/tenant 复合外键、统一迁移和 Outbox 事务不能通过简单换表自然成立。原项目已有带 scope/有效期的身份防护，不将其误述为“无鉴权”。
3. 原依赖树引入 LangGraph/DeepAgents/多 SDK 和文档解析；当前 V0 只需要受控任务基础。保持一套本地执行器可以在模拟/故障注入下独立验证恢复与授权，后续是否采用图工作流依评测重新决定。

## 许可证与来源证据

三个参考工程根 LICENSE 为 MIT，哈希在 reference-inventory.json；本轮不复制其业务源码。MyCareer 安装环境 SBOM/许可证清单已保存；其中 certifi MPL-2.0、orjson 复合许可证、tiktoken 及解析器的多方许可证/捆绑通知需要在分发相应模块时保留并复核，不能仅凭根 MIT 宣称所有素材均可任意复用。审计环境继承 CareerPilot 开发基础，清单是安装环境全集，包含主工程依赖和 first-party UNKNOWN 条目，并非声称这些都是 MyCareer 自身依赖。

Viora 的 roadmap.ts 用连续 mastery 阈值与 prerequisite 排序；其结果不能直接授予 CareerPilot 的离散 verified 状态。V0 以自建离散证据/预算回归代替算法代码迁移，后续需要真实对照再决定。Jobyn 的 Supabase/模型/组件栈尚无隔离迁移价值证据，本轮不嵌入。两者不是本轮直接源码依赖，因此没有把全部其依赖许可风险转嫁到主工程或伪造运行通过。

## 回滚与重新评估

未来若希望复用 MyCareer 的具体模块，必须固定来源版本/哈希，列出许可与改动、把对象授权和幂等合同跑到真实适配器，比较与当前执行器的恢复/成本。出现越权、双写、未知投递结果被当成功、或权限扩大时保持关闭；不要并存两套主 Agent 编排。
