# CareerPilot

按 PRD / ARCHITECTURE / TASKS 推进的职业成长项目。**用户确认仅本人本地使用，开发 78/78、V4 20/20、质量包 6/6 已完成各自本地范围技术验收**。当前进展见 [PLAN](PLAN.md)，本地治理边界见 [ADR-027](docs/ADR-027-personal-local-governance.md)。正式生产部署、未验证的外部渠道与正式法律签核仍未验收。

已实现 API/页面原型、显式迁移、用户/租户隔离、角色策略、模型结构化网关与配额、可撤销同意、Outbox/独立 Worker、状态机、来源接入闸和运营元数据。开发、测试与浏览器验证统一由 **Docker Compose** 管理，宿主机无需 Python 或 `.venv`。

## 启动开发环境

前置：Docker Desktop 已启动并使用 Linux containers，支持 `docker compose`。在项目根目录运行：

```powershell
docker compose up -d --build --wait api worker
docker compose exec api python -m careerpilot.cli --name developer
```

打开 [成长空间](http://127.0.0.1:8000/app/)，点击“连接我的账号”使用邮箱/密码注册或登录。邮箱当前作为账号标识，不发送验证或找回邮件。CLI 令牌登录仍可用于本地开发和交互 API 文档；令牌仅存内存、24 小时有效，库内只存摘要。需要运营角色时，用 `python -m careerpilot.cli --name operator --role operator` 创建本地身份；运营角色不能查看他人的私有资料。

首次启动先执行 `migrate`（迁移至 0005 并初始化非敏感来源及词库），成功后启动 `api/worker` 并等待健康检查。宿主机只发布 `127.0.0.1:8000`。端口冲突时，复制 `.env.example` 为 `.env`，修改 `CAREERPILOT_PORT` 后重新启动。Compose 自动读取 `.env`；容器数据库地址由 compose.yaml 管理。

源码以只读挂载进入容器，修改 `src/` 下 Python 代码会触发 API 热重载；worker 代码变更后执行 `docker compose restart worker`。更新依赖或 Dockerfile 后重新执行上述 `up --build`。模型变更须编写 Alembic 迁移；已有环境升级时先 `docker compose stop api worker`，再 `docker compose run --rm migrate`，最后 `docker compose up -d --wait api worker`。

```powershell
docker compose ps -a
docker compose logs -f api
docker compose down
```

`down` 保留数据库卷，下次启动数据仍在。**`down -v` 会删除开发数据库，不能当作普通停止命令。** 原宿主机 `.venv` 与 `data/` 保留，不再作为默认运行环境。

## 迁移原本地数据（仅首次）

若需要保留旧 `data/careerpilot.db`，在首次 `up` **之前**停掉旧本地 API，并运行：

```powershell
docker compose build
docker compose run --rm --no-deps migrate python scripts/import_dev_database.py
docker compose up -d --wait api worker
```

脚本使用 SQLite backup 导入到 Compose 卷，原文件保留；目标数据库已经存在时拒绝覆盖。导入后 Alembic 自动升级。若已启动过新环境，先备份并确认两份数据库的用途，再安排人工合并，不要删除已有卷来绕过保护。

## 服务与存储

| 服务 | 用途 | 数据 |
| --- | --- | --- |
| `migrate` | 一次性迁移 + 来源种子初始化 | 共享开发数据库卷 |
| `api` | FastAPI + Uvicorn 热重载，健康检查 | `careerpilot-data` 卷中的 `/var/lib/careerpilot/careerpilot.db` |
| `worker` | 租约、checkpoint、重试/死信、Outbox 幂等消费 | 同一开发卷，独立进程与 heartbeat |
| `checks` | lint/typecheck、凭据扫描、需求追踪、pytest、评测、HTTP/浏览器、恢复演练 | 临时数据库，不挂载开发卷；按需执行 |

镜像使用 Python 3.14 slim、requirements.lock、Chromium 和非 root 用户。构建上下文排除 `.env/.secrets`、本地数据、`.venv` 和 Git。PostgreSQL 兼容检查由 [临时 Compose 配置](infra/compose.postgres.yaml) 提供；默认开发库仍为 SQLite。成长工作台、职业探索、岗位市场、能力地图、学习中心、求职中心和设置已调用真实业务；项目验收按后续阶段推进。

建议顺序：设置与隐私建档/导入材料→职业探索选主目标与里程碑→岗位市场导入 JD/筛选保存→能力地图查看差距→学习中心生成并发布预算计划→记录学习成果→能力地图提交基础测验→学习中心查看并确认重排。成长工作台的助手通过独立 Worker 读取实际进展、生成草案或预览。

材料接受 UTF-8 txt/md/json，单文件不超过 1MiB。JD 使用保守规则，缺失/歧义字段为未知；确认材料和完成课程不会授予 verified。客观测验只验证对应基础知识，证据有版本/范围/有效期。当前词库覆盖软件基础方向，网页工具检索登记的官方来源索引，读取网页需单独同意。

求职中心：导入文本简历或确认事实→保存简历→选择历史版本查看/导出/生成材料→建立申请→确认准备清单→本人前往原始来源→补录回执。无回执保持待核实；回执仅本人确认，未经源站独立验证。申请锁定旧版本，来源改版/删除需重新确认；历史快照保留至账号注销。提醒只走授权的站内渠道，可暂停/取消。PDF 运行需要镜像内的 Chromium 与中文字体；Word/PDF 原件导入尚未提供。

文字面试：在求职中心选择岗位与简历版本，核对项目陈述后开始练习；完成后查看评价、修订复盘，预览并接受后才加入学习计划。基础选择题有客观答案，开放题正确性待人工核对；结构/长度是辅助指标。练习不改变已验证技能或形成经历事实；无受信项目验收回执时项目分项仍未知，真实验收生产端尚未接入。

资料与来源：按岗位、知识点、类型与平台检索本地登记入口，或主动导入有权处理的规范链接/摘录。本人标签和观点均待核实；可对照引用中的差异，删除本人记录，最长保留 30 天。GitHub 公开元数据读取需明确授权并受全局限频；小红书/抖音未配置授权搜索，仅显示本人导入，不代表已直接搜索。更多限制见 [来源范围登记](docs/source-review-v2.md)。

复盘与通知：记录本人实际投入分钟，按日期查看周月报、证据/计划偏差和同队列申请漏斗；计划分钟不推定实际学习。通知默认暂停，可确认站内类型、时区、静默、上限及暂停/取消，邮件未配置不发送。连续复盘由独立 Worker 逐步执行，失败可恢复，计划预览仍需另行接受。运营页面支持公共变更的独立审核、管理员发布和回滚；工单仅同租户元数据处理，私人原文不可见。

V4 本地实验：方向与练习、协作与分享、本地工具三个页面提供版本化视图、路线比较、定向空间与逐字段分享、本人授权媒体索引、短期录音、手动转写、ICS 导出、Offer 区间比较和断网申请演练。媒体 PDF/图片/视频分别提取文字、OCR、首帧 OCR；不会自动转写音频。申请演练只调用本地模拟器，不向雇主投递。所有公开内容须本人逐项确认并可撤销。日历时区可直接输入有效 IANA 标识；常用项仅作建议，新建使用档案时区，编辑保留事件自身时区及别名，不因界面语言改变。正式外部平台接入、生产多租户部署和合规审核仍需独立完成。

本地工具中的日历可逐项选择里程碑、导入岗位截止日期和本人填写的面试事件，支持全天或带时区的时间，修改来源后须重新授权导出。可移除单项或撤销整份导出；其他日历软件已导入的副本需在那里删除。季度复盘保留当时报表、目标和计划版本及本人反思，历史可查看；建议不会自动修改目标，到职业探索预览并确认后才切换。顾问进度评论通过「协作与分享」单独授权并可撤销。

方向与练习中的历史匹配使用冻结的岗位、词库、本人证据、日期和权重重放，词库改版或权重回滚不会覆盖旧快照。当前匹配读取已发布的字面技能别名；公开字段差异可在运营台核对，必须由另一位运营审核、管理员发布，权重支持灰度，回滚也须审核。规则门禁和合成回归不代表真实市场准确率。

同一职业可比较基础学习与追加小练习的工时、资源、假设和风险，金额未知时保留未知。阶段练习的错题可开始私人教学会话，按概念、前提和反例逐层提示，刷新后可续接并可删除；每会话最多 12 轮，与分层教学共享每日 60 次配额。自由文本不自动判分或升级能力。完成练习并保存申请后，可显式选择已加入本人空间的同伴，授权其核对本次练习快照；授权可撤销，成员变化或七天到期后失效。同伴的人工意见保留依据与局限，不修改成绩或作出处罚。

方向视图支持编辑与回滚，原始事实保持共享来源。趋势订阅按本人选择的间隔核对本地岗位，有变化才在通知中心生成摘要，遵守成长通知的时区、静默与上限。协作页可选择作品资料、项目陈述或计划的字段，先预览再确认；退出空间撤销本人在此空间的旧分享，重新加入须重新分享。录音可回放、填写本人核对转写、单独清除音频；离开页面、取消录制或撤销同意会停止麦克风并丢弃未保存片段。具体边界见 [ADR-025](docs/ADR-025-v4-local-experiments.md)。

申请演练先核对岗位/简历版本与材料摘要，再进行五分钟内有效的一次性审批。每个申请版本只执行一次，超时或中断保留未知结果，可在本人历史中记录核对说明；不会因此将真实申请标记为已投递。Offer 比较可自定权重，保留未知评分范围，并记录本人复核后的选择，向雇主接受 Offer 仍由本人另行操作。

## 验证与日常命令

本地和 CI 共用同一容器检查入口：

```powershell
docker compose run --build --rm --no-deps checks
```

也可以单独执行：

```powershell
docker compose run --rm --no-deps checks python -m pytest -q -p no:cacheprovider
docker compose run --rm --no-deps checks python scripts/smoke_http.py
docker compose exec api python -m alembic current
```

测试报告、截图、日志和导出校验样本只作临时使用，用完删除。默认检查只输出终端摘要；旧 `--report-dir` / `--output` 参数保留为兼容标签，实际输出改到系统临时目录，成功或异常结束均自动清理，不写入指定的持久目录。需要查看文件或串联导出与渲染时，在同一个 `temporary_artifacts` 临时上下文中完成，再退出清理。

实际读屏抽测使用独立的可选镜像，在私有 X11/AT-SPI 会话内运行真实 Orca 和有界面的 Chromium。先构建上述开发镜像，再运行：

```powershell
docker build -f infra/Dockerfile.screen-reader -t careerpilot-screen-reader:local .
docker run --rm --init --network none -v "${PWD}:/workspace:ro" careerpilot-screen-reader:local
```

该检查验证中英登录弹窗标题、密码/必填字段、真实 Tab、下拉框、复选框状态、授权通知和档案预览/取消焦点。断言来自 Orca 的实际朗读输出，并核验 Speech Dispatcher/eSpeak 合成的非静音音频；不替换读屏引擎或用无障碍树代替朗读。数据库、读屏配置、日志和音频均在系统临时上下文内生成并清理，容器无业务数据卷及外网连接。此项仅覆盖 Linux Chromium/Orca 的基本支持，不代表 NVDA/JAWS、真人听感或完整 WCAG 认证；不把桌面及语音依赖加入应用镜像。Orca 的调试输出说明见 [官方文档](https://orca.gnome.org/debugging)。

需求追踪直接校验 PRD、TASKS 和 PLAN 的映射，无需留存 JSON：

```powershell
docker compose run --rm --no-deps checks python scripts/check_traceability.py --check
```

CI 继续执行依赖扫描、PostgreSQL 合同和独立 Worker 冒烟，报告不挂载到宿主目录。历史 V0/V1 哈希工具仅供旧快照复核；当前版本使用统一 `checks`。必要业务证据、真实数据库和业务备份按业务规则保留。

登录后在“职业探索”选择明确兴趣，比较基础补课成本、有效技能可迁移性与本人导入样本；样本数量不代表市场供需。在“岗位市场”保存可实习月数、资格及城市硬约束，再查看“明确符合 / 待核实 / 不符合”三组结果。缺失、歧义条件保持待核实；分数不是录用概率。项目验收尚未接入时，该分项保持未知；不从自述或材料推断通过。每次打开重新计算，另存的解释快照保留当时版本，可由账号导出或随注销清理。

同页可收藏岗位、选择 2—4 项对比、编辑企业屏蔽和关键词排除，并切换/关闭推荐模式。反馈有撤销与历史；“已投递”只影响推荐，不能代替回执。订阅仅检查我的本地岗位库，可按 IANA 时区设置日/周频率和静默时段、暂停/取消/恢复；点击“立即检查岗位库”由 Worker 执行，有新增或实质变化才产生站内摘要。需确保 API 与 Worker 都运行，修改本批 Worker 代码后重启 worker。

## V3 项目练习

在“作品集”按已导入岗位选择 Python 数据汇总练习，保存代码后等待隔离评测；通过后预览并确认成果陈述，可在求职中心选择使用。默认私密，撤回会使相关证据和简历来源失效。有限测试只验证该练习的行为，不证明原创性、业务收益或完整岗位能力。

同页可记录设计/复盘阶段、上传 64 KiB 内纯文本成果、保存 GitHub commit/PR 引用、下载或撤回成果；GitHub 引用只保存本人提供的链接，不发起仓库访问。实现阶段只依据当前代码测试回执。项目任务先预览预算、日期与既有任务变化，确认后才加入学习计划；拒绝不改变计划。项目打卡不升级能力。

填写实际背景、任务与个人行动，未知结果留空，再预览 STAR 描述。本人确认陈述且当前代码通过后，可另行确认保存简历事实。陈述改版需重新确认，撤回或成果失效会阻止旧材料继续使用。阶段复核单独保留本人结论与历史，不冒充导师验收。

首次构建专用镜像，然后由本地操作者在项目根目录运行评测器（宿主 Python 仅用标准库）：

```powershell
docker build -f infra/Dockerfile.sandbox -t careerpilot-sandbox:local .
python scripts/project_sandbox.py --max-jobs 3
```

API 和 Worker 保持运行；评测器每次处理最多指定数量后退出，没有自动轮询服务。无镜像或 Docker 不可用时不伪报通过；基础设施错误可通过新提交重评。业务容器不需要 Docker socket。宿主复验命令 `python scripts/project_drill.py` 使用临时数据库、合成账号及真实 Chromium/隔离容器，不改动开发业务库；仅写入临时目录，用完删除。见 历史验证摘要（产物已清理）、[ADR-021](docs/ADR-021-v3-project-workflow.md) 及 [首批执行边界](docs/ADR-020-v3-project-sandbox.md)。GitHub 账户绑定见下文 V3 配置说明。

## V3 学习节奏与复习

在「学习中心」已发布计划后，选择冲刺/均衡/科研繁忙/低负担模式，先查看预算与排程差异，再接受调整。模式使用档案预算的 100%/75%/50%/25%；冲突不会覆盖已完成或锁定工作。间隔复习展示历史测验的到期日期、错题和重新测验入口。

在「复盘与通知 → 配置成长通知」可启用或关闭「间隔复习提醒」，默认关闭，只走站内渠道。学习中心底部可收藏、评价和举报坏链；反馈仅影响本人推荐，取消举报可恢复。没有可用外部材料时会明确提供本地练习入口。见 历史验证摘要（产物已清理） 与 [ADR-023](docs/ADR-023-v3-study.md)。

## V3 时间线与 GitHub 绑定

「设置」新增成长与能力时间线，以及 GitHub 账户绑定。时间线可分页查看档案、目标、历史计划、能力来源和撤销原因；GitHub 仅同步本人公开仓库名称和链接，不读取私有仓库或代码，不提升技能验证状态。同步必须本人点击，后台不会轮询。

GitHub 真实接入需在本机完成以下配置（不要把 Secret 发到聊天或提交到仓库）：

1. 在 GitHub Developer settings 创建自己的 OAuth App。开发环境 Homepage URL 填 `http://127.0.0.1:8000/app/`，Authorization callback URL 填 `http://127.0.0.1:8000/app/github-callback.html`。端口须与实际配置一致；浏览器也使用该主机和端口，不混用 localhost。
2. 从 `.env.example` 创建未跟踪的 `.env`，填写 `CAREERPILOT_GITHUB_CLIENT_ID`、`CAREERPILOT_GITHUB_CLIENT_SECRET`、`CAREERPILOT_GITHUB_CALLBACK_URL`。将 `cryptography.fernet.Fernet.generate_key()` 生成的独立稳定密钥填入 `CAREERPILOT_OAUTH_ENCRYPTION_KEY`，妥善保存在本机密钥管理中。不要使用会话 token 或开发助手连接器凭据。
3. 执行 `docker compose up -d --no-deps api` 重新加载环境配置；登录后在「设置」点击同意公开只读范围并绑定，允许弹窗，完成 GitHub 授权后再点击同步。

申请空 scope，若 GitHub 继承了本应用先前的更大权限会拒绝保存，并提示先撤销旧授权。每分钟最多同步一次，最多列出最近更新的 100 个公开仓库。解绑清除本地凭据与同步记录，远端撤销未确认时可通过页面链接进入 GitHub 设置撤销。账号注销会立即清理本地绑定；离线备份需按保留策略清理，恢复旧备份前须处理已撤销绑定或更换加密密钥。自动化使用模拟提供方；2026-10-22 用户另行完成真实 GitHub 绑定、同步和解绑验证。设计见 [ADR-024](docs/ADR-024-v3-profile-history.md)。

## API 验证切片

`POST /api/v1/career-goals` 传 `Idempotency-Key: demo-goal-1` 和 `{"title":"后端工程师"}`；相同键及内容返回同一创建结果，不同内容返回 409。`PATCH /api/v1/career-goals/{id}` 传 `If-Match: "1"`（含双引号），成功后版本加一，旧版本返回 412。列表下一页 cursor 使用上一页最后一项 id。

`/health/live` 检查进程，`/health/ready` 检查迁移。私有 API 要求 Bearer 身份。注册/登录见 `/api/v1/auth/register` 和 `/login`；创建档案用 `If-Match: "0"`，更新传当前版本。目标切换、计划发布、预览接受和注销均需显式确认；旧版本返回 412。完整接口见交互 API 文档。付费 provider、公网发布、代码沙箱与外部投递不在本地 V1 出口中启用。

## 备份与恢复

使用 SQLite 一致快照，不覆盖已有备份：

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
docker compose run --rm --no-deps -v "${PWD}/backups:/output" migrate python scripts/backup_restore.py /var/lib/careerpilot/careerpilot.db /output/snapshot-001.db
```

恢复须先停止 API/worker，再向新路径恢复并检查完整性、认证及数据，最后切换；保留 UID/GID 10001 写权限。本地合成数据恢复与 PostgreSQL 结构合同已验证。正式 RPO/RTO 提案与生产治理见 [运维手册](docs/V0-RUNBOOK.md)。

## 文档

V3 多岗位图谱：登录后进入「能力地图」，在「多岗位技能图谱」选择 3–20 个已导入且未关闭/合并的 JD，点击「生成技能图谱」。展开节点查看必需/优先要求原文、完整先修顺序、学习材料和基础测验入口。更换选择后重新生成；当前只覆盖已收录技能，不把未识别要求当成不存在。

- [V3 图谱设计](docs/ADR-022-v3-skill-graph.md)

- [实际进展与下一步](PLAN.md)
- [V1 本地出口](docs/V1-EXIT.md)
- [V1 完整闭环决策](docs/ADR-013-v1-growth-release.md)
- [V1 首批架构决策](docs/ADR-012-v1-growth-slice.md)
- [V0 首批架构决策](docs/ADR-001-v0-foundation.md)
- [Compose 开发环境决策](docs/ADR-002-compose-development.md)
- [复用审计](docs/reference-audit.md)
- [V0 完整出口与限制](docs/V0-EXIT.md)
- [安全与来源策略](docs/V0-SECURITY.md)
- [复用 Go/No-Go](docs/ADR-003-v0-reuse-go-no-go.md)

`domain.py` 定义纯领域契约，`application.py` 承载事务用例，`persistence.py` 管理 ORM，`api.py` 提供 HTTP 适配。

V4 本地工具现支持授权索引检索/更新订阅、离线英文转写草稿、笔试与算法练习追踪，以及逐项授权的双语主页草稿。英文识别质量有限，须本人核对。界面中英切换已覆盖导航、登录、真实成长工作台与助手、档案确认与历史、材料与记忆、职业目录/目标/里程碑/路径比较、JD 导入核验、岗位匹配/推荐反馈/收藏对比/偏好/订阅与摘要、协作/公开分享/来源导入与观点对照/复盘统计与通知、GitHub 绑定/登录会话/成长时间线、私人项目建议/代码提交/评测/阶段成果/STAR/计划预览、简历事实/版本/导出与材料草稿、申请/回执/提醒、文字面试/评价/复盘、技能/差距/图谱/测验、学习计划/任务/重排、学习节奏/错题复习/资源反馈及基础隐私操作。助手支持中英文规则意图，也可明确选择操作；仍只调用受控本地成长工具。题目、课程内容、历史计划、用户输入与引用原文不翻译。V4 方向/趋势/错题教学/阶段练习/翻译主页、媒体/录音/日历、申请演练/Offer/季度复盘、练习追踪/实验和运营审核发布表单也已接入完整中英文界面。业务原文与冻结内容仍不翻译；表单名称、结果焦点、本人时区、连续选择与旧响应保护已补齐。W-041 已完成隔离 Linux Chromium + Orca/AT-SPI 中英实际朗读与合成音频抽测；Windows NVDA/JAWS、真人听感及完整 WCAG 认证未执行。真实代表性模型收益、正式生产环境和正式法律签核仍为独立门槛。当前技术范围与未完成项以 [PLAN.md](PLAN.md) 和 [ADR-025](docs/ADR-025-v4-local-experiments.md) 为准。


CP-222 另提供真实模型适配器可接入的本地对照执行入口：`python scripts/compare_agent_models.py --dataset <授权数据集.json> --primary <模块:工厂> --reviewer <模块:工厂> --primary-price-micros <每token估算微费用> --reviewer-price-micros <每token估算微费用> --budget-micros <本次上限> --repeats 3`。工厂返回 [Provider 协议](src/careerpilot/gateway.py) 对象；不自动下载模型或启用模型调用。外部适配器默认拒绝，只有在已获实际调用授权后，操作者才可显式传 `--allow-external`。

数据集字段为 `version`、`source`（`synthetic` / `owner_authorized`）、`rights_confirmed: true`，以及 `examples` 数组；每项含唯一 `id`、分组 `group`、授权原文 `text`、严格布尔标签 `expected`。此入口仅评测二值招聘风险标注：同一主模型+固定词项工具，与同主模型再经复核模型核对，不能代表职业/学习/求职/面试专家已验收。输出含版本、重复质量差、失败、延迟样本与估算用量；不打印原文/模型正文/敏感推理，不生成报告文件。用量数据库及预测步骤仅在系统临时目录/内存使用并清理。退出码 0 代表本次执行及冻结重算完整，3 代表预算/调用/授权等原因导致不完整，2 代表配置或输入错误；任何退出码都不会启用多模型协作。

DeepSeek 适配器工厂为 `careerpilot.deepseek_provider:from_env`。仅在明确选择此工厂时，读取进程中的 `DEEPSEEK_API_KEY`，或项目 `.env` 中同名的单项凭据；不导出其他配置、不回显密钥。它固定访问官方 HTTPS 地址，使用 `deepseek-flash`、非思考模式、temperature=0、JSON 输出及单次非流式请求，不跟随重定向、不自动重试或调用外部工具，丢弃推理字段；缺失用量、截断、异常模型或不合规响应均拒绝。网关及评测器使用同一个 token 预留上界，包括适配器额外系统提示和聊天格式的保守开销。

2026-10-26 官方文档的 `deepseek-flash` 对应 DeepSeek-V4.1-Flash-1017，旧 V4 Flash 名称已经路由到新版本；接口别名不是不可变权重快照。当前高峰输入/输出每百万 token 分别为人民币 2/8 元；本轮将所有 token 都按 8 元/百万计保守估算，不假设缓存折扣。实际供应商账单不由此估算证明；未来运行前须重新核对 [官方模型及价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。

四类专家另有待验的只读交接协议与合成样例执行器：`python scripts/prepare_expert_review.py` 临时生成职业、学习、求职、面试各 4 条草案并输出摘要及 SHA256。`expected_action` 和必须引用的依据只用于评分，不进入主模型或复核模型输入。已由操作者审核相应标签后，才可显式传入该摘要运行：

```powershell
docker compose run --rm -T checks python scripts/compare_expert_models.py --reviewed-sha256 <已审核样例摘要> --budget-micros-cny 5000000 --token-price-micros-cny 8 --repeats 3 --allow-external
```

该入口使用同一通用模型、相同依据及程序生成的来源版本清单，对照一次决策与按场景交接给四类专家之一复核；返回动作枚举及引用，禁止生成业务写入。重复轮次交替执行顺序，失败/未执行保留分母，预算和授权逐次检查，用量未知停止。仅重算冻结观察，不重新调用模型；不会因标签确认、退出码 0 或合成高分而启用协作。样例 JSON、用量库和观察均临时清理，只保留生成器源码；它衡量封闭场景的下一步选择/引用，不衡量开放式专家建议，也不接入现有助手或代替代表性真实样本和独立收益验收。

重复对照和冻结结果重算是两个概念：前者重新经过适配器，后者只按冻结结构化结论重新计分。真实模型身份、代表性/标注审核、采样配置和独立质量收益仍需实际环境验证；成本是按声明 token 单价估算，不能当作供应商账单。适配器是受信本地代码，应固定模型版本、遵守异步 Provider 协议且不得记录输入/输出正文。当前自动化验证使用明确标识的模拟适配器，不构成真实模型收益证据。


在「本地工具 → 模型对照临时复核」中，可粘贴同一授权数据集与 CLI 使用 `--show-steps` 打印的 JSON，核对摘要/版本、重算指标并展开每轮每个样例的决策/复核步骤。界面区分重算一致和不一致，显示重算值及成本完整性；不会再次调用模型或写入历史。修改输入使旧结果失效；清空、重新加载内容、离页或切换语言后清除当前内容。观察来源仍未经独立核验，用户可修正导入内容后再次核对，但重算一致不构成真实调用、标注正确或样本代表性的证明。CLI 输出只供当次临时查看，不生成持久报告；提供的数据集来源文件按原业务规则管理，程序不覆盖或删除原文件。


模型对照临时复核现支持明确的逐条人工修正：在步骤下选择 true/false、填写本人依据，再点击「核对人工修正参考结果」；可「撤回全部人工修正」。修正只叠加到单独的人工参考正确数，原观察、冻结标签、模型质量差、原始费用/耗时和启用状态均不改写。原调用失败或未执行时，人工判断也不会冒充一次模型调用。备注仅本次暂存，不回传、不落库、不作为模型输入；输入变化、撤回、离页及切语会清除旧参考结果，未提交的修改不计入参考结果。

「本地工具 → 四类专家冻结流程复核」现提供独立的网页核对入口。粘贴 case 数组或含 `cases` 的对象，以及带 `rows` 的专家冻结观察，可查看通用 Agent → 专家复核的有序步骤、结构化动作/来源版本和中英失败原因，重算分组、每轮质量差、费用完整性和耗时。身份验证必需，输入总量最多 1 MiB；只投影固定动作与来源标识/版本，不回传场景正文、标准标签、人工备注或未知报告字段，不调用模型或写入业务历史。输入变化、重绘、切语、清空及页面缓存恢复均使旧结果失效。

每条专家观察支持选择该角色允许的人工动作、勾选当前来源版本并填写本人依据。引用缺失/过期/伪造、重复目标、非法角色动作和凭据内容拒绝。人工参考结果单独计分，可全部撤回；原观察、步骤、冻结标签、模型质量/费用/耗时保持不变，补足未执行观察不会制造模型调用。界面持续提示来源未经独立核验、封闭下一步评分不代表开放式专家质量；没有启用协作的操作。

2026-10-26 用户确认 CP-222 按“本地评测/回放交付，协作 No-Go”技术结项，V4 本地技术验收为 18/20。该范围覆盖上述评测和复核工具；开放式专家能力及真实代表性收益仍未验收，协作保持关闭，正式生产和法律审核另行保留，详见 PLAN W-044。

CP-260 新增独立本地发布候选与故障演练：在项目根目录运行 `python -B scripts/candidate_drill.py`，需要 Docker Compose 及现有 `careerpilot-dev:local` 镜像。脚本打包当前源码，使用文件密钥、非 root/只读应用、双 API/双 Worker、独立 PostgreSQL 和就绪路由，测试节点退出、失败配置灰度撤回、数据库重启、并发身份/跨用户隔离和原生备份恢复。合成数据及备份仅在一次性 Docker 资源和系统临时上下文使用，成功/失败均清理；不挂载真实业务卷、不读取 `.env` 或调用模型。范围、清理及正式环境差距见 [ADR-026](docs/ADR-026-local-candidate-recovery.md)；本地观测不代表正式生产 SLA，验收计数以 PLAN 为准。

2026-10-26 用户先确认 CP-260 本地工程验收，随后明确“仅本人本地使用”。W-047 完成 CP-262 本地治理交付后，开发 78/78、V4 20/20、本地质量包 6/6；正式隐私/版权/法律签核及原生产门槛保留，协作继续关闭。注册前与页脚可打开[本地使用与数据说明](http://127.0.0.1:8000/app/local-use.html)，源码随静态资源交付。说明当前资料、保留期限、导出注销、外部授权及旧备份恢复的实际边界，不作为公开服务协议或法律批准。详见 [PLAN W-047](PLAN.md) 和 [ADR-027](docs/ADR-027-personal-local-governance.md)。

`prepare_expert_review.py --show-json` 只向标准输出提供生成的场景对象，在同一系统临时上下文完成校验和清理。`compare_expert_models.py` 增加 `--cases <明确授权的来源文件>` 和 `--show-steps`：前者要求 `source` 为 `synthetic`/`owner_authorized`、`rights_confirmed: true`、`cases` 为场景列表，并沿用审核摘要检查；本人来源文件只读、不会被程序覆盖或删除。后者输出单条含结构化步骤的 JSON，隐藏进度行，方便当次临时粘贴核对，不含场景原文或推理。声明为本人授权的输入仍标作来源未经独立核验。生成样例和观察只作临时查看，不重定向保存到仓库或持久证据包；调用前核对当前价格及此前累计预算余额。
