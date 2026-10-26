# V0 依赖与许可证处理

`requirements.lock` 是 Linux 开发环境的精确安装版本，`scripts/dependency_report.py` 生成本地安装 SBOM、许可证清单和 PyPI 漏洞报告。扫描只发送包名/版本查询公开漏洞，报告仅在系统临时目录生成，用完自动删除；CI 只检查，不配置外部报告上传。

主工程是用户拥有的私人代码，本轮不擅自为其选择开源许可证；清单内 first-party `careerpilot: UNKNOWN` 按此识别。依赖保留发行包自带 dist-info/licenses，不删改原始许可。

当前清单中的需要额外留意项：certifi/pathspec 使用 MPL-2.0；psycopg/psycopg-binary 使用 LGPL-3.0-only（仅 PostgreSQL 兼容测试/后续可选驱动）；不修改其源码、不静态合并到自有模块。分发镜像/修改这些包时，需按相应上游许可证提供通知/源码可得性等义务，当前开发镜像仅本地使用，不由工程测试推断法律批准。其余依赖按生成的完整清单逐项可查。

Advisory scan 的报告不长期留存，必要时重新扫描。已知漏洞会使脚本非零退出，进而阻断 CI；不得静默忽略或仅改变阈值。新依赖引入 UNKNOWN、强 copyleft 或非商业限制时需登记并在集成前复核，不能用“已锁版本”替代许可/漏洞检查。

MyCareer 的安装/许可/风险清单仅临时生成并在检查完成后删除；主工程没有导入其运行时，也没有复制其源码。具体 No-Go 见 ADR-003。
