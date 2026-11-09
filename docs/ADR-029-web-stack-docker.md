# ADR-029：网页端技术栈替换，保留 Docker Compose 部署

日期：2026-10-27（Asia/Shanghai）。状态：已采纳，W-056 已在原项目实施；验证范围见 PLAN。

## 背景

用户要求完整替换当前主技术栈，随后明确“网页端即可”“先讨论”，本轮授权先写文档、后续依文档实施，并补充“保留 Docker 部署”。原 Electron 桌面草案不再是实施依据。

## 决策

- 使用 React + TypeScript + Vite 重建网页界面，Python + FastAPI 承载业务服务，LangGraph 接管 Agent 编排，LangChain 按需使用，SQLAlchemy/Alembic + SQLite 和本地文件保留领域持久化。
- 保留 Docker Compose 作为默认部署：Node 阶段构建网页，FastAPI 镜像提供构建资源和 API，独立 Worker 执行图与定时任务；一次性迁移、检查及独立隔离代码评测继续使用容器。
- 保留现有业务卷与资料关系，不因换栈重建真实库。默认宿主仅回环发布，浏览器免登录进入个人工作区，沿用 ADR-028；不引入公网多用户或运营权限。
- 不开发 Electron、桌面 IPC、系统托盘或 PyInstaller 安装包。关闭浏览器不等于关闭后台服务，不默认承诺浏览器关闭时系统推送。
- 最终只保留一套 React 产品界面和一套 LangGraph Agent 内部编排；领域规则、幂等、模型网关与来源治理仍由 Python 代码控制。

## 影响与执行边界

执行依据为 [WEB-REFACTOR-PLAN](WEB-REFACTOR-PLAN.md)、[TASKS §0](../TASKS.md) 和 [PLAN](../PLAN.md)。原桌面草案标为已取代，历史工作日志不改写。W-055 只完成规划；W-056 按用户“直接替换”实施，当前只有 React 产品界面和 LangGraph 助手执行器，见 [实现契约](WEB-CONTRACTS.md)。旧验收不转记为新栈验收。

浏览器无法直接管理服务器文件，上传/下载须经受控后端接口；SQLite/checkpoints/附件保存在 Docker 持久化卷。本地模型调用不等于完全离线，外部处理仍须本人同意并受预算限制。恢复、跨库一致性、同源开发代理与隔离评测依新规范验证，不通过删除业务边界简化换栈。
