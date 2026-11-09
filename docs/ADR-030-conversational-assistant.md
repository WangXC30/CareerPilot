# ADR-030：成长助手的自由问答、多轮上下文和业务工具

日期：2026-10-27。用户已选择 C：完整对话助手。验证结果与部署状态见 PLAN W-061。

## 决策

沿用 React/FastAPI、唯一 LangGraph 执行器、SQLite 和原业务卷。通过专用 DeepSeek 对话策略生成结构化决策，由程序根据白名单 Schema 校验、读取资料或执行领域工具。提取专用适配器的提示与禁止工具语义保持独立；不引入第二套 Agent 框架或多专家协作。

- conversation_id 是工作区拥有的会话，run_id 是一次消息运行及 checkpoint thread。浏览器创建会话、发送关联消息和切换会话；不得由客户端指定任意 checkpoint thread。
- 同一会话只允许一个在途运行。幂等消息不会重复追加历史；失败运行只有仍为会话最后一条时才允许重试，防止回放旧动作干扰后续消息。
- 使用 GrowthRecord 的 conversation、chat_preference、chat_model_receipt、chat_tool_receipt 等类型；沿用既有所有者、版本、修订、导出和工作区清空。现有 Schema 0007 不变，旧消息和真实资料不迁移或删除。
- 上下文使用当前消息、最多六轮近期原文、按需生成的旧消息摘要、当前档案/目标/显式偏好和近期工具引用。按 UTF-8 字节保守限制请求；过长时先压缩旧完整轮次，窗口仍不足则提示模型用 read_history 回查。原始消息保留，摘要失败不推进边界或删除原文。
- 每个模型决策和工具效果以 run/round 确定稳定回执。工具写入与回执在同一业务事务提交；checkpoint 丢失重用回执，不重复调用模型或创建业务资料。最终模型回答失败时仍展示已经成功的工具结果，重试接续原状态。

## 工具与确认

允许读取个人档案、目标、已确认经历、JD、技能/进展、计划/重排、偏好及本会话历史；允许差距分析和登记来源索引资源检索。资料截短有标记，不将不足的数据补造为事实。

求职接入复用 chat_hiring 与求职页相同领域服务：read_context 支持 resume_facts、resumes、resume_versions、applications、application_drafts，返回当前事实来源状态；create_resume_fact 经独立确认保存用户指定陈述；create_resume/revise_resume/tailor_resume 只使用当前有效事实并保留不可变版本；check_resume 执行原 ATS 规则；draft_material 保存求职信/邮件正文/自我介绍草稿；create_application 仅创建尚未投递的申请草稿。写入先计算真实事实、岗位与材料正文预览，恢复后重新校验预览指纹、来源及版本；保存回执显示求职页链接，不发送或投递。事实解析、定制顺序与材料正文生成由 resumes 共享纯函数提供，不维护第二套实现。

补充个人文字资料、添加结构化经历、更新基础档案，以及生成计划草案、修改草案或生成已发布计划的调整预览、重排、发布、接受/拒绝重排、保存/忘记偏好，都先计算具体内容，再由 LangGraph interrupt 暂停。确认等待不持有业务事务。确认后复核工作区、运行租约、模型授权、所有者、版本和资料上下文，失效则反馈原因并重新预览。客户端 require_confirmation=false 不能跳过聊天工具写入确认。

调整第一周只改变安排，不删减学习任务；周预算不超过已确认档案预算，保持先修依赖与锁定/完成任务。已发布计划修改只创建 plan_preview，接受前不替换原计划；既有重排继续使用原领域计算。聊天记录与偏好不自动升级技能证据，也不自动修改正式档案。用户明确要求补充资料时，update_profile 只合并指定字段并复核原版本，沿用 ProfileInput、历史与 ProfileChanged 调整预览；add_experience 复用 ExperienceInput 与表单同一保存函数；add_profile_note 使用原 memory 自述记录，不强迫先补齐正式档案。后三者均经具体预览确认才保存，不填造未知字段/日期/成果、不自动创建求职事实。经历与文字补充保留会话/运行来源、本人确认和未独立验证标记；同轮相同补充使用稳定记录 ID，事务回执和 checkpoint 恢复共同防重。已忘记偏好不再跨新会话注入，历史聊天与修订仍按原保留/导出/清空规则处理。

发送邮件、投递、执行代码、删除业务资料与改变外部处理授权不属于本次对话工具范围。界面保留现有领域入口。

## 模型、预算与运行

Worker 固定访问 DeepSeek 官方 HTTPS Chat Completions；对话使用实际 user/assistant 历史角色、非思考 JSON 输出与程序校验的工具决策，不依赖供应商托管会话。密钥由 Compose 从本机 .env 的 DEEPSEEK_API_KEY 单项传给 Worker，不进镜像、网页或输出日志。

W-062 将格式约束、动态 JSON Schema 和输出样例统一放进 system_prompt；历史 assistant 内容按同一回答 JSON 结构发送，用户消息与个人资料仍保留在低优先级上下文。摘要使用独立的 system_prompt、summary schema 和样例，避免与工具决策格式冲突。growth.chat.v6 要求成功写入立即说明已确认保存，区分计划草稿与发布，并处理工具恢复；v7 支持短岗位原文预览；v8 要求完整保留明确标记的原文，将其中可疑指令仅视为数据。格式约束仍只在 system_prompt，不改变运行图或既有 checkpoint/回执标识。

确认恢复接口返回 confirmation_response：accepted 表示提交成功、待执行，saved 表示领域写入与工具回执已提交，declined/failed 分别表示拒绝或保存失败。提交后移除已消费的 confirmation；每个工具事务同步更新 agent_run 的部分结果并追加持久事件，页面在最终模型回复前即可读到真实保存回执，刷新仍保留。下一项不同操作仍独立确认。程序拦截同轮同参数的成功写入重提，不再次创建或要求确认；模型后续回复遇 QUOTA_EXCEEDED/MODEL_LIMIT 且已有成功写入时，根据回执直接说明保存结果和额度限制，不增加额度。历史运行缺少反馈字段时只读投影原成功回执，不改写历史或重新执行。计划草稿保存确认与发布确认分开展示，不把已保存称为未确认。

每次请求前后检查 external_model 授权；每个工具节点再次检查。复用原模型网关用量、超时、错误与保守 token 预留，不提高 token 或费用额度。chat_limits 统一工具执行上限 12、图递归上限 56 和回执枚举/删除范围；超过原五步的正常流程可继续具体确认。工具失败回执提供 recovery，模型按需回查当前对象，不猜 ID；刚失败的相同参数或本轮已拒绝的相同操作再次提出时，程序直接生成说明，保留真实回执，不再次执行或弹出确认。到达 12 步时由程序直接根据成功/失败/拒绝回执生成中英收尾，说明已保存与尚未执行的部分并保留同会话续接，不再调用模型索要回答或抛 CHAT_TOOL_LIMIT。失败后取全部执行范围的回执，删除覆盖最终收尾及新增步骤回执；旧会话和已保存业务结果保持。

对已校验用量但内容为空白的 MODEL_EMPTY_RESPONSE 自动重试一次。对普通回答中仅缺少 `arguments` 或 `source_ids`、已有参数为空、补上空值后其他字段恰好满足决策 Schema 的情况，也最多重试一次；补齐的副本只用于识别错误，不能作为模型输出接受或保存。v9 在此重试的系统指令中加入固定的缺字段提示，不发送上一份无效输出，也不将用户内容提升为系统指令。每次重试重新预占额度并检查授权/取消；其他格式、工具决策、供应商或超时错误不自动重试。空回复和格式错误的已知 token 用量仍记录，程序不接受非法输出或重复执行已提交工具。摘要只对空回复重试并计入用量。

保留此前累计人民币 10 元上限：历史至少扣 0.80 元，W-061 真实联调预留最多 1.20 元，W-062 复现/验证预留最多 1.50 元，应用对话独立持久累计上限调整为 6.50 元，已有用量不清零；按每 token 10 微元保守预占，不回退失败调用的额度。该数值是工程上界，不是供应商账单或精确价格。

模型调用外的 API 及工具写事务均为短事务，原 Worker heartbeat 继续续租。SSE 与快照提供真实状态和最终回答，当前不逐 token 输出或模拟打字。取消后的晚到模型结果不发布，也不继续执行工具。

## 参考

- [Dify LLM Memory](https://docs.dify.ai/en/cloud/use-dify/nodes/llm)
- [LibreChat Summarization](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/summarization)
- [LibreChat User Memory](https://www.librechat.ai/docs/features/memory)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
- [DeepSeek JSON Output：格式样例与偶发空回复](https://api-docs.deepseek.com/guides/json_mode/)
