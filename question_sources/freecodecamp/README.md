# freeCodeCamp Developer Quiz Site（候选，待核验）

- 来源：[freeCodeCamp/Developer_Quiz_Site](https://github.com/freeCodeCamp/Developer_Quiz_Site)，固定提交 `d71a60ba08113e8f999aeaa8c75c7d16aef1dbc0`。
- 原作者及版权：Copyright (c) 2023, freeCodeCamp。BSD 3-Clause，完整条件及免责声明见 `LICENSE.md`。
- 保存 17 个主题的原始题目文件，包括计算机基础、Linux、SQL、Python、JavaScript、Git、安全、云计算等；未经审阅的内容及链接仅为候选来源。
- 每题在进入正式题库前须核对题干、唯一答案、版本和来源，并保留具体依据；不能以有许可证或源站答案作为正确性的证明。
- 抽查发现计算机基础文件中存在值得修正的题目：二叉树索引查询的 O(log n) 答案未限定平衡条件；Ford–Fulkerson 的 O(VE²) 答案需限定使用 BFS 的 Edmonds–Karp 实现；另有最短路径题同时列出优先队列和堆，选项可能不互斥。这些题在消除歧义之前不得进入评分。
- 静态解析发现 13 道题选项完全重复、1 道字段不完整；人工审阅还发现 Python 题 6 的 XOR 解析误写为 NOT、题 9 的字符串拼接答案多了空格、安全题 4 的 SSH 与 Telnet 均可用于远程登录，均未导入。
- `reviewed_manifest.json` 固定逐题审阅后选入的 261 道计算机基础、网络安全、云计算、DevOps、Python、JavaScript、Git、Linux、CSS、HTML、SQL 题的原文指纹及参考依据。应用保留英文原题、原解释、原项目链接与许可证说明；原始文件或选项变化须重新审阅，不能仅更新指纹。Python 题 38 的解析声称只能用 set() 创建空集合，表述过于绝对；Linux 题 15 将口令哈希描述成加密、题 55 声称文件所有者可以任意更改所有权；HTML 题 44 使用 src 而非 href 引入样式表；SQL 题 37 将 MySQL 的 EXPLAIN 表名列为错误选项；计算机基础题 12 将 Kruskal 算法列为最短路径算法，题 21 忽略 Bellman–Ford 也能检测负权环；安全题 21 将加密混同于不可否认性；云计算题 18 声称无服务器计算抽象应用代码。这些题均已排除。
