# 本机备份与运维告警

- 每日北京时间 03:00–03:05 执行备份；启动错过的任务会补执行。
- 数据库和附件备份期间暂停 API、通知 worker 与聊天 worker，完成或失败后恢复原先运行的服务。
- 目录 `/opt/sapdesk/backups/daily/<UTC时间>`，包含数据库归档、附件归档、配置归档、SHA256SUMS 和完成标记。root 专用权限。
- 仅成功完成的每日备份保留 14 天。升级备份、手动备份、不完整备份不自动删除。
- 配置归档含密码和证书私钥，只能在本机 root 权限下使用；不要发送到聊天或普通文件共享。
- 每分钟检查：HTTPS/API/数据库健康、五个容器、磁盘、内存、CPU、备份时效、备份失败、证书临期与续期失败。
- Codex 认证卷独立持久化，未纳入每日配置归档；灾难恢复后需重新登录。不要删除 `sapdesk_codex-auth` 卷。
- CPU/内存达到 85% 且连续 5 次，服务连续 3 次失败，磁盘达到 80%，备份超过 30 小时未成功，证书不足 48 小时触发提醒。
- 相同未恢复告警最多每小时重复一次；新增/恢复情况会更新通知。钉钉接口接受不等于用户已读。
- 管理员机器人单独存于 `/etc/sapdesk-ops.env`，root:root 0600：`OPS_DINGTALK_TOKEN` 和 `OPS_DINGTALK_SECRET`；不复用顾问群。
- 测试渠道：使用 systemd 的 EnvironmentFile 启动 `python3 /opt/sapdesk/scripts/ops.py test-alert`，不要在命令行参数中传密钥。
- 查看任务：`systemctl list-timers 'sapdesk-*'`；`journalctl -u sapdesk-backup -u sapdesk-monitor`。
- 主机断电、网络全断时本机无法发送提醒；需要独立外部监测才能覆盖。这次未购买 COS，无异地副本。

## 恢复步骤（由管理员明确授权后执行）

1. 选择带 `manifest.json` 的完成备份，进入目录执行 `sha256sum -c SHA256SUMS`。
2. 为当前数据另做备份并暂停 API/worker。不要直接覆盖仍在使用的数据。
3. 在隔离 PostgreSQL 实例或新数据库中用 `pg_restore` 恢复 `database.dump`，验证表与数据后再切换；不要在未经确认的生产库执行清空操作。
4. 将 `attachments.tar.gz` 恢复到空的附件卷，保留容器应用 uid/gid 10001 的可读写权限。
5. 如需重建服务器，从 `configuration.tar.gz` 恢复配置并检查证书有效期；不得在不同环境直接暴露原密钥。
6. 启动服务，验证登录、工单、附件及健康接口。

发布验证包括归档目录可读、附件归档完整读取和校验和验证；不等同于已经进行过完整灾难恢复演练。

## 上线后同步 GitHub

每次新功能取得针对本次最终内容的发布确认、完成部署并通过上线验证后，必须同步更新 [项目 GitHub 仓库](https://github.com/stillvictoria533-ui/sap-work-management)。这是同次发布的必做收尾步骤，无需对已授权上线内容的同步重复确认；上线前的内容确认要求仍按根目录 [AGENTS.md](../AGENTS.md) 执行。

1. 整理与本次实际上线版本一致的源代码、测试、配置示例和相关文档，提交并推送到对应发布分支（当前为 `origin/main`）。工作区有其他未发布改动时，按文件或差异块选择本次内容，保留其他修改，不将本地项目快照等无关内容一并上传。
2. 遵守 `.gitignore`，不得提交真实环境配置、密码、密钥、证书私钥、数据库、附件、备份或运行数据。
3. 推送后读取 GitHub 远端分支，确认包含本次上线对应的提交；仅凭本地 `origin/main` 缓存或执行过推送命令不能宣称同步成功。
4. 在本次发布记录和交付说明中记录上线内容、上线时间（北京时间）、验证结果、GitHub 分支、提交号及同步结果。若同步失败，明确报告“已上线，GitHub 同步未完成”及原因，后续补齐后再标记发布收尾完成。

最近同步记录：[2026-10-27 已上线版本同步 GitHub](reviews/github-sync-20261027/README.md)。

后续发布记录：[2026-10-27 优先级修复上线与 GitHub 同步](reviews/audit-priority-fixes/README.md)，上线代码 `c1c654a`，服务器隔离 PostgreSQL 171 项回归与生产冒烟通过。

最新发布记录：[2026-10-27 复核跟进修复上线与 GitHub 同步](reviews/audit-followup-fixes/README.md)，上线代码 `ad0388d`，服务器隔离 PostgreSQL 186 项回归与生产冒烟通过。
