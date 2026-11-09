"""Editorial interview review cards with primary references; not skill evidence."""

from careerpilot import interview_knowledge_languages

SOURCES = {
    "fork": "https://man7.org/linux/man-pages/man2/fork.2.html",
    "exec": "https://man7.org/linux/man-pages/man2/execve.2.html",
    "wait": "https://man7.org/linux/man-pages/man2/waitpid.2.html",
    "mmap": "https://man7.org/linux/man-pages/man2/mmap.2.html",
    "pipe": "https://man7.org/linux/man-pages/man7/pipe.7.html",
    "tcp": "https://www.rfc-editor.org/rfc/rfc9293.html",
    "udp": "https://www.rfc-editor.org/rfc/rfc8085.html",
    "dns": "https://www.rfc-editor.org/rfc/rfc1034.html",
    "methods": "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Methods",
    "status": "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status",
    "isolation": "https://www.postgresql.org/docs/18/transaction-iso.html",
    "transactions": "https://www.postgresql.org/docs/18/tutorial-transactions.html",
    "index": "https://www.postgresql.org/docs/18/indexes.html",
    "explain": "https://www.postgresql.org/docs/18/using-explain.html",
    "constraints": "https://www.postgresql.org/docs/18/ddl-constraints.html",
    "react": "https://react.dev/reference/react/useEffect",
    "js": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise/all",
    "docker-image": "https://docs.docker.com/get-started/docker-concepts/the-basics/what-is-an-image/",
    "docker-volume": "https://docs.docker.com/engine/storage/volumes/",
    "docker-context": "https://docs.docker.com/build/concepts/context/",
    "docker-cache": "https://docs.docker.com/build/cache/optimize/",
    "k8s-pod": "https://kubernetes.io/docs/concepts/workloads/pods/",
    "k8s-deployment": "https://kubernetes.io/docs/concepts/workloads/controllers/deployment/",
    "k8s-service": "https://kubernetes.io/docs/concepts/services-networking/service/",
    "k8s-config": "https://kubernetes.io/docs/concepts/configuration/configmap/",
    "k8s-secret": "https://kubernetes.io/docs/concepts/configuration/secret/",
    "k8s-probe": "https://kubernetes.io/docs/concepts/workloads/pods/probes/",
    "git-fetch": "https://git-scm.com/docs/git-fetch",
    "git-pull": "https://git-scm.com/docs/git-pull",
    "git-revert": "https://git-scm.com/docs/git-revert",
    "git-restore": "https://git-scm.com/docs/git-restore",
    "git-status": "https://git-scm.com/docs/git-status",
    "sql-injection": "https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html",
    "xss": "https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html",
    "csrf": "https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html",
    "ssrf": "https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html",
    "password": "https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html",
    "cors": "https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS",
}

# ID, topic, question, answer, primary source key. Answers describe a specific
# implementation or standard where behaviour is not universal.
CARDS = [
    (
        "os-fork",
        "操作系统",
        "Linux 的 fork() 做什么？",
        "创建调用进程的子进程。父子进程具有独立地址空间，子进程有自己的进程 ID。",
        "fork",
    ),
    (
        "os-exec",
        "操作系统",
        "execve() 与 fork() 的作用有什么不同？",
        "fork 创建子进程；成功的 execve 在调用进程中用新程序替换当前程序映像，不是另建进程。",
        "exec",
    ),
    (
        "os-wait",
        "操作系统",
        "waitpid() 常用于什么？",
        "等待指定子进程的状态变化并读取状态；等待已终止子进程可回收其状态信息。",
        "wait",
    ),
    (
        "os-zombie",
        "操作系统",
        "Linux 中僵尸进程是什么，为什么要等待它？",
        "子进程已终止但退出状态尚未被等待和回收时会保持僵尸状态；等待操作可释放内核保留的状态。",
        "wait",
    ),
    (
        "os-map",
        "操作系统",
        "mmap() 的基本作用是什么？",
        "在调用进程的虚拟地址空间中创建映射，可映射文件或创建匿名映射；访问与共享行为由参数决定。",
        "mmap",
    ),
    (
        "os-pipe",
        "操作系统",
        "Linux 管道提供什么类型的数据通道？",
        "管道提供单向字节流通道，进程可以通过管道的读端和写端通信；它不保留消息边界。",
        "pipe",
    ),
    (
        "net-tcp",
        "计算机网络",
        "TCP 向应用提供什么服务？",
        "TCP 提供面向连接、可靠且按序交付的字节流；应用消息边界不由 TCP 保留。",
        "tcp",
    ),
    (
        "net-handshake",
        "计算机网络",
        "TCP 建立连接通常使用什么过程？",
        "通常使用三次握手同步连接状态和初始序列号；协议也考虑同时打开等情况。",
        "tcp",
    ),
    (
        "net-retransmit",
        "计算机网络",
        "TCP 如何处理丢失的数据段？",
        "TCP 使用序列号、确认和重传等机制检测和修复丢失，以向应用提供可靠、有序字节流。",
        "tcp",
    ),
    (
        "net-halfclose",
        "计算机网络",
        "TCP 半关闭是什么意思？",
        "连接的两个传输方向可分别关闭；一端停止发送后，另一方向仍可能继续传送数据。",
        "tcp",
    ),
    (
        "net-udp",
        "计算机网络",
        "UDP 自身是否保证可靠、有序交付？",
        "UDP 不提供 TCP 式可靠交付或重传；需要这些功能的应用须自行实现或选用提供相应服务的协议。",
        "udp",
    ),
    (
        "net-dns",
        "计算机网络",
        "DNS 的基本作用是什么？",
        "DNS 是分层、分布式的命名系统，客户端可通过解析器查询域名相关的资源记录。",
        "dns",
    ),
    (
        "http-safe",
        "HTTP",
        "HTTP 的安全方法是什么意思？",
        "安全方法的语义是客户端请求的操作本质上只读，例如 GET 和 HEAD；"
        "服务器仍可能产生日志等附带效果。",
        "methods",
    ),
    (
        "http-idempotent",
        "HTTP",
        "HTTP 幂等方法是什么意思？",
        "同一请求重复执行的预期服务器效果与执行一次相同；这不要求响应完全相同，也不排除日志等附带效果。",
        "methods",
    ),
    (
        "http-put",
        "HTTP",
        "PUT 和 POST 的常见语义区别是什么？",
        "PUT 请求创建或替换目标资源的表示，具有幂等语义；"
        "POST 将内容提交给目标资源处理，语义取决于目标资源。",
        "methods",
    ),
    (
        "http-404",
        "HTTP",
        "HTTP 404 表示什么？",
        "服务器未找到目标资源的当前表示，或不愿披露其存在；它本身不说明缺失是暂时还是永久。",
        "status",
    ),
    (
        "http-304",
        "HTTP",
        "HTTP 304 的用途是什么？",
        "条件 GET 或 HEAD 的缓存验证表明资源未修改时，可返回 304，客户端可复用已缓存的表示。",
        "status",
    ),
    (
        "db-transaction",
        "数据库原理",
        "数据库事务中的 COMMIT 与 ROLLBACK 有什么区别？",
        "COMMIT 提交事务中成功完成的更改；ROLLBACK 放弃事务中尚未提交的更改。",
        "transactions",
    ),
    (
        "db-savepoint",
        "数据库原理",
        "PostgreSQL 中保存点的用途是什么？",
        "SAVEPOINT 在事务中建立回退点；ROLLBACK TO 可以撤销保存点之后的更改，"
        "而保留之前的事务工作。",
        "transactions",
    ),
    (
        "db-readcommitted",
        "数据库原理",
        "PostgreSQL 的 Read Committed 下普通 SELECT 看到什么？",
        "普通 SELECT 看到该查询开始前已提交的数据，以及本事务先前的写入；"
        "同一事务内后续查询可看到较新的提交。",
        "isolation",
    ),
    (
        "db-serializable",
        "数据库原理",
        "PostgreSQL Serializable 事务为什么仍需重试？",
        "数据库会检测可能导致不可串行化结果的并发情形，并可能以序列化失败中止事务；应用需要准备重试整个事务。",
        "isolation",
    ),
    (
        "db-index",
        "数据库原理",
        "为什么索引不是越多越好？",
        "索引可帮助检索，但也会增加维护成本；插入、更新和删除可能需要更新相关索引，并消耗存储。",
        "index",
    ),
    (
        "db-explain",
        "数据库原理",
        "PostgreSQL 的 EXPLAIN 用来做什么？",
        "EXPLAIN 展示规划器为语句选择的执行计划和估算；"
        "EXPLAIN ANALYZE 会实际执行语句并提供执行统计。",
        "explain",
    ),
    (
        "db-foreign",
        "数据库原理",
        "外键约束的主要作用是什么？",
        "外键要求引用列的值满足对被引用表键的引用约束；具体空值和删除、更新行为由定义决定。",
        "constraints",
    ),
    (
        "react-effect",
        "前端框架",
        "React 的 useEffect(fn, []) 一定只执行一次吗？",
        "空依赖使 effect 不因依赖变化重跑；开发环境启用 Strict Mode 时 React 会额外执行"
        "一次 setup/cleanup 检查，重新挂载也会重新执行。",
        "react",
    ),
    (
        "js-all",
        "前端语言",
        "Promise.all 中一个输入拒绝时会怎样？",
        "返回的 Promise 会以首个拒绝原因拒绝；其他已经开始的操作不会因此自动取消。",
        "js",
    ),
]

CARDS += [
    (
        "docker-image-container",
        "Docker",
        "Docker 镜像和容器有什么关系？",
        "镜像包含运行所需的文件与配置；容器是由镜像创建的隔离进程及其运行环境。",
        "docker-image",
    ),
    (
        "docker-image-layers",
        "Docker",
        "镜像层与容器可写层有什么区别？",
        "镜像由只读层组成；容器运行时有独立的可写层。修改容器可写层不会改变原镜像。",
        "docker-image",
    ),
    (
        "docker-volume-life",
        "Docker",
        "Docker 卷的生命周期是否等同于容器？",
        "卷独立于使用它的容器；删除容器不会自动删除命名卷，卷仍需按实际需求单独管理。",
        "docker-volume",
    ),
    (
        "docker-volume-share",
        "Docker",
        "多个容器能否挂载同一个卷？",
        "可以。多个容器可以同时挂载同一卷；应用仍需处理并发访问和数据一致性。",
        "docker-volume",
    ),
    (
        "docker-context",
        "Docker",
        "Docker 构建上下文是什么？",
        "构建上下文是构建时可供访问的一组文件，例如指定的本地目录；COPY 等指令可以使用其中的文件。",
        "docker-context",
    ),
    (
        "docker-ignore",
        "Docker",
        ".dockerignore 在构建时有什么作用？",
        "它按规则排除构建上下文中的文件，减少不必要的传输，也有助于避免无意把文件加入构建上下文。",
        "docker-context",
    ),
    (
        "docker-cache",
        "Docker",
        "Docker 构建缓存何时可复用层？",
        "当指令及其依赖的文件未发生影响缓存的变化时，构建器可复用缓存层；依赖变化会导致相关层重新构建。",
        "docker-cache",
    ),
    (
        "k8s-pod-unit",
        "Kubernetes",
        "Pod 在 Kubernetes 中是什么？",
        "Pod 是 Kubernetes 可创建和管理的最小可部署计算单元，包含一个或多个需要协同运行的容器。",
        "k8s-pod",
    ),
    (
        "k8s-pod-network",
        "Kubernetes",
        "同一 Pod 中的容器如何共享网络？",
        "同一 Pod 中的容器共享网络命名空间，包括 IP 地址和端口空间，"
        "通常可通过 localhost 相互通信。",
        "k8s-pod",
    ),
    (
        "k8s-deployment",
        "Kubernetes",
        "Deployment 的主要职责是什么？",
        "Deployment 声明式管理一组 Pod 的期望状态，"
        "通常通过 ReplicaSet 管理副本，并支持滚动更新和回滚。",
        "k8s-deployment",
    ),
    (
        "k8s-service",
        "Kubernetes",
        "为什么需要 Kubernetes Service？",
        "Pod 可能被替换且地址变化；Service 为一组网络端点提供稳定的访问抽象，常通过标签选择 Pod。",
        "k8s-service",
    ),
    (
        "k8s-configmap",
        "Kubernetes",
        "ConfigMap 适合存储什么？",
        "ConfigMap 用于保存非机密的配置数据，让配置与容器镜像解耦；"
        "机密数据应考虑 Secret 及额外保护措施。",
        "k8s-config",
    ),
    (
        "k8s-secret",
        "Kubernetes",
        "Kubernetes Secret 是否默认意味着已经安全加密？",
        "不应这样假定。Secret 用于保存敏感数据，但默认可能以未加密形式存入 etcd；"
        "需配置静态加密、访问控制等保护。",
        "k8s-secret",
    ),
    (
        "k8s-ready",
        "Kubernetes",
        "readiness 探针失败的主要影响是什么？",
        "Pod 被标记为未就绪，通常会从匹配 Service 的就绪端点中移除；探针成功后可重新接收流量。",
        "k8s-probe",
    ),
    (
        "k8s-live",
        "Kubernetes",
        "liveness 探针持续失败通常会触发什么？",
        "kubelet 会按配置判定容器不健康并重新启动该容器；它与是否接收流量的 readiness 检查不同。",
        "k8s-probe",
    ),
    (
        "k8s-startup",
        "Kubernetes",
        "startup 探针解决什么问题？",
        "它为启动较慢的容器提供启动检查；在 startup 探针成功前，"
        "liveness 和 readiness 探针不会开始。",
        "k8s-probe",
    ),
    (
        "git-fetch-review",
        "Git",
        "git fetch 与 git pull 的核心区别是什么？",
        "fetch 下载远端对象并更新相关引用，但不自动整合到当前分支；"
        "pull 通常先 fetch，再按配置整合。",
        "git-pull",
    ),
    (
        "git-revert-review",
        "Git",
        "git revert 和重写历史的操作有什么区别？",
        "revert 通常创建新提交来反向应用已有提交的变更，保留原提交；它可能需要解决冲突。",
        "git-revert",
    ),
    (
        "git-restore-review",
        "Git",
        "git restore 通常用于什么？",
        "restore 可从指定来源恢复工作区文件，或使用 --staged 恢复暂存区内容；"
        "使用前应核对将被覆盖的修改。",
        "git-restore",
    ),
    (
        "git-stage-review",
        "Git",
        "为什么 git status 同时显示暂存和未暂存修改？",
        "暂存区记录下一次提交准备使用的内容；文件在暂存后再次修改时，工作区与暂存区又会出现差异。",
        "git-status",
    ),
    (
        "git-prune-review",
        "Git",
        "git fetch --prune 的用途是什么？",
        "它根据远端配置的引用映射清理本地已不存在于远端的跟踪引用；具体受 refspec 和配置影响。",
        "git-fetch",
    ),
    (
        "sec-sqli",
        "应用安全",
        "参数化查询为什么有助于防止 SQL 注入？",
        "参数化查询将 SQL 结构与参数值分开处理，避免把用户输入当成 SQL 语法；"
        "动态表名等仍需单独约束。",
        "sql-injection",
    ),
    (
        "sec-xss",
        "应用安全",
        "防御 XSS 时为什么要考虑输出上下文？",
        "HTML 文本、属性、JavaScript 和 URL 等上下文的解析规则不同，"
        "应使用适合上下文的编码或安全 API。",
        "xss",
    ),
    (
        "sec-csrf",
        "应用安全",
        "CSRF 的典型风险是什么？",
        "攻击者诱导浏览器向用户已登录的站点发送非预期请求；浏览器可能自动附带凭据，服务端需验证请求意图。",
        "csrf",
    ),
    (
        "sec-csrf-token",
        "应用安全",
        "同步器模式中的 CSRF token 应具备哪些特征？",
        "令牌应与会话关联、不可预测并保密；服务端要检查请求显式提交的令牌，"
        "不能只依赖自动附带的 cookie。",
        "csrf",
    ),
    (
        "sec-ssrf",
        "应用安全",
        "SSRF 的核心风险是什么？",
        "攻击者影响服务器发出的请求，使服务器访问原本不应由该请求访问的内部或外部资源；防护需结合目标校验和网络限制。",
        "ssrf",
    ),
    (
        "sec-password",
        "应用安全",
        "为什么密码不应直接用快速通用哈希保存？",
        "密码通常熵较低；应使用适合密码存储的慢速、自带盐的密码哈希算法及适当参数，以提高离线猜测成本。",
        "password",
    ),
    (
        "sec-salt",
        "应用安全",
        "密码哈希中的盐有什么作用？",
        "为不同密码记录使用独特盐，可避免相同密码产生相同存储结果，并降低预计算表的复用价值；盐本身通常不必保密。",
        "password",
    ),
    (
        "sec-cors",
        "应用安全",
        "CORS 能否代替服务器的认证和授权？",
        "不能。CORS 是浏览器依据响应头实施的跨域访问机制；服务器仍须独立验证身份和访问权限。",
        "cors",
    ),
]


SOURCES.update(interview_knowledge_languages.SOURCES)
CARDS.extend(interview_knowledge_languages.CARDS)


def cards():
    return [
        {
            "id": key,
            "topic": topic,
            "question": question,
            "answer": answer,
            "source_url": SOURCES[source],
        }
        for key, topic, question, answer, source in CARDS
    ]
