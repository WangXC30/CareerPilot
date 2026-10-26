# ADR-024：成长时间线与 GitHub 最小授权绑定

- 日期：2026-10-22
- 关联：CP-113、FR-PRO-005、FR-PRO-006
- 状态：当前实现范围技术验收；2026-10-22 用户完成真实 GitHub 绑定、公开仓库同步及解绑验证，见 PLAN W-20261022-021。

时间线在既有 schema 0004 上合并本人档案/计划版本、目标变更快照、能力来源及撤销记录，使用时间与稳定事件键分页。新目标创建、改名和切换保留内容快照；旧目标只有操作审计时不重建未知标题。计划预览通过同事务 trace 关联触发原因，恢复操作记录目标版本。学习完成、自述、基础测验和项目评测保留各自 scope，不因为 GitHub 活动而升级能力。

GitHub OAuth 使用固定回调、600 秒一次性 state、PKCE S256 和认证账号绑定。请求空 scope，只接受实际返回空 scope 的 token；拒绝继承的 repo/public_repo 等扩大权限并尝试撤销此次 token。固定 GitHub 主机、公开 DNS/IP 固定连接、TLS、8 秒 socket 超时、2 MiB 响应上限、不跟随跳转、不记录提供方响应正文。GitHub 文档说明空 scope 是公开信息只读，且历史授权可能继承权限，故同时校验返回 scope。

浏览器使用弹窗，保持原页面内存中的 CareerPilot 会话。回调清除地址中的 code/state，使用同源 postMessage；接收方同时核对窗口、state、会话和当前页面版本。回调页不存储 token，拒绝授权可取消待完成状态。离开页面或切换账号后须重新开始。

授权状态和 access token 用独立环境密钥 Fernet 加密，放入本人 `github_secret` 聚合，不创建不可变凭据历史，不进入账号导出、时间线、任务或审计正文。应用 Client Secret 只注入 API 服务。公开绑定/最近一次同步使用 `github_binding`，只保存 GitHub 数字 ID、登录名、本人公开仓库名及构造的链接，不保存邮箱、私有仓库、代码、README 或自由正文。同步重新核对账号 ID，允许登录名变化；每分钟最多尝试一次，最多列出 100 个最近更新仓库并说明截断。若提供方返回过期时间则遵守；不使用 refresh token，过期后重新授权。

本阶段仅本人点击同步，无后台轮询、同步队列或自动仓库访问。网络调用与解绑/删除共用 owner 锁，避免撤销后旧响应重新写回；锁覆盖网络会让同账号操作等待，SQLite 写入也可能等待或超时，失败须重试，不声称生产吞吐已验收。解绑删除本地凭据、待完成授权、同步结果及相关历史，仅留无仓库内容的审计；远端 token 撤销失败也完成本地清理，并明确提示用户到 GitHub 设置撤销。账号删除请求立即清理绑定，随后 Worker 清理其余数据。密钥丢失时仍可解绑。

数据库备份可能含加密凭据和历史授权数据，解绑不会重写离线备份。恢复旧备份前须按备份策略清理已撤销绑定，或更换 OAuth 加密密钥使旧凭据失效；密钥更换要求剩余绑定重新授权。未配置 OAuth 时页面明确禁用绑定，时间线可继续使用。该设计不接入其他代码托管/学习平台。

验证入口：`tests/test_v3_profile_history.py`、`scripts/browser_v3_profile_history.py`、`scripts/profile_history_contract.py`，后者接入 PostgreSQL 与 SQLite 恢复合同。提供方为模拟，真实 Chromium 只验证弹窗/同源回调/同步/解绑界面。运行结果仅在 PLAN 中保留简短摘要，测试产物按 AGENTS.md 临时生成并清理。

协议依据：[GitHub OAuth 授权流程](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)、[scope 定义](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)、[OAuth token 撤销 API](https://docs.github.com/en/rest/apps/oauth-applications)。
