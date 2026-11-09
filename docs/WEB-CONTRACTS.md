# 网页替换：实现契约与功能归属

更新：2026-10-28。当前项目原位替换，产品入口仍为 `/app/`；不创建第二个应用或过渡站点。本文维护实现契约，实际验证结果只在 PLAN 记录。

界面仅要求桌面浏览器尺寸，页面最小宽度为 1100px；已移除窄屏断点、手机导航及覆盖式会话侧栏，不再要求手机尺寸适配。桌面验证覆盖 1280、1366、1440、1920px 及较矮窗口，中英、键盘和内部滚动要求保持。

## 入口、接口和原记录

当前日常主导航为 AI 助手（`/app/today`，也是 `/app/` 默认）、学习（`/app/learn`）和求职（`/app/career`）；目标岗位、资料及设置为辅助入口。下表是原业务路径的保留与归属。

| 入口 | 原入口归属 | 保留接口族 | 原记录与边界 |
| --- | --- | --- | --- |
| 今日行动 | dashboard、progress 的摘要和通知 | workflows/runs、plans、notification-center、progress-report | agent_run、BackgroundTask、plan、通知；业务事实仍属于原领域 |
| 目标与岗位 | explore、market、directions、skills | career-goals、goal-previews、jobs、job-search、job-matches、gap-analysis、skill-graph、skills | CareerGoal、job、技能证据、匹配快照、偏好和订阅 |
| 学习与实践 | learning、sources、portfolio 执行、progress | plans、plan-previews、assessments、projects、learning-resources、source-documents、study-logs、review-pipelines | 预算/先修/客观评分沿用；项目引用同一个 practice_project |
| 求职 | recruiting | resume-facts、resumes、resume-versions、applications、interviews、interview-reviews | 事实来源失效规则、不变简历/申请快照、文字面试和本人回执沿用 |
| 我的资料 | settings、portfolio 成果、本人材料 | profile、materials、resume-imports、memories、experiences、projects、profile-timeline | 不另建 profile_v2 或事实库；经历扩展原 memory |
| 设置 | 必要本人维护 | consents、agent-tasks、local-workspace、account/export | 无登录、注册或运营角色；清空及来源处理边界沿用 |

旧 hash 深链接映射到新分区；延期 collaboration、平台实验及运营深链接明确提示历史位置。延期私人记录通过设置中的历史资料读取，全量个人导出及工作区清空仍覆盖其记录；不调用延期功能以生成新数据。历史身份合同仅用于显式测试，不恢复到默认产品。

## 资料与私人草稿

引导状态复用工作区所属 `private_draft`，由 `GET/PUT /api/v1/workflows/guidance` 读取及按 If-Match 更新；同一工作区稳定 ID、所有者锁和版本冲突防止覆盖。状态保存目的、已问及回答提交标记；提交时核对运行归属，状态不保留对话内容或运行 ID，提交标记不代表内容已理解或正式保存。初始检查读取现有档案、有效自述、目标和档案草稿；展示、暂缓及恢复不调用模型。按动作的 `check_requirements` 只读检查当前状态，未知不当作事实，正式业务校验保持。`save_profile_draft` 预览旧值和新值，经确认保存原私人草稿；保存前复核草稿和正式档案版本，恢复编辑保留现有字段。验证范围与运行状态见 PLAN 最新工作记录。

聊天 `import_job` 只接受本轮用户消息中逐字出现的原文和来源；对以“请导入以下岗位原文：”或“Please import the following job description:”等明确前缀开头的消息，程序要求模型提供冒号后的完整文本，不能静默截去其中部分内容。其他自由表达仍按原有逐字子串规则检查；所有写入均显示原文后确认。执行前复核岗位集合版本，再调用原岗位导入、来源及去重逻辑；成功回执提供真实岗位 ID，不等于验证招聘信息。有关来源、模型授权与预算规则不变。

没有完整档案时，计划可使用用户为本计划明确提供并确认的每周分钟预算；预算只保存在计划的 `budget_cap_minutes`，不是伪造档案。直接计划表单接受可选 `weekly_minutes`，聊天工具使用同一领域校验；没有档案也没有可复用预算时提示补充。发布、任务、项目安排及重排仍校验计划预算、先修和保护任务；后来创建或修改档案会改变来源上下文，未发布的旧预览需重新核对。锁定、进行中及已完成任务不能被发布或重排静默替换。

- “我的资料→全部资料”（`/app/profile#all-information`）按 21 类资料及业务记录集中搜索、筛选、查看/历史和管理；可直接创建的资料复用原新增表单，匹配快照/调整预览/复习安排由原业务生成。各原分区复用 InformationActions。`GET /api/v1/information?kind=&q=` 只返回归属资料，record_kind 与事实自身 kind 分离；过期来源仅返回删除所需元数据，搜索及历史不泄露过期正文。新增职业目标、面试练习/复盘、译文、Offer 比较、匹配快照、计划调整预览和复习安排；默认隐藏移入历史的记录，可勾选查看及恢复。延期功能的历史查看/导出/整体清空沿用原边界。
- `GET /information/{kind}/{id}/history` / `.../deletion-preview` 与 `DELETE /information/{kind}/{id}` 均核对所有者；删除要求 `If-Match`、confirmed=true 及最新预览 fingerprint。预览包含所属子项和阻塞依赖，关联内容或版本变化返回 412，有依赖返回 409。删除所属子项及历史，移除包含已删记录的 HTTP 幂等回执；撤销关联 SkillEvidence，作废旧助手 checkpoint 可恢复性。保留独立简历/申请快照，不处理模型额度、费用或公共词库。
- 原接口新增 `POST /memories`（仅本人自述）、`PUT /materials/{id}`、`PUT /resume-facts/{id}`、`PUT /source-documents/{id}`、`PUT /jobs/{id}`、`PUT /projects/{id}/details`、`PUT /resumes/{id}/details`、`PUT /application-drafts/{id}`、`PUT /study-logs/{id}` 与 `PUT /quarter-reviews/{id}`。显式模型限制可改字段，沿用上传、来源权限/引用、JD 解析、事实来源和学习每日时长校验；更新需确认和版本。简历修改内容创建新版本，申请阶段沿用 TransitionInput；统一编辑入口内可直接填写。
- `POST /plans/{id}/edit-preview` 使用 PlanEdit/PlanTaskEdit 与 If-Match；可编辑名称、开始/截止日期、任务及其技能、时长、日期、先修、资源与完成标准。新增任务固定 pending/unlocked/learning_record，服务端保留已有状态/成果/验证，拒绝改删受保护任务，复核当前目标、预算、日期和先修。预览以任务名展示差异，确认/退回复用 `POST /plan-previews/{id}/accept|reject`；未接受不改原计划，草稿保存后仍须单独发布，历史沿用原修订。
- `PUT /business-records/{kind}/{id}/details` 只允许 title/note，用于面试、匹配快照、计划预览、复习安排和申请说明；`PUT /offer-comparisons/{id}` 复算分数，`PUT /resume-translations/{id}` 复核来源事实与数字。目标、本人面试复盘沿用原显式编辑契约；面试列表和历史均隐藏答案/rubric。各接口要求归属、确认和 If-Match，派生分数与客观结论不能直接覆写。
- 新增 8 类业务记录的删除入口明确显示“移入历史，可随时恢复”，调用 `POST /business-records/{kind}/{id}/archive`，ArchiveRecord.archived 控制恢复；沿用原记录及修订，不取消后台任务、不脱钩日志、不删除目标历史。目标使用原 archived 状态，恢复为 draft；仍被草稿或有效计划引用时拦截。目标历史从 `GET /career-goals/{id}/history` 读取。原 13 类资料的依赖删除预览/确认规则保持。
- `resumes.valid_version_facts` 同时核对当前事实来源和简历内事实快照版本；导出、新材料、翻译、面试复用、新申请及助手求职预览共用，事实修改后不能仅凭“当前事实有效”继续复用旧正文。翻译历史的 sources_current 也比较原事实版本，缺失事实返回失效状态而非破坏历史读取。
- `GET /resume-version-options` 返回当前工作区简历的版本选项，只包含 ID、简历 ID 和序号；按简历列表顺序及版本序号倒序排列，供表单批量读取，仍由现有领域操作校验版本与事实有效性。
- 材料原文保存精确保留空白，修订后 pending_confirmation；其旧 memory 标记 superseded，新确认的 source_version 指向本次原文修订。结构化经历只由经历编辑器更新，不再通过平面 memory 表单写出矛盾内容。事实修改保留修订，旧简历逐项比较事实快照版本；事实删除返回 source_deleted，历史快照仍可读取。档案删除后重新建档生成新身份 ID，已存在档案 ID 保持；简历重命名不占用内容版本 sequence。

- 正式档案仍使用 ProfileInput/ConfirmProfile 的完整校验；未完成编辑保存到 `private_draft`，不修改正式 profile，不视为确认事实或外部处理授权。
- 草稿接口为 `GET/PUT/DELETE /api/v1/drafts/{kind}/{record_key}`。范围仅 profile/experience/project/material/fact/interview，记录所有者和 record_key 纳入稳定 ID。
- PUT 携带 `If-Match: "版本"`，初建为 `"0"`；响应提供 version、values、base_record_version。冲突返回 412，保留本地输入并提供已保存值比较，不能静默覆盖。
- IndexedDB 只暂存未提交编辑，按工作区/类型/记录隔离；已保存草稿以后端为准。正式确认后清理对应缓存；重置后清理旧工作区缓存。浏览器禁用存储时明确提示保存私人草稿。
- `experiences` 使用原 `memory` 记录，增加 structured_experience 和 experience 字段。教育/实习/工作/项目的机构、角色、背景、任务、行动、结果分开存储；日期为 value + precision，精度为 unknown/year/month/day，不补造某月一日；未知结果为空。
- 经历本人确认仍为 self_report + user_confirmed，不成为 verified 能力。转换事实仍调用 resumes.create_fact，source_type=memory、source_id、source_version 沿用现有失效链。修改/删除源记录阻止新用，既有简历/申请快照保留。
- 材料读取与求职导入统一使用原解析器。携带稳定 Idempotency-Key 的同一确认请求返回原结果；不同请求复用键返回冲突，语义相似内容不自动覆盖。

## 运行、事件与一致性

对话列表提供独立删除按钮与确认，取消不请求；删除当前会话回到新对话，删除其他会话保留当前草稿。`DELETE /api/v1/workflows/conversations/{conversation_id}` 需 confirmed=true 与 If-Match；204 后会话、消息/摘要/历史副本立即不可读，单 Worker 安全清除执行数据并压缩 SQLite 文件。保留已保存的独立业务资料与已用预算，细节见 ADR-031。没有自动删除真实对话或修改备份规则。

| 动作 | HTTP 契约 |
| --- | --- |
| 创建 | `POST /api/v1/workflows/runs`，AssistantInput + require_confirmation；Idempotency-Key 必需；返回持久 run_id、status、task_version、graph_version、sequence |
| 列表/快照 | `GET /api/v1/workflows/runs`、`GET .../{run_id}`，所有读取核对工作区归属 |
| 取消 | `POST .../{run_id}/cancel`，If-Match 对 task_version；取消后不执行后续业务动作，已提交动作不回撤 |
| 确认/拒绝 | `POST .../{run_id}/resume`，If-Match + approved；仅当前图版本的 waiting_confirmation 可恢复 |
| 重试 | `POST .../{run_id}/retry`，If-Match；仅当前版本的失败运行；继续原 thread 与原领域回执 |
| SSE | `GET .../{run_id}/events?after=N` 或 Last-Event-ID；update 携带 run_id/sequence/type/payload；snapshot 补齐当前持久状态 |
| 会话 | `GET/POST /api/v1/workflows/conversations`；创建需 Idempotency-Key；`GET .../{conversation_id}/runs?limit=30&before=N` 分页读取归属会话消息 |
| 偏好 | `GET /api/v1/workflows/preferences`；`POST .../{preference_id}/forget` 需 If-Match 与 confirmed；已忘记项不再注入新会话 |

状态为 queued/running/waiting_confirmation/completed/failed/cancelled。旧 assistant 接口只保留同一执行器的兼容投影（success 对应 completed），不执行旧内部步骤。旧 BackgroundTask 继续管理队列、派发、租约和 Outbox；Agent 节点与内部 checkpoint 仅由 LangGraph 管理。

模型提示协议为 growth.chat.v9（个人资料、求职、工具恢复及确认保存反馈）：短岗位原文可预览、缺项保持未知，明确标记的岗位原文须完整保留，可疑指令只作为数据。格式约束、动态 Schema 与示例只由 system_prompt 提供；历史 assistant 回答转换为同格式 JSON，个人资料和用户输入不提升为系统指令。摘要使用独立协议。空回复及普通回答仅遗漏空参数/来源字段时最多重试一次，后者附加固定系统提示；每次重新检查额度、授权和取消。其他非法输出或失败仍明确报错，成功工具回执不重放，未通过 Schema 的内容不会被接受。工作区 HTTP 请求结构与 Schema 0007 不变。

确认恢复返回 confirmation_response，accepted/saved/declined/failed 明确区分请求提交与业务保存。提交后消费 confirmation；工具回执与部分 result 同事务保存并追加事件，最终模型回复前即可展示保存状态，刷新继续可见。前端确认/拒绝共享请求锁，确认响应推进快照游标，错误保留重试；反馈位于确认卡之外，卡片关闭仍可见。计划标注“已确认并保存为草稿 · 尚未发布”或“已发布”，发布需独立确认。同轮同参数的成功写入不会再次创建或确认；已保存后的模型额度不足仅说明回复受限并保留结果，不增加费用额度。旧运行只读投影原保存回执，历史记录保持。

计划保存回执保留保存时快照；GET 运行快照补充 current_status，只读查询当前所有者的计划状态，界面据此显示草稿或已发布。之后在学习页发布计划并刷新聊天时，不再把历史保存卡继续显示为未发布；不覆盖原回执、任务快照或历史。

工具循环由 chat_limits 统一为至多 12 步，图递归上限 56；原 token、费用、确认、取消和授权边界保持。模型收到剩余步数、最近工具结果及全部步骤状态摘要，按需读取资料；无效对象先回查，不猜 ID。程序拦截紧接着重复的失败参数及本轮已拒绝的相同操作，直接说明未完成原因；达到边界时无需额外模型调用，依据真实回执整理已保存/失败/拒绝和待继续部分，正常完成本次回复，不抛 CHAT_TOOL_LIMIT。全部结果保留在原会话，可继续处理；失败投影和删除清理覆盖扩展后的全部回执。

求职工具包括 create_resume_fact、create_resume、revise_resume、tailor_resume、check_resume、draft_material 和 create_application；read_context 新增 resume_facts/resumes/resume_versions/applications/application_drafts。复用原求职校验、事实来源和版本服务，写入都需具体预览确认；事实过期/撤销、岗位或版本变化后需重新预览。前端 hiring-result 呈现事实陈述、目标岗位、材料全文和保存回执，结果链接到 /app/career；ATS 展示问题与未体现关键词，申请始终为未投递草稿。未知信息不补造，普通聊天/资料补充不会自动进入事实库，发送邮件及外部投递未开放。

图版本为 growth.v1，thread_id 固定等于服务端 run_id，不接受客户端任意 thread 选择。网页创建 conversation_id 后，每次请求携带会话、locale 和可选岗位/计划关联；auto 进入真实对话模型与有界工具循环，同一会话最多一个在途消息。模型基于近期原文、旧消息摘要、当前资料与近期工具引用承接追问，可按需读历史细节；不同会话历史隔离。旧未关联会话的兼容任务接口仍保留固定意图，不代表新的聊天入口。今日行动以成长助手为主区域，学习行动与提醒/复盘置于下方折叠区。会话使用可收起侧栏，按创建时间分组，支持标题搜索、当前会话高亮与长标题省略；桌面布局默认展开会话侧栏，可用标题栏按钮或 Escape 手动收起；选中会话保持侧栏状态，不再按屏幕宽度自动切换。欢迎入口只填入消息草稿，首次发送才创建会话；发送请求尚未返回时禁止切换，避免消息落入其他会话。消息按会话分页呈现，输入框置底，Enter 发送、Shift+Enter 换行并兼容输入法；新建/切换/刷新恢复及 SSE/快照兜底均可用，结果与引用直接随回答展示。

会话图为 load → model → prepare_tool → approval → tool → model，最终进入 answer；旧显式任务图保留原 prepare/confirmation/execute/summarize。聊天工具包括资料/进展/历史/资源/差距、计划草案/调整/发布/重排及显式偏好。新增 update_profile（指定字段合并、原版本校验）、add_experience（复用经历表单校验与保存）及 add_profile_note（文字自述 memory）写入。个人资料预览卡显示具体文字、经历或字段前后值，确认后显示真实保存回执及我的资料链接；未建档且必填字段不足时先澄清或保存已知文字补充。未知日期/成果留空，自述不升级验证状态，不自动生成求职事实或覆盖已发布计划。所有写入先给具体预览再确认，客户端不能关闭该边界；发布和接受预览复用原领域合同。SqliteSaver 保存确认中断，等待时释放事务与租约。一个 Worker，每 5 秒续租；模型、摘要和工具回执使用稳定 run/round 标识，重复恢复不重复写入，最终回答失败仍投影已成功的工具结果。完整约束见 ADR-030。

领域写入与 `workflow_receipt` 在同一短事务提交，回执 ID 由 run/动作版本确定；业务提交后丢失 checkpoint 时返回原回执，不重复创建计划。SQLite 工作流库不与业务库伪装为原子事务。旧图版本未匹配的在途任务明确停止，不伪造 checkpoint。

`workflow_events` 是 0007 追加表，按 run/sequence 持久化，并对 run/event_key 去重；事件与快照保留所有者。SSE 定期关闭连接后可续订，终止流不代表成功；前端按序号去重、独立取消订阅，重置立即结束旧订阅。当前模型返回完整 JSON 后展示回答，不逐 token 输出，也不伪造文本流。会话、摘要、偏好和回执沿用 GrowthRecord，Schema 仍 0007；随原工作区导出/清空，业务资料及来源规则保持。

资料详情采用正文阅读布局：全部资料与原分区共用 InformationDetail，必要状态和时间简短展示，正文保留原始文字、换行与空行；经历按背景、任务、行动和成果组织，不重复展示生成的汇总文字。简历事实按类别阅读，岗位仅提取已知的要点，项目说明与学习安排直接可读。来源引用、版本和历史快照按需展开；编辑文字资料和经历的表单默认收起。仅缩短列表标题，不截断正文、不补造未知内容，也不改变原始资料、验证状态及增删改查接口。

## 构建、存储与运行

- 学习计划标题优先使用已有名称，否则使用关联岗位名与“学习计划”，无关联岗位时显示“学习计划”；不以开始日期作为标题，日期单独显示在计划信息中。计划默认展开，以日期分组的勾选清单呈现任务名称、时长、真实状态与完成进度；详情、延期、拆分和历史调整按需展开。草稿与先修未完成的任务不可直接勾选；勾选后记录本人学习成果，保存时沿用原 start/complete 状态机和每次返回的计划版本，成功后计入进度，失败保留输入并刷新真实状态。完成的学习任务保持勾选，不提供后端尚不支持的撤销完成操作；勾选不替代能力考核。今日行动复用同一清单，已完成项不再列入待办。
- 复盘默认期间为设备系统当地月份首日至今天，使用本地年月日，不固定上海时区或依赖区域化日期字符串。页面跨天、跨月与重新获得焦点时更新默认日期和报表；手动选定的历史期间保持，可用“使用系统日期”恢复默认。阶段反思复用当前已查询期间，历史业务记录的日期保持原值。

- 今日行动的提醒默认仅显示未读，标题与两行内摘要直接可见，已读历史和正文按需查看；标记已读沿用原接口与版本校验。复盘默认只展示所选期间的本人记录分钟、完成任务、逾期待办、新增证据四项摘要；“详细复盘”按学习进展、能力积累、求职进展使用自然语言表达，学习区与通知中的周期报表复用同一解读，不输出原始报表对象。统计口径按需展开，时长换算为小时/分钟；期间新建申请的进展与当前有效验证状态分别表述。阶段反思沿用原确认与保存接口，复用所选日期，仅展示保存日期范围和本人正文，不平铺报表/目标/计划快照；原业务快照仍保留。辅助面板沿用桌面网格布局。

- 页面不再显示顶部个人工作区栏，语言切换位于设置。对话按动态视口高度铺满，输入框置底；长消息和会话列表内部滚动，页面本身不随内容撑长。其他分区保留正常文档滚动。

- Node 24 构建 React 19.3.1、TypeScript 7.0.2、Vite 8.3.5，依赖锁定于 frontend/package-lock.json。Python 3.14 镜像安装 requirements.lock；LangGraph 1.2.18、SQLite checkpointer 3.1.2。
- Docker 构建将 Vite 产物复制至 `/opt/careerpilot-web`，由 FastAPI 同源提供。日常镜像无 Node/Vite 服务前置要求，不挂载整个源码，也不启用 API reload。
- `/app/` 和扩展名为空的 HTML 导航允许 SPA fallback；缺失资源、API、下载保持真实错误。未构建则报 503，不回退旧产品界面。前端永不编译模型密钥或原环境配置。
- dev profile 的 Vite 只在宿主 127.0.0.1:5173 发布，保留 Host 代理 `/api` 和 `/health`，后端继续核对本地 Host、Origin 与跨站来源。node_modules 使用开发专用卷。
- 业务卷仍为 `careerpilot_careerpilot-data`，业务库路径不改；工作流库为同目录 workflows.sqlite。工作流文件 Schema 为 user_version=1，不兼容版本停止；显式 migrate 初始化。SQLite 保留外键、10 秒 busy_timeout，bootstrap 配置业务库 WAL。
- 原文件/媒体/项目记录继续受原受控接口管理，不静态暴露私人附件。PDF/Word 保留后端 Chromium 与中文字体；代码评测沿用独立 Docker 容器和宿主受控 runner，API/Worker 无 Docker socket。
- `careerpilot.storage_backup` 在 API/Worker 停止后备份整个数据根：SQLite backup、工作流库、附件及内容摘要；备份不递归包含旧 backups，不覆盖目标。恢复必须是独立新目录，并校验完整性与引用。业务备份保留；不使用删卷作为迁移或恢复。

依据：[网页规范](WEB-REFACTOR-PLAN.md)、[ADR-029](ADR-029-web-stack-docker.md)、[LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)、[中断](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Vite 集成](https://vite.dev/guide/backend-integration)。验证和未完成项见 PLAN，不将本文视作验收结果。

对话跟进使用 record_interview 预览确认并核对申请版本与状态，同一安排去重并关联本地日程；无外部日历同步。save_interview_reflection 使用真实技能 ID 保存本人自述，POST /api/v1/interview-reviews/self-report 支持幂等，PUT /api/v1/interview-reviews/{id}/self-report 使用版本校验；plan_interview_review 仅生成调整预览，接受需单独确认。read_context(project_assessments) 显示原始考核状态；排队和失败不等于通过，也不自动验证能力。


### 日常工作台与结果导航（2026-10-28）

学习页直接展示当前计划及可操作任务，求职页按简历、申请与面试展示相应记录，避免重复概览；加载失败不得展示成空数据。学习提醒和复盘位于学习流程，求职页可查看提醒。搜索、历史、次要编辑及设置分类按需展开，旧入口与既有操作保持可达。

聊天中的岗位、计划、个人资料、求职事实、简历、材料、申请和面试结果链接应进入对应标签；有可匹配记录的回执带记录标识并展开对应记录，返回助手恢复当前会话。计划草稿、已发布、预览与实际保存状态保持区分；来源与详细记录按需展开，不能丢弃。


### 会话草稿、定位和键盘交互（2026-10-28）

未发送消息按工作区和会话保存在当前浏览器标签页的 sessionStorage；切换页面、会话和刷新可恢复，成功发送、主动新建对应空白会话、删除对话及本地工作区重置时清理对应草稿。标签页结束后浏览器按会话存储规则清理，不承诺跨浏览器或重启恢复。业务信息不会由草稿自动写入。

记录和任务链接进入相应分区后展开、滚动并聚焦匹配项；可从已加载列表或岗位详情定位，不扩大业务写权限。学习任务区分逾期、当天及先修限制；具体可执行状态沿用任务规则。子标签支持左右方向键、Home 和 End，并关联标签面板。

2026-10-28 求职补充：`POST /applications/external` 使用岗位、可选外部简历说明/投递时间/备注及确认创建本人自述，以幂等键去重；无系统简历版本，状态 `reported_submitted`、核验 `pending_verification`，仅回执核验后更新核验状态。`POST /applications/{id}/interview-preview` 只读返回具体安排和指纹；`POST /applications/{id}/interview` 要求原申请版本、指纹和确认，复用原聊天面试领域逻辑在事务中检查冲突、更新阶段并保存关联日程。
