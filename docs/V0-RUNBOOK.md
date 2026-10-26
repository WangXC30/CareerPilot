# V0 运维、迁移与回滚手册

## 启动与检查

```powershell
docker compose up -d --build --wait api worker
docker compose exec api python -m careerpilot.seed
docker compose run --rm --no-deps checks
docker compose exec api python -m careerpilot.cli --name developer
docker compose exec api python -m careerpilot.cli --name operator --role operator
```

CLI 仅显示一次新凭据，不把输出保存到仓库/共享日志。浏览器访问 `/app/`，连接账号后可操作真实目标草稿、隐私同意；运营账号可查来源与聚合指标，不能查其他用户私有对象。

`POST /api/v1/agent-tasks` 使用 `Idempotency-Key` 和 `{"kind":"baseline.validate"}` 返回 202；按返回 ID 查询进度。worker 通过 lease_token 抢占，过期租约可恢复；同一事件至少一次投递由 InboxReceipt 去重。取消需 If-Match，后台在 checkpoint 和提交前再次检查。失败重试按指数退避，三次耗尽后 failed 是死信；仅本人可显式 `/retry`，取消项不能重放。

## 故障定位

`docker compose ps -a` 检查 migrate/api/worker。`/health/live` 是进程存活，`/health/ready` 是期望迁移版本。worker heartbeat 过期表示内部 worker 故障；来源错误在运营台另列，不将外部不可用混入内部可用性指标。

统一错误结构含 code/message/details/trace_id/retryable；客户端 X-Trace-ID 可关联任务、Outbox、状态审计和模型用量。禁止记录异常原始内容。运营台只显示错误类、重试次数、责任模块和汇总指标。

## 数据备份与恢复演练

在宿主机建立私人输出目录并挂载到工具容器（独立目的目录，避免覆盖源码只读挂载）：

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
docker compose run --rm --no-deps -v "${PWD}/backups:/output" migrate python scripts/backup_restore.py /var/lib/careerpilot/careerpilot.db /output/snapshot-001.db
docker compose run --rm --no-deps checks python scripts/operations_drill.py
```

快照使用 SQLite backup API，可得到一致快照，输出拒绝覆盖。恢复生产路径前停止 `api worker`，再恢复到一个新数据库路径、校验 integrity 与迁移版本，确认私有记录和认证可读后切换路径；保留切换前快照用于回滚，不在运行写入时覆盖数据库。快照文件应仅由当前用户访问；正式环境需要加密和独立备份存储。

V0 演练在临时合成数据库上完成快照→新路径恢复→完整性→认证和业务记录检查；性能脚本报告 100 次本地 TestClient 请求的 p50/p95，不能解释为公网 SLA。正式目标提案为每日快照 + 发布前快照，RPO ≤24 小时、RTO ≤1 小时；尚需部署方评审。演练实际耗时见 operations-v0.json。

## 本地技术验证

```powershell
docker compose run --rm --no-deps checks
docker compose run --rm --no-deps checks python scripts/dependency_report.py
```

测试截图、报告和日志仅允许临时生成，使用结束后删除；脚本使用系统临时目录并自动清理，不挂载 docs 或 artifacts 输出目录。真实数据备份仍按上文规则保留。

维护元数据执行 `docker compose exec api python scripts/maintenance.py`，默认清理 30 天前请求指标与一天前来源分钟配额，不清理业务/状态审计。

## 可选密钥文件注入

外部密钥未配置时，V0 默认无外部模型调用。若后续需 SecretVault，先在私人 `.secrets/vault.key` 放置 Fernet 密钥，再使用 `docker compose -f compose.yaml -f infra/compose.secrets.yaml up -d api worker`；该文件由 Compose 只读注入，不进入镜像或版本控制。挂载文件需允许容器 UID 10001 读取，轮换时保留旧密文解密迁移方案，不直接覆盖仍被旧密文引用的密钥。

候选底座 Spike 可通过 `docker compose -p careerpilot-reference-v0 -f infra/compose.reference-audit.yaml run --rm reference-audit` 重跑；可用 MYCAREER_PATH 指定只读来源。原项目不被修改，原测试与 PoC 在临时目录运行，使用结束后自动删除产物。

## 版本升级与回滚

1. 备份数据库，停止 API/worker；构建新镜像并保留旧镜像 ID。
2. 执行 `docker compose run --rm migrate`；成功后 `up -d --wait api worker`，再执行 checks。
3. 新版失败且未写入新格式数据时，可停止写入、按所需 revision 执行 downgrade 并切回旧镜像；有新数据时优先从快照恢复到新路径并审查差异。
4. 0002→0001 会删除平台任务/授权/运营表；仅测试库可直接降级，不把破坏性 downgrade 当常规生产回滚。旧版主身份/目标表保留。

## PostgreSQL 演进

V0 默认 SQLite 单机；PostgreSQL 合同使用完全临时库：

```powershell
docker compose --project-directory . -p careerpilot-pg-v0 -f infra/compose.postgres.yaml up --abort-on-container-exit --exit-code-from postgres-check
docker compose --project-directory . -p careerpilot-pg-v0 -f infra/compose.postgres.yaml down
```

临时测试密码只用于无宿主端口、tmpfs 的合成测试库，不可复用于正式环境。正式切换需要停写快照、导出/导入保持 UUID、同用户复合键与 UTC 字段、比对行数/所有权/版本及事件收据、重建索引，再从只读验证到切换；失败返回原 SQLite 快照。迁移结构跨库通过不等于已完成真实用户数据迁移。
