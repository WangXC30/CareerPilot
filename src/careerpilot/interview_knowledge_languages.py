"""Original interview cards scoped to documented language and database behaviour."""

SOURCES = {
    "java-object": "https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Object.html",
    "java-exceptions": "https://docs.oracle.com/javase/tutorial/essential/exceptions/catchOrDeclare.html",
    "java-resource": "https://docs.oracle.com/javase/tutorial/essential/exceptions/tryResourceClose.html",
    "java-override": "https://docs.oracle.com/javase/tutorial/java/IandI/override.html",
    "java-generics": "https://docs.oracle.com/javase/tutorial/java/generics/erasure.html",
    "java-collection": "https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/Collection.html",
    "java-map": "https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/Map.html",
    "java-thread": "https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Thread.html",
    "java-string": "https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/String.html",
    "go-spec": "https://go.dev/ref/spec",
    "go-memory": "https://go.dev/ref/mem",
    "go-context": "https://pkg.go.dev/context",
    "go-sync": "https://pkg.go.dev/sync",
    "go-errors": "https://pkg.go.dev/errors",
    "pg-mvcc": "https://www.postgresql.org/docs/18/mvcc-intro.html",
    "pg-isolation": "https://www.postgresql.org/docs/18/transaction-iso.html",
    "pg-index": "https://www.postgresql.org/docs/18/indexes-multicolumn.html",
    "pg-locks": "https://www.postgresql.org/docs/18/explicit-locking.html",
    "pg-vacuum": "https://www.postgresql.org/docs/18/routine-vacuuming.html",
    "pg-null": "https://www.postgresql.org/docs/18/functions-comparison.html",
    "pg-join": "https://www.postgresql.org/docs/18/queries-table-expressions.html",
    "pg-aggregate": "https://www.postgresql.org/docs/18/functions-aggregate.html",
    "py-bisect": "https://docs.python.org/3/library/bisect.html",
    "py-heap": "https://docs.python.org/3/library/heapq.html",
    "py-deque": "https://docs.python.org/3/library/collections.html#collections.deque",
    "bfs": "https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-spring-2020/6afd7e9a85d7d36e204533569f9fccf6_MIT6_006S20_r09.pdf",
    "py-iter": "https://docs.python.org/3/library/stdtypes.html#iterator-types",
    "py-generator": "https://docs.python.org/3/reference/expressions.html#yield-expressions",
    "py-with": "https://docs.python.org/3/reference/compound_stmts.html#the-with-statement",
    "py-scope": "https://docs.python.org/3/reference/executionmodel.html#resolution-of-names",
    "py-mutable": "https://docs.python.org/3/tutorial/controlflow.html#default-argument-values",
    "js-closure": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Closures",
    "js-microtask": "https://developer.mozilla.org/en-US/docs/Web/API/HTML_DOM_API/Microtask_guide",
    "js-equality": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/Strict_equality",
    "js-allsettled": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise/allSettled",
    "js-array": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Array/map",
    "js-scope": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/let",
    "js-object": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Object/freeze",
}

# Entries are separately authored; source links identify the behaviour and version being reviewed.
CARDS = [
    (
        "java-equals",
        "Java",
        "Java 的 equals 与 == 对引用类型有何区别？",
        "对于引用类型，== 比较引用是否指向同一对象；equals "
        "的语义由类实现，Object 的默认实现也是引用相等。",
        "java-object",
    ),
    (
        "java-hash",
        "Java",
        "Java 的 equals 和 hashCode 必须满足什么关系？",
        "根据 Object 契约，两个对象若由 equals 判定相"
        "等，其 hashCode 必须相同；哈希相同不保证对象相等。",
        "java-object",
    ),
    (
        "java-checked",
        "Java",
        "Java 受检异常的捕获或声明要求是什么？",
        "可能抛出受检异常的代码需要捕获它，或在方法 throws 子"
        "句中声明；未受检异常不受同一编译要求约束。",
        "java-exceptions",
    ),
    (
        "java-resource",
        "Java",
        "try-with-resources 何时关闭资源？",
        "在语句结束时自动关闭声明的 AutoCloseable 资源，即使代码块因异常退出也会尝试关闭。",
        "java-resource",
    ),
    (
        "java-overrides",
        "Java",
        "Java 实例方法覆盖与静态方法隐藏有什么区别？",
        "子类兼容的实例方法可覆盖父类实例方法；同签名静态方法则是隐藏"
        "，不能把它当作实例方法的动态覆盖。",
        "java-override",
    ),
    (
        "java-erasure",
        "Java",
        "Java 泛型的类型擦除主要做什么？",
        "编译器将类型参数替换为边界类型或 Object，按需插入类型"
        "转换，并可能生成桥接方法；泛型检查仍在编译期发挥作用。",
        "java-generics",
    ),
    (
        "java-map-contains",
        "Java",
        "Java Map 中 get(key) 返回 null 一定表示键不存在吗？",
        "不一定。允许 null 值的 Map 可能将该键映射到 nu"
        "ll；可用 containsKey 区分不存在和映射值为 n"
        "ull。",
        "java-map",
    ),
    (
        "java-string",
        "Java",
        "Java String 对象是否可变？",
        "String 的值创建后不可改变；看似修改字符串的方法返回结果字符串，原 String 对象保持不变。",
        "java-string",
    ),
    (
        "java-interrupt",
        "Java",
        "Java 线程 interrupt 是否保证立即强行终止线程？",
        "不保证。interrupt 发送中断信号；被中断线程应根据阻塞方法及中断状态协作处理。",
        "java-thread",
    ),
    (
        "java-collection",
        "Java",
        "Java Collection 的可选操作意味着什么？",
        "某些实现不支持修改等可选操作，调用时可抛出 Unsuppor"
        "tedOperationException；应以具体实现契约"
        "为准。",
        "java-collection",
    ),
    (
        "go-slice",
        "Go",
        "Go 中 slice 与底层数组有什么关系？",
        "slice 描述底层数组的一段；多个 slice 可能共享同一底层数组，对共享元素的修改可能相互可见。",
        "go-spec",
    ),
    (
        "go-append",
        "Go",
        "为什么 Go 的 append 结果通常需要重新赋值？",
        "append 返回更新后的 slice；容量不足时可能分配新底层数组，因此应使用返回值。",
        "go-spec",
    ),
    (
        "go-nil-map",
        "Go",
        "对 nil map 读取和写入分别会怎样？",
        "读取不存在的键会得到值类型的零值；向 nil map 写入会触发运行时 panic。",
        "go-spec",
    ),
    (
        "go-map-presence",
        "Go",
        "Go 如何区分 map 中键不存在与值恰好为零值？",
        "使用 value, ok := m[key]；ok 表明该键是否存在。",
        "go-spec",
    ),
    (
        "go-channel",
        "Go",
        "无缓冲 channel 的发送何时完成？",
        "无缓冲 channel 的发送与对应接收同步，发送需要接收方"
        "准备好；nil channel 的发送和接收会永久阻塞。",
        "go-spec",
    ),
    (
        "go-close",
        "Go",
        "向已关闭的 channel 发送会怎样？",
        "发送到已关闭的 channel 会触发 panic；关闭且缓"
        "冲区已耗尽后接收会立即返回元素类型零值及 ok=false。",
        "go-spec",
    ),
    (
        "go-select",
        "Go",
        "select 有多个可立即执行的通信分支时如何选择？",
        "Go 规范规定从可执行通信中以均匀伪随机方式选择一个；不应依赖源码顺序决定结果。",
        "go-spec",
    ),
    (
        "go-defer",
        "Go",
        "同一函数中多个 defer 的执行顺序是什么？",
        "defer 的调用参数在执行 defer 语句时求值；延迟调用在函数返回前按后进先出顺序执行。",
        "go-spec",
    ),
    (
        "go-context",
        "Go",
        "context.Context 主要传递什么？",
        "它跨 API 边界传播取消信号、截止时间及请求范围的值；取消"
        "函数应在不再需要时调用以释放相关资源。",
        "go-context",
    ),
    (
        "go-mutex",
        "Go",
        "sync.Mutex 能否在首次使用后复制？",
        "不能。官方契约要求 Mutex 在首次使用后不得复制；并发共享时应确保各使用方操作同一个锁。",
        "go-sync",
    ),
    (
        "go-errors-is",
        "Go",
        "errors.Is 与直接比较错误值的区别是什么？",
        "errors.Is 沿错误包装树检查目标错误，并支持错误类型"
        "自定义 Is 方法；直接比较只比较当前错误值。",
        "go-errors",
    ),
    (
        "go-happens-before",
        "Go",
        "数据竞争会对 Go 程序正确性产生什么影响？",
        "没有适当同步的并发读写可能构成数据竞争；应使用 channe"
        "l、锁或其他同步机制建立必要的先行关系。",
        "go-memory",
    ),
    (
        "pg-mvcc",
        "数据库原理",
        "PostgreSQL MVCC 的核心作用是什么？",
        "通过数据版本和快照处理并发访问，使语句看到一致视图，减少普通读取与写入间的锁冲突。",
        "pg-mvcc",
    ),
    (
        "pg-rr",
        "数据库原理",
        "PostgreSQL Repeatable Read 使用什么快照？",
        "事务中的普通查询通常使用事务首次非事务控制语句开始时建立的快"
        "照；并发更新冲突可能导致事务需重试。",
        "pg-isolation",
    ),
    (
        "pg-leftmost",
        "数据库原理",
        "PostgreSQL 多列 B-tree 索引一定不能用于非首列条件吗？",
        "不能这样绝对化。多列 B-tree 可用于任意子集条件，但对"
        "前导列有约束通常更高效；是否使用由规划器决定。",
        "pg-index",
    ),
    (
        "pg-deadlock",
        "数据库原理",
        "PostgreSQL 检测到死锁后通常如何处理？",
        "数据库会检测死锁并中止其中一个事务以打破循环；应用应准备处理错误并按业务规则重试。",
        "pg-locks",
    ),
    (
        "pg-vacuum",
        "数据库原理",
        "为什么 PostgreSQL 需要 VACUUM？",
        "VACUUM 回收可复用的过期行版本空间，并承担防止事务 I"
        "D 回卷等维护工作；不能简单理解成每次都缩小文件。",
        "pg-vacuum",
    ),
    (
        "pg-null-eq",
        "数据库原理",
        "在 PostgreSQL 中为什么不能用 = NULL 判断空值？",
        "NULL 表示未知，普通相等比较得到未知；判断空值使用 IS"
        " NULL，空值可比较相等语义可使用 IS NOT DIST"
        "INCT FROM。",
        "pg-null",
    ),
    (
        "pg-left-filter",
        "数据库原理",
        "LEFT JOIN 后在 WHERE 中限制右表字段可能有什么效果？",
        "未匹配行的右表字段为 NULL，WHERE 条件若不为真会把"
        "这些行过滤掉；过滤条件放在 ON 中的语义不同。",
        "pg-join",
    ),
    (
        "pg-count-null",
        "数据库原理",
        "COUNT(*) 与 COUNT(column) 的区别是什么？",
        "COUNT(*) 统计输入行数；COUNT(column) 只统计该表达式非 NULL 的行。",
        "pg-aggregate",
    ),
    (
        "algo-bisect",
        "数据结构与算法",
        "对有序列表使用二分查找需要注意什么前提？",
        "比较顺序必须与列表排序一致；二分能定位插入点，但 Pytho"
        "n 列表中插入元素仍可能需要线性移动。",
        "py-bisect",
    ),
    (
        "algo-heap",
        "数据结构与算法",
        "Python heapq 默认堆顶保存什么？",
        "heapq 使用最小堆，heap[0] 是最小元素；堆只保证堆序，不意味着内部列表整体有序。",
        "py-heap",
    ),
    (
        "algo-deque",
        "数据结构与算法",
        "Python deque 与列表相比在哪类操作上有优势？",
        "deque 支持在两端高效添加和弹出；列表头部插入或删除通常需要移动后续元素。",
        "py-deque",
    ),
    (
        "algo-bfs",
        "数据结构与算法",
        "无权图中按边数求最短路径常用什么遍历？",
        "广度优先搜索按层访问顶点；在边权相同且以边数衡量路径长度时，首次发现目标对应最少边数。",
        "bfs",
    ),
    (
        "algo-stack",
        "数据结构与算法",
        "栈和队列的取出顺序有什么不同？",
        "栈通常后进先出；队列通常先进先出。具体实现可用不同容器，但需要保持对应操作语义。",
        "py-deque",
    ),
    (
        "py-iterator",
        "Python",
        "Python 迭代器如何表示耗尽？",
        "迭代器通过 __next__ 返回下一项；没有更多项时抛出 StopIteration，for 循环会处理该信号。",
        "py-iter",
    ),
    (
        "py-generator",
        "Python",
        "Python 生成器的 yield 有什么作用？",
        "yield 暂停生成器并向调用方产生值；后续恢复执行时保留局部状态，从暂停处继续。",
        "py-generator",
    ),
    (
        "py-with",
        "Python",
        "with 语句为何适合管理文件等资源？",
        "上下文管理器在进入和退出代码块时执行相应方法；即使块中发生异常，也会调用退出逻辑。",
        "py-with",
    ),
    (
        "py-scope",
        "Python",
        "Python 闭包中的自由变量在哪里解析？",
        "名称解析遵循语言定义的作用域规则，内部函数可引用外层函数作用"
        "域中的绑定；读取发生在执行时。",
        "py-scope",
    ),
    (
        "py-default",
        "Python",
        "为什么可变对象作为函数默认参数容易产生意外共享？",
        "默认参数表达式在函数定义时求值一次；多次调用省略该参数时可能复用同一个可变对象。",
        "py-mutable",
    ),
    (
        "js-closure-review",
        "JavaScript",
        "JavaScript 闭包是什么？",
        "函数与其词法环境共同形成闭包，使函数能够访问定义时所在作用域的变量绑定。",
        "js-closure",
    ),
    (
        "js-microtask-review",
        "JavaScript",
        "浏览器中的 Promise 回调通常何时执行？",
        "Promise 回调作为微任务排队，在当前执行栈清空后、事件"
        "循环进入后续任务前运行；不能简单等同于同步调用。",
        "js-microtask",
    ),
    (
        "js-strict-eq",
        "JavaScript",
        "JavaScript 的 === 是否会像 == 一样先做类型转换？",
        "严格相等比较不会对不同类型做隐式转换；例如 1 === '1' 为 false。",
        "js-equality",
    ),
    (
        "js-allsettled",
        "JavaScript",
        "Promise.allSettled 与 Promise.all 对失败的处理有何不同？",
        "allSettled 等所有输入结束并分别报告 fulfil"
        "led 或 rejected；all 在任一输入拒绝时返回的"
        " Promise 会拒绝。",
        "js-allsettled",
    ),
    (
        "js-map-review",
        "JavaScript",
        "Array.prototype.map 会原地替换原数组吗？",
        "map 返回由回调结果组成的新数组，不会仅因调用 map 就替换原数组；回调本身仍可能产生副作用。",
        "js-array",
    ),
    (
        "js-let-review",
        "JavaScript",
        "let 声明变量在声明前访问会怎样？",
        "块级 let 绑定在初始化前处于暂时性死区；在此期间访问会抛出 ReferenceError。",
        "js-scope",
    ),
    (
        "js-freeze",
        "JavaScript",
        "Object.freeze 是否递归冻结所有嵌套对象？",
        "不会。Object.freeze 是浅冻结；嵌套对象若未单独冻结，仍可能被修改。",
        "js-object",
    ),
]
