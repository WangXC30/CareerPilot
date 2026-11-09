"""Source-linked single-choice practice. It does not create skill evidence."""

import hashlib
import json
from pathlib import Path

from careerpilot.domain import DomainError

# ID, topic, question, options, answer index, explanation, reference, followed
# by optional attribution fields for the reviewed source snapshots.
OriginalQuestion = tuple[str, str, str, list[str], int, str, str]
ReviewedQuestion = tuple[str, str, str, list[str], int, str, str, str, str]
CodeprepQuestion = tuple[str, str, str, list[str], int, str, str, str, str, str, str]
QUESTIONS: list[OriginalQuestion | ReviewedQuestion | CodeprepQuestion] = [
    (
        "ts-union",
        "TypeScript",
        "在 TypeScript 中，string | number 表示什么？",
        ["值可以是 string 或 number", "值必须同时是两种类型", "只表示数字字符串", "只表示数组"],
        0,
        "联合类型的值可以属于任一成员类型；使用专有操作前通常需收窄类型。",
        "https://www.typescriptlang.org/docs/handbook/2/everyday-types.html#union-types",
    ),
    (
        "ts-narrow",
        "TypeScript",
        "对 x: string | number，哪个检查可以在真分支将 x 收窄为 string？",
        ["typeof x === 'string'", "x === null", "typeof x === 'number'", "Boolean(x) === false"],
        0,
        "TypeScript 能识别 typeof 检查并在相应控制流分支收窄类型。",
        "https://www.typescriptlang.org/docs/handbook/2/narrowing.html",
    ),
    (
        "ts-structural",
        "TypeScript",
        "两个普通对象类型具有相同必需属性和兼容类型时，TypeScript 的类型兼容主要依据什么？",
        ["结构是否兼容", "类型名称必须相同", "声明文件的行号", "必须使用同一个接口声明"],
        0,
        "TypeScript 主要采用结构类型系统；具体检查还受私有成员等条件影响。",
        "https://www.typescriptlang.org/docs/handbook/type-compatibility.html",
    ),
    (
        "node-env",
        "Node.js",
        "Node.js 中未设置的 process.env.MISSING 通常是什么？",
        ["undefined", "空字符串", "0", "false"],
        0,
        "process.env 表示环境变量，读取未定义的属性得到 undefined。",
        "https://nodejs.org/api/process.html#processenv",
    ),
    (
        "node-backpressure",
        "Node.js",
        "可写流 write() 返回 false 后，生产者通常应如何处理背压？",
        ["暂停写入并等待 drain", "立即无限循环写入", "认为数据已被安全持久化", "直接删除流"],
        0,
        "false 表示写入缓冲需要排空；继续写入可能导致缓冲不断增长。",
        "https://nodejs.org/api/stream.html#writablewritechunk-encoding-callback",
    ),
    (
        "node-cjs",
        "Node.js",
        "CommonJS 模块中的 __dirname 指什么？",
        [
            "当前模块文件所在目录的绝对路径",
            "发起 HTTP 请求的客户端目录",
            "总是进程启动目录",
            "模块的网络地址",
        ],
        0,
        "__dirname 是当前 CommonJS 模块的目录，不必等于 process.cwd()。",
        "https://nodejs.org/api/modules.html#__dirname",
    ),
    (
        "css-box",
        "CSS",
        "默认 box-sizing: content-box 时，声明的 width 主要对应哪部分？",
        ["内容区宽度", "内容加内边距和边框的总宽度", "外边距的宽度", "浏览器窗口宽度"],
        0,
        "content-box 的 width 指内容盒；内边距和边框在其外额外占据空间。",
        "https://developer.mozilla.org/en-US/docs/Web/CSS/box-sizing",
    ),
    (
        "css-flex",
        "CSS",
        "flex-direction: row 时，justify-content 主要沿哪个轴分配空间？",
        ["主轴", "交叉轴", "总是垂直轴", "层叠轴"],
        0,
        "justify-content 沿主轴分配空间；row 的主轴方向受书写模式等影响。",
        "https://developer.mozilla.org/en-US/docs/Web/CSS/justify-content",
    ),
    (
        "css-position",
        "CSS",
        "position: sticky 的元素在达到阈值前通常如何参与布局？",
        ["按正常文档流参与布局", "始终从文档流移除", "总是相对视口固定", "始终不可见"],
        0,
        "sticky 先按正常流布局，再相对滚动容器等条件进行偏移。",
        "https://developer.mozilla.org/en-US/docs/Web/CSS/position",
    ),
    (
        "http-401",
        "HTTP",
        "HTTP 401 响应通常表明什么？",
        [
            "请求缺少有效的目标资源认证凭据",
            "服务器已经成功处理请求",
            "资源永久移动",
            "服务器内部错误",
        ],
        0,
        "401 表示请求缺少有效认证凭据，响应通常包含认证挑战。",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/401",
    ),
    (
        "http-429",
        "HTTP",
        "HTTP 429 通常表示什么？",
        ["客户端在一定时间内发送请求过多", "资源不存在", "请求已经成功", "服务器要求永久重定向"],
        0,
        "429 表示速率限制；服务端可以提供 Retry-After 说明何时重试。",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/429",
    ),
    (
        "http-nostore",
        "HTTP",
        "Cache-Control: no-store 的主要意图是什么？",
        [
            "要求缓存不要存储该请求或响应",
            "缓存必须永久保存响应",
            "允许缓存但永不验证",
            "强制状态码为 200",
        ],
        0,
        "no-store 指示缓存不要存储请求或响应；它不是对恶意缓存的绝对保证。",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control",
    ),
    (
        "db-distinct",
        "数据库原理",
        "SELECT DISTINCT 的作用是什么？",
        ["去除结果中的重复行", "删除表中的重复记录", "总是按主键排序", "创建唯一索引"],
        0,
        "DISTINCT 对查询输出行去重，不会删除原表数据。",
        "https://www.postgresql.org/docs/18/queries-select-lists.html#QUERIES-DISTINCT",
    ),
    (
        "db-having",
        "数据库原理",
        "对 GROUP BY 的聚合结果进行过滤通常使用哪个子句？",
        ["HAVING", "WHERE", "ORDER BY", "LIMIT"],
        0,
        "WHERE 在分组前筛选输入行；HAVING 筛选分组后的结果。",
        "https://www.postgresql.org/docs/18/queries-table-expressions.html#QUERIES-GROUP",
    ),
    (
        "db-index-write",
        "数据库原理",
        "增加索引可能带来什么代价？",
        ["写入维护和存储成本增加", "所有查询必定更快", "不再需要事务", "自动消除死锁"],
        0,
        "索引可能加速某些查询，也需要占用空间并在数据变动时维护。",
        "https://www.postgresql.org/docs/18/indexes.html",
    ),
    (
        "net-tcp-stream",
        "计算机网络",
        "TCP 提供给应用的基本数据模型是什么？",
        [
            "可靠、有序的字节流",
            "保留每次写入边界的消息队列",
            "只支持单向数据",
            "每个包都保证低延迟",
        ],
        0,
        "TCP 是面向连接的可靠有序字节流；应用需要自行定义消息边界。",
        "https://www.rfc-editor.org/rfc/rfc9293.html",
    ),
    (
        "net-udp",
        "计算机网络",
        "UDP 自身是否提供 TCP 式可靠重传？",
        ["不提供", "总是提供", "只在 IPv6 提供", "只在本机提供"],
        0,
        "UDP 本身不提供可靠交付或重传，需要时由应用或其他协议处理。",
        "https://www.rfc-editor.org/rfc/rfc8085.html",
    ),
    (
        "os-exec",
        "操作系统",
        "Linux 上成功调用 execve 的直接效果是什么？",
        [
            "用新程序替换调用进程的程序映像",
            "创建一个新子进程并保留原程序",
            "立即回收所有子进程",
            "仅创建一个线程",
        ],
        0,
        "execve 替换当前进程的程序映像；创建子进程是 fork 的职责。",
        "https://man7.org/linux/man-pages/man2/execve.2.html",
    ),
    (
        "os-zombie",
        "操作系统",
        "子进程终止后，父进程尚未回收其状态时通常称为什么？",
        ["僵尸进程", "运行中线程", "孤儿 socket", "内核模块"],
        0,
        "终止子进程的状态需要被等待和回收；等待前会保留僵尸状态。",
        "https://man7.org/linux/man-pages/man2/waitpid.2.html",
    ),
    (
        "security-sql",
        "应用安全",
        "在 SQL 查询中防止把用户输入解释成 SQL 语法的首选方法是什么？",
        ["参数化查询", "直接拼接输入", "只隐藏错误消息", "把所有值转换为大写"],
        0,
        "参数化查询将 SQL 结构与数据分离；动态标识符仍需额外约束。",
        "https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html",
    ),
    (
        "security-cors",
        "应用安全",
        "CORS 能否替代后端认证和授权？",
        [
            "不能，后端仍需独立检查",
            "能，它保护所有非浏览器客户端",
            "能，只要允许任意来源",
            "只能替代数据库索引",
        ],
        0,
        "CORS 是浏览器控制跨域访问的机制，并不替代服务器的身份与权限检查。",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS",
    ),
    (
        "system-tracing",
        "系统设计",
        "排查一次请求跨多个服务的延迟时，哪个信号最直接展示跨服务调用路径？",
        ["分布式追踪", "仅单个服务的进程 ID", "只看静态配置文件", "磁盘文件名"],
        0,
        "分布式追踪通过关联 span 展示一次请求跨进程的执行路径和时间。",
        "https://opentelemetry.io/docs/concepts/signals/traces/",
    ),
    (
        "system-circuit",
        "系统设计",
        "熔断器进入半开状态的典型目的是什么？",
        [
            "允许有限试探请求以判断服务是否恢复",
            "永久关闭所有调用",
            "立刻删除所有数据",
            "保证所有远程调用零延迟",
        ],
        0,
        "半开状态限制试探流量，依据成功或失败情况决定关闭或重新打开熔断器。",
        "https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker",
    ),
    (
        "k8s-ready",
        "Kubernetes",
        "Pod 的 readiness 探针失败后，典型效果是什么？",
        [
            "Pod 被标记为未就绪，通常不接收 Service 流量",
            "一定删除整个集群",
            "一定重启节点",
            "将 Secret 自动公开",
        ],
        0,
        "readiness 控制就绪状态和流量分发；持续 liveness 失败才可能触发容器重启。",
        "https://kubernetes.io/docs/concepts/workloads/pods/probes/",
    ),
    (
        "docker-volume",
        "Docker",
        "下列哪项通常适合保存需要独立于容器生命周期的数据？",
        ["Docker 卷", "仅容器进程内存", "临时 shell 变量", "镜像标签"],
        0,
        "卷的生命周期独立于容器，适合持久化数据；仍需按应用需要做好备份。",
        "https://docs.docker.com/engine/storage/volumes/",
    ),
]


_REVIEWED_HASH = "586a3180528b351b4297f60813ed0430aacdacd7754e98a444152445f56084c6"
_reviewed_data = (Path(__file__).parent / "reviewed_freecodecamp.json").read_bytes()
if hashlib.sha256(_reviewed_data).hexdigest() != _REVIEWED_HASH:
    raise RuntimeError("Reviewed freeCodeCamp question snapshot has changed")
QUESTIONS.extend(
    (
        item["id"],
        item["topic"],
        item["question"],
        item["choices"],
        item["correct"],
        item["explanation"],
        item["reference"],
        item["origin"],
        item["attribution"],
    )
    for item in json.loads(_reviewed_data)
)

_CODEPREP_HASH = "186837361358c2a592b81a8aa3d460e5860d7d34796417a2c8d55c44aeafd249"
_codeprep_data = (Path(__file__).parent / "reviewed_codeprep.json").read_bytes()
if hashlib.sha256(_codeprep_data).hexdigest() != _CODEPREP_HASH:
    raise RuntimeError("Reviewed CodePrep question snapshot has changed")
QUESTIONS.extend(
    (
        item["id"],
        item["topic"],
        item["question"],
        item["choices"],
        item["correct"],
        item["explanation"],
        item["reference"],
        item["origin"],
        item["attribution"],
        item["author_url"],
        item["license_url"],
    )
    for item in json.loads(_codeprep_data)
)


def order(question):
    return sorted(
        range(len(question[3])),
        key=lambda i: hashlib.sha256(f"{question[0]}:{i}".encode()).digest(),
    )


def public_questions():
    return [
        {
            "id": q[0],
            "topic": q[1],
            "question": q[2],
            "choices": [q[3][i] for i in order(q)],
            "language": "en" if len(q) > 7 else "zh",
        }
        for q in QUESTIONS
    ]


def answer(question_id, choice):
    question = next((q for q in QUESTIONS if q[0] == question_id), None)
    if question is None:
        raise DomainError("QUESTION_UNAVAILABLE", "题目不存在", 404)
    if not 0 <= choice < len(question[3]):
        raise DomainError("ANSWER_INVALID", "请选择题目中提供的选项", 422)
    correct_choice = order(question).index(question[4])
    result = {
        "correct": choice == correct_choice,
        "correct_choice": correct_choice,
        "explanation": question[5],
        "source_url": question[6],
    }
    attribution = question[7:]
    if len(attribution) >= 2:
        result.update(origin_url=attribution[0], attribution=attribution[1])
    if len(attribution) >= 4:
        result.update(author_url=attribution[2], license_url=attribution[3])
    return result
