# Docker Compose 统一开发环境（架构 ADR-011；历史文件名保留）

- 日期：2026-10-19，Asia/Shanghai
- 状态：按用户要求实施；仅开发环境，不是生产部署批准
- 关联：CP-003、CP-010；补充 ADR-001 的本地运行方式

将宿主机 Python/venv 默认工作流替换为 Docker Compose。保留现有 SQLite 数据模型，避免同时引入 PostgreSQL 迁移范围；Python 3.14 与测试依赖安装进同一开发镜像，API 和检查使用相同环境。

`migrate` 单次运行，成功后才启动 `api`；`api` 只在宿主机 loopback 发布，使用非 root 用户、只读源码挂载与热重载。开发数据使用命名卷；`checks` 按需启动，不挂载开发数据库。开发镜像含测试依赖，不作为最小生产镜像发布。

原宿主机数据库和虚拟环境不删除。旧 SQLite 可以在第一次启动前由 backup API 导入，目标存在即拒绝覆盖。正常 `down` 保留数据；删除卷不属于常规重启操作。

源码只读要求需求追踪提供 `--check`；pytest/Ruff 不向挂载目录写缓存。CI 迁移到 Linux runner，调用相同 Compose 检查并验证健康启动。

现有限制：Python 基础镜像标签会更新，后续供应链治理可固定 digest；锁文件来自首批开发环境，必须通过 Linux 镜像安装和实际测试验证。生产数据库、Worker、密钥托管和备份恢复演练仍按后续任务实施。
