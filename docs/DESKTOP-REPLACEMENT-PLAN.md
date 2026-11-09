# CareerPilot 桌面技术栈替换规划

> **已被取代（2026-10-27）**：用户取消 Electron，改为网页端并明确保留 Docker 部署。后续仅按 [网页端重构规范](WEB-REFACTOR-PLAN.md) 与 [ADR-029](ADR-029-web-stack-docker.md) 实施。以下内容保留原草案历史，桌面入口、Windows 打包及去 Compose 要求全部失效，不能作为当前待办。

日期：2026-10-26（Asia/Shanghai）。状态：规划草案，尚未实施或完成新栈验证。已按同日功能收敛审查修订迁移范围。

## 1. 目标与范围

用户明确要求用 Electron + React + TypeScript + Vite、Python + FastAPI、LangGraph（按需 LangChain）、SQLite + 本地文件夹，完整替换目前的主技术栈。

最终只保留一套桌面主入口、一套 React 界面和一套 LangGraph Agent 编排。分阶段迁移用于保证数据和功能连续性，过渡实现须在收尾阶段删除。当前仅本人本地使用，首发建议 Windows x64；其他系统后续分别构建和验证。本轮仅做规划，不改业务代码、启动方式、真实数据或用户配置。

**功能范围以 [功能收敛与资料复用设计](PRODUCT-FOCUS.md) 和 PRD §0.4 为准**：完整替换主技术栈，按收敛后的业务范围迁移；不再逐页复制全部平台实验。保留/合并的能力须逐项验证，延期功能的已有私人数据仍提供读取、导出和删除路径。R0-F1/F2 先落实入口归属、数据映射与资料草稿/复用契约，状态见 TASKS §0。

用户已进一步明确不需要登录注册和运营权限；现有实现已按 [ADR-028](ADR-028-local-personal-workspace.md) 默认直接打开本地个人工作区。新桌面沿用该语义，不迁移账号弹窗、运营后台或角色流程。内部 IPC/服务握手凭据用于识别进程，不要求用户获取或输入令牌。

## 2. 已核对的现状与替换边界

| 当前实现 | 最终处理 |
| --- | --- |
| FastAPI 提供 API，并挂载 static 下的原生 JS 页面 | 保留并整理 API；移除旧页面挂载和原生 JS 页面，React 完整接管界面 |
| 无 Electron、React/Vite 工程 | 建立 Electron main/preload 与 React renderer 工程 |
| Python 领域模块、模型网关、工具权限、预算和来源规则 | 迁入新分层并复用业务逻辑，通过接口封装和回归验证 |
| runtime.py、assistant.py 的自研 Agent 调度/阶段 checkpoint | Agent 内部流程迁移至 LangGraph；删除旧 Agent 分支和阶段恢复机制 |
| Outbox、幂等记录、任务派发与订阅调度 | 保留其业务一致性职责并整理为通用任务基础设施，不再拥有 Agent 内部步骤状态 |
| SQLAlchemy + Alembic，Compose 默认已使用 SQLite | 继续使用 SQLite，不需要做 PostgreSQL 到 SQLite 的默认库转换；迁移实际数据位置及附件引用 |
| Docker Compose 启动 migrate/api/worker，Linux Python 3.14 镜像 | 替换为桌面应用管理的打包 Python 服务；桌面运行不要求系统 Python 或 Compose |
| PostgreSQL 发布候选、HAProxy、多实例部署配置 | 不作为新桌面产品依赖；完成切换后移除旧主运行链，历史设计由 Git 保留 |
| 镜像内 Chromium、中文字体、Tesseract、FFmpeg、PocketSphinx | 优先验证保留的 PDF/中文字体；OCR、视频索引及语音转写延期，不默认打包其运行依赖；旧私人媒体仍须可读取/导出/删除 |
| Docker 隔离代码评测 | 单独设计桌面隔离适配器；普通 Python 子进程不能替代代码沙箱 |

主要源码依据：src/careerpilot/api.py、persistence.py、runtime.py、assistant.py、worker.py、bootstrap.py、backup.py、resume_exports.py、media_extract.py、voice_decode.py，以及 pyproject.toml、Dockerfile、compose.yaml。

## 3. 目标结构与职责

```text
Electron main                         React + TypeScript + Vite
  窗口、菜单、文件对话框                  聊天、侧栏、任务、全部业务页面
  单实例、数据目录、服务生命周期    ←→    preload 暴露的窄接口
  本地凭据、下载/导出                      不直接访问磁盘或持有模型密钥
       │ 受认证的本机 HTTP / 流式事件
       ├── Python API（FastAPI）
       └── Python worker（任务派发、定时调度、LangGraph 执行）
                │
                ├── 领域服务 / 模型网关 / 受控工具
                ├── business.sqlite（业务、任务索引、Outbox、幂等）
                ├── workflows.sqlite（LangGraph checkpoints）
                └── 本地附件目录（由应用授权访问）
```

- Electron 管理 API 和 worker 两个子进程；二者可由同一个打包程序的不同模式启动。耗时任务不占用 Electron 主线程或 FastAPI 请求处理流程。
- preload 按功能暴露调用及事件订阅接口；Electron main 验证参数、调用方和文件访问范围，并代理本机 API/事件流。禁止任意 IPC、路径访问和命令执行入口。
- HTTP 用于查询和命令，SSE/流式 HTTP 用于进度和消息；初期无需同时维护 WebSocket。事件携带 run_id、序号和状态，断线后以持久化状态重新同步。
- 本机服务仅绑定 127.0.0.1，采用操作系统分配端口及每次启动生成的认证凭据；启动握手核对服务身份和版本，避免仅通过端口存活判断就绪。
- 应用级本机凭据用于进程握手，资料归属自动读取本地个人工作区；不需要用户登录或运营角色，保留资料关系校验。模型调用继续通过现有网关实施预算、Schema 和同意校验。
- 保持 contextIsolation、sandbox，关闭 renderer Node 集成，配置 CSP，限制导航和外部链接。应用关闭时停止接单并有界退出；父进程异常退出也要回收子进程。

## 4. LangGraph 的明确边界

LangGraph 负责 Agent 节点、分支、工具调用、人工确认中断和工作流恢复；LangChain 只在模型/工具适配确有收益时使用。无需引入 LangSmith 云服务或托管 Agent Server。

首条迁移链：用户消息 → 读取业务上下文 → 识别意图/规划 → 权限与预算校验 → 调用领域工具 → 必要时中断等待确认 → 汇总真实结果 → 保存运行状态。

- 现有学习计划、技能证据、申请和审核规则仍属于领域服务，不复制进 graph state。
- graph checkpoint 是内部执行状态的唯一恢复依据；业务任务表只保存运行 ID、派发租约和面向界面的状态投影。明确 queued/running/waiting_confirmation/completed/failed/cancelled 的映射与恢复重建流程。
- 初期单个 worker、限制并发，避免两个执行者恢复同一 run；SQLite checkpointer 使用持久化实现。
- 图恢复可能重新进入节点，所有写入和外部动作继续使用稳定幂等键/执行回执；不能把 checkpoint 当作“恰好执行一次”保证。
- 业务库和工作流库不共享原子事务，以 Outbox、幂等写入和启动对账处理“业务提交成功但 checkpoint 未保存”的中断。
- 旧 Agent 在途任务在切换前停止接收并收口；不能映射的任务明确显示中断、等待本人决定是否重建，不能伪造 LangGraph checkpoint 或自动重做外部动作。
- 图版本和状态 Schema 显式记录；升级遇到不兼容 checkpoint 时提供明确处理，不盲目续跑。暂停审批期间不持有数据库事务或执行租约。
- 工作流、聊天和附件纳入账号导出/删除/授权撤销范围；检查点不能成为绕过数据治理的副本。

## 5. 数据、打包及必须先验证的难点

1. 数据保存在 Electron userData 下的应用目录，安装资源与用户数据分开。附件采用受控相对引用，不把卷内绝对路径直接带入桌面环境。
2. 默认数据实际位于 Docker 卷；旧 data/careerpilot.db 不应被假定为最新库。迁移前核对源库及附件位置，停止旧写入，使用 SQLite backup 形成一致副本，校验 Schema/外键/关键关系和附件引用，拒绝覆盖已有桌面库。
3. 迁移先备份再执行 Alembic，失败停止启动并保留恢复入口；不能在新 Schema 上直接运行不兼容旧程序。备份须涵盖一致的业务库、工作流库和附件清单，不能只复制正在写入的 .db 主文件。
4. SQLite 配置外键、合理 busy_timeout、短事务；WAL 作为候选并验证随应用打包的实际 SQLite 版本。多个库不具有跨库原子事务，不放在网络共享或云同步目录中并发使用。
5. 首轮采用 PyInstaller onedir 验证 Python 服务随安装包交付；固定兼容的 Python 与依赖版本，不能直接认为 Linux 镜像的运行环境已在 Windows 验证。显式包含 Alembic 配置/迁移、种子资源、动态导入和时区数据。
6. PDF 优先验证 Electron 的隐藏窗口打印能力，以减少额外 Chromium；保留领域导出契约并验证中文字体、分页和离线行为。若不满足现有要求，评估打包独立渲染器，不能静默降级。
7. OCR、视频索引和语音转写已延期，不作为当前打包前置；历史附件的读取/导出/删除必须覆盖。未来重新纳入时分别验证 Windows 二进制、模型及许可证，原质量边界保持。
8. 代码评测是保留范围内的单独难点：优先评估 Windows 受限执行环境/隔离 VM 的资源限制、断网、只读输入和进程树回收。是否可完全免 Docker，必须通过实际隔离验证。若未通过，此项明确阻塞当前承诺范围的完整验收；不能用宿主机直接执行用户代码填补。
9. 桌面退出后任务和提醒默认暂停，重开后按业务规则处理到期事项；如果要求关窗继续运行，应明确托盘驻留行为。开机自启或系统服务不是本轮默认假设。
10. 本地存储不等于模型离线运行；外部模型与已授权外部来源仍需要网络。无网时正常显示可用本地数据及真实失败状态。

## 6. 实施阶段与完成条件

| 阶段 | 实际工作 | 完成条件 |
| --- | --- | --- |
| R0 替换基线与打包验证 | 按功能收敛设计映射页面/API/数据；完成 R0-F1/F2；确定资料契约与数据目录；验证 Windows Python 打包、PDF 及沙箱 | 明确保留/合并/按需/延期及旧资料处理；安装/冷启动实验可运行，不能用开发机依赖冒充交付 |
| R1 桌面运行底座 | Electron main/preload、React/Vite、Python API/worker 启停、健康握手、迁移入口 | 无 Python/Node/Compose 开发环境的目标 Windows 环境可启动并关闭；端口冲突、启动失败和子进程退出有真实反馈 |
| R2 React 界面完整重建 | 按 5 个主入口及辅助设置重建布局/路由/API；实施资料分步填写、草稿、去重与来源复用，覆盖 R2-F1～F3 | 保留/合并能力覆盖，中英、键盘、来源/确认、旧响应隔离保持；无平行事实表单，延期旧入口有明确去向 |
| R3 LangGraph 接管 | 迁移主助手及其他 Agent 工作流；接工具、网关、审批、checkpointer、取消和恢复 | 持久化状态真实；失败/中断/重复恢复不重复产生业务副作用；旧 Agent 执行链退出 |
| R4 本地能力与真实数据迁移 | 附件、PDF/Word 导出、隔离评测；迁移工具与备份恢复；覆盖延期能力的旧私人记录 | 原库保留，迁后关系/附件可读、可导出/删除；当前承诺范围功能等价；沙箱隔离未验证则不通过完整迁移 |
| R5 完整切换与旧栈退出 | 安装、升级/恢复、全链回归及 R5-F1；改 README/架构/任务与检查入口；删除过渡代码及旧主运行配置 | 默认仅新桌面入口；无旧原生 JS 界面、自研 Agent 编排或 Compose 普通运行依赖；延期旧私人资料可管理，旧版本可通过 Git/备份恢复 |

现有 595 passed 等历史结果只作为旧实现基线。新桌面端必须分别验证原领域规则、API 合同、LangGraph 状态恢复、Electron IPC/生命周期、安装及业务闭环，不能继承旧“已验收”标签。暂不承诺工期；R0 后按页面、工作流和平台依赖清单估算。

建议目录：apps/desktop（main/preload）、apps/renderer（React）、src/careerpilot（Python 业务及 API）、src/careerpilot/workflows（LangGraph）、packaging（安装/服务打包）、migrations、tests。先建立边界再按需搬移，避免同时改动所有 Python 导入。

迁移任务采用 R0～R5 独立状态；原 CP 开发任务及历史验收范围保持历史记录。本轮没有开展迁移、重跑测试或真实模型调用，不生成测试证据包。

## 7. 官方资料与设计依据

- Electron 权限隔离和 IPC：https://www.electronjs.org/docs/latest/tutorial/security
- LangGraph 可独立于 LangChain 使用：https://docs.langchain.com/oss/python/langgraph/overview
- LangGraph 持久化边界：https://docs.langchain.com/oss/python/langgraph/persistence
- 本地 SQLite checkpointer：https://reference.langchain.com/python/langgraph.checkpoint.sqlite
- SQLite WAL 并发与限制：https://www.sqlite.org/wal.html
- PyInstaller 自包含交付与平台构建限制：https://pyinstaller.org/en/stable/operating-mode.html

本文中的进程划分、目录、代理方式和迁移顺序为针对本项目的设计建议，尚不是已验证的实现结果。
