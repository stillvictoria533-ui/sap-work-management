# SAP Desk · 需求协作空间

第三版界面的正式实现：Vue 3 + TypeScript + Vite，Django 5.2 LTS + DRF，生产使用 PostgreSQL。首页保留滚动叙事，登录后进入简洁工单工作台。

## 已实现

- 管理员创建、编辑、停用、启用账号；随机初始/重置密码、首次登录改密、角色权限与会话失效。
- 顾问提交工单、只查看/回复自己的工单；管理员查看全部、处理状态、导出当前筛选结果。
- 标题、模块、类型、优先级、期望日期、描述、附件；状态流程和关闭后的只读限制。
- 原始需求保留，回复与状态变化分别记录操作人和时间；提交使用幂等键，回复使用版本冲突检测。
- 组合筛选、编号/标题/描述/沟通内容搜索、分页、CSV 工单和沟通记录导出。
- 开发日历：支持“提交记录／预计完成”切换，按北京时间的首次提交日期或开发预计完成日期查看工单，包含已完成工单；管理员查看全部并维护排期，顾问仅查看自己工单的日期和计划。详见 [发布与验证记录](docs/reviews/calendar-visibility/README.md)。
- 开发预计完成日期：管理员受理时选填或详情维护，改期／清空需原因；时间线与通知、逾期提示、列表筛选排序及 CSV 导出。退回或重开时提示重新评估，已完成工单只读。详见 [本地审阅说明](docs/reviews/development-schedule/README.md)。
- 工单 AI 多轮跟进（已上线，约每分钟检查）：管理员受理后首轮回复，顾问补充后继续分析；独立聊天入口已退出导航，执行权限改造已于 2026-10-26 发布，见 [工单 AI 说明](docs/AI_TICKET_AUTOMATION.md)。
- 原独立 AI 聊天入口已退出导航，历史会话保留；当前按工单时间线进行 AI 跟进，见 [工单 AI 说明](docs/AI_TICKET_AUTOMATION.md)。
- 站内未读提醒；协作群机器人定向 @ 管理员 / 顾问。
- 附件私有存储、权限下载、扩展名与内容校验、每次最多 5 个/每个 20MB。
- Docker Compose、Nginx HTTPS、日志轮转、数据库迁移、备份脚本与恢复步骤。

钉钉通道默认关闭。没有群机器人配置与可访问的 HTTPS 地址时，通知队列显示“待配置/受阻”，不伪装发送成功。站内提醒仍可使用。

## 本地启动

需要 Python 3.14、Node.js 24。首次设置：

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
python3 scripts/init_dev.py
.venv/bin/python backend/manage.py migrate
npm --prefix frontend ci
```

创建真实本地管理员，密码在终端交互输入，不在命令行传递：

```sh
.venv/bin/python backend/manage.py bootstrap_admin --email your-email@example.com --name 管理员
```

仅当本地数据库为空且想使用验收账号时，可改用：

```sh
.venv/bin/python backend/manage.py local_preview_accounts
```

此命令仅支持 DEBUG + SQLite，创建本地管理员和验收顾问，随机密码写入 `.local-credentials.txt`（权限 0600、已忽略），不写进代码。已有账号时不会覆盖。不要将本地数据库和验收账号复制到生产。

分别打开三个终端：

```sh
.venv/bin/python backend/manage.py runserver 127.0.0.1:8000
```

```sh
npm --prefix frontend run dev
```

```sh
.venv/bin/python backend/manage.py notify_worker
```

访问 http://127.0.0.1:5173/ 。Vite 将 `/api` 同域代理至 Django。开发使用 SQLite 便于本地运行；正式数据库通过环境变量切换到 PostgreSQL。

## 业务规则

1. 顾问提交后状态为待受理；管理员受理后进入处理中。
2. 管理员将处理中工单提交验收后进入待验收；顾问回复只追加沟通，不暗中改变业务状态。
3. 顾问可对自己的待验收工单确认完成，或说明原因退回处理中；管理员可填写说明直接完成任意未完成工单。
4. 已完成即归档，不存在额外“关闭”状态；管理员及工单提交人均可填写原因重新打开为处理中。已完成时不可回复或修改排期。
5. 工单创建者、初始状态和时间由服务器确定，提交后的原始描述不覆盖，纠正通过追加回复留痕。
6. 账号停用、角色改变、密码重置使旧登录失效；用户改密码后仅当前登录保留。
7. 管理员不能停用自己或修改自己的角色；至少保留一个启用的管理员。
8. 当前登录用户决定可读范围，前端隐藏入口不替代接口鉴权。通知和附件也使用工单归属检查。

状态动作以服务端 `backend/desk/services.py` 的 `ACTIONS` 为准：

| 当前状态 | 管理员可执行的状态动作 | 本工单顾问可执行的状态动作 |
| --- | --- | --- |
| 待受理 | 开始处理、直接完成（需说明） | 无 |
| 处理中 | 提交验收（需说明）、直接完成（需说明） | 无 |
| 待验收 | 直接完成（需说明） | 确认完成、仍需处理（需说明） |
| 已完成 | 重新打开（需说明） | 重新打开（需说明） |

回复、排期、加急与状态动作独立。新建工单的幂等键同时绑定规范化字段及附件内容；相同键且内容相同返回原结果，内容不同返回 409 并保留草稿。缺少指纹的旧工单重试会提示打开原工单补充，避免猜测原始提交内容。详情每 30 秒检查版本，有更新时提示刷新，刷新保留当前草稿。

| 目录 | 维护范围 |
| --- | --- |
| `backend/desk/`、`frontend/`、`scripts/ai_ticket*` | 当前工单业务与受控 AI 跟进 |
| `backend/assistant/` | 历史独立聊天兼容接口，仅管理员可用，provider 默认关闭；按每次入队时间计入滚动 24 小时额度 |
| `connector/`、`scripts/windows/` | 连接器试点及离线协议验证，未接入现行工单 AI 执行链路 |
| `design-v3/` | 设计参考，不参与生产构建 |

2026-10-27 优先级修复已上线，范围、PostgreSQL 回归、生产冒烟及 GitHub 同步证据见 [修复与发布记录](docs/reviews/audit-priority-fixes/README.md)。该记录明确区分已验证修复和后续容量、监控、灾备建议。

基于 `22c3987` 复核反馈的跟进修复（业务键组合、日期草稿基线、新建页迟到响应、列表类型及版本查询）已上线，见 [跟进修复发布记录](docs/reviews/audit-followup-fixes/README.md)。

## 通知规则与钉钉配置

| 事件 | 站内提醒 | 同一钉钉协作群 |
| --- | --- | --- |
| 顾问提交或补充工单 | 所有启用的管理员 | @ 启用且非操作者的管理员 |
| 管理员回复/变更状态 | 工单提交人 | @ 该工单提交人 |

回复与状态在同一次操作中只发送一份通知。群消息对全群可见，**@ 不等于私聊**。只发送更新类型、紧急程度（低/中/高/紧急）、@ 和登录链接，不发送标题、详细描述、回复正文或附件；链接仍受平台登录与工单归属校验。钉钉是单向提醒，回复需回到平台。

### 配置步骤

1. 建立一个协作群，将管理员和顾问加入。确认群设置允许添加自定义机器人。
2. 添加自定义机器人并启用加签。将 Webhook 的 `access_token` 值填入服务器 `.env.production` 的 `DINGTALK_GROUP_TOKEN`，加签密钥填入 `DINGTALK_GROUP_SECRET`。若还启用了关键词规则，请包含 `SAP Desk`。无需企业内部应用 AppKey / AppSecret。
3. 管理员在平台“账号管理”中为自己和顾问填写**钉钉绑定的 11 位中国大陆手机号**。须取得本人同意并确认已入群。手机号用于 `atMobiles` 与消息正文中的 @ 标记，会传给钉钉并可能显示在群消息中。此版暂不支持海外号码。
4. 保持 `PUBLIC_BASE_URL=https://43.130.36.105`，核对群和绑定后设 `DINGTALK_ENABLED=true`，重建 API / worker 容器。不要把密钥放入前端、Git 或聊天。
5. 用约定的测试顾问验证“提交 @ 管理员 → 管理员回复 @ 顾问”。测试会真实发群消息，先确认测试接收人。钉钉 API 接受请求不保证 @ 成功、送达或已读，须由目标成员确认。

缺少机器人配置、手机号、启用的接收账号时记录为受阻，不会退回 @所有人。补齐后在管理员“钉钉通知”页重试。旧版 `group` / `personal` 记录保留，停止投递且禁止重试，避免历史私聊提醒被转发到共享群。原 `dingtalk_user_id` 字段保留兼容，不会自动当作手机号。

站内提醒独立工作，前台每 30 秒更新。自动发送间隔至少 4 秒；部署单个 worker。

协议参考：[自定义机器人](https://open.dingtalk.com/document/orgapp/custom-robot-access)。

### 队列语义

通知事件与工单写入同一事务；独立 worker 发送，避免钉钉故障阻塞提交。运行单个 worker，调用之间至少间隔 4 秒。网络错误指数退避，最多尝试 5 次；进程中断后 5 分钟过期租约可恢复。API 确认接收后记录 `accepted`，**不代表送达或已读**。网络超时存在对方已接受但本地未知的情况，因此重试可能重复通知，语义为至少一次，不承诺恰好一次。

## 腾讯云上线

**本项目现在支持公网 IP＋HTTPS，无需购买域名。已确认目标为 `43.130.36.105`，完整的分步命令见 [IP 部署指南](docs/IP_DEPLOYMENT.md)。**

面向少量用户，2 核 4GB 可作为起点。服务器在硅谷，需额外关注国内访问延迟。附件占用量决定 60GB 磁盘的长期容量；定期检查磁盘，备份应复制到独立存储。

1. 使用固定公网 IPv4，或使用已解析的域名；安装 Docker Engine + Compose 插件；只公开 80/443，SSH 限制来源。API 和 PostgreSQL 没有公开端口。
2. 上传源代码（排除 `.venv`、`node_modules`、本地数据库、`.local-credentials.txt`、开发 `.env`）。
3. 运行 `python3 scripts/prepare_production_env.py 43.130.36.105 --email 你的联系邮箱`，生成新生产密钥；不会覆盖已有 `.env.production`。核对域名、Cookie/CSRF 设置。
4. 按 IP 部署指南先测试签发，再申请可信证书；脚本验证后原子切换 `deploy/certs/current`。测试证书不会安装到线上。证书未准备好时不开放业务服务。
5. 执行以下命令。请在项目根目录运行，并始终指定生产 env 文件。

```sh
docker compose --env-file .env.production build
docker compose --env-file .env.production up -d db --wait
docker compose --env-file .env.production run --rm api python manage.py migrate
docker compose --env-file .env.production run --rm api python manage.py bootstrap_admin --email your-email@example.com --name 管理员
docker compose --env-file .env.production run --rm api python manage.py check --deploy
docker compose --env-file .env.production up -d
docker compose --env-file .env.production ps
```

PostgreSQL 初始化脚本创建非超级用户 `sapdesk`，应用使用该角色；数据库超级用户密码与应用密码分开。初始化脚本仅对新数据卷执行，后续更改数据库密码需要同步修改 PostgreSQL 角色和应用配置，不能只改 env 文件。

生产启用 Secure/HttpOnly/SameSite Cookie、CSRF、Argon2 密码哈希、登录限流、HTTPS 重定向、HSTS、CSP 和防嵌入头。`TRUST_PROXY=true` 仅适用于 API 不对外暴露、由这里的 Nginx 覆写代理头的部署方式。

`check --deploy` 关于 HSTS includeSubDomains 和 preload 的提醒默认保留：平台只管理当前域名，不自动约束你所有子域或申请浏览器预加载。

IP 证书有效期约 6 天，按 IP 部署指南安装每 6 小时检查的 systemd 定时任务；主机脚本完成续期、验证、平滑重载与失败回滚，并检查实际 TLS 证书。需额外配置任务失败与临期告警。Docker 配置 `restart: unless-stopped`，守护程序自身需启用开机启动。健康检查可报告异常，但不会自动重启仍在运行的无响应进程，需要运维告警配合。

### 更新

先备份，再构建新镜像；在维护窗口停止 API/worker，执行数据库迁移，然后启动新版本。保留上一版本代码/镜像和迁移前备份。涉及不可逆迁移时，不能只回退镜像，需要同时恢复配套数据库与附件。

每次新功能经本次发布确认、部署上线并验证通过后，必须将与上线版本一致的代码、测试及文档提交并推送到 GitHub，核实远端提交并记录同步结果，作为发布收尾的完成条件。详见 [上线后同步 GitHub](docs/OPERATIONS.md#上线后同步-github)。 当前已上线快照与同步证据见 [2026-10-27 同步记录](docs/reviews/github-sync-20261027/README.md)。

```sh
sh scripts/backup.sh
docker compose --env-file .env.production build
docker compose --env-file .env.production stop api worker
docker compose --env-file .env.production run --rm api python manage.py migrate
docker compose --env-file .env.production up -d
```

## 备份与恢复

`sh scripts/backup.sh` 会短暂停止 API/worker 以获取一致的数据库与附件快照，保存到 `backups/时间戳/`，写入校验和，随后启动 API/worker。未完成的备份目录以 `.incomplete` 结尾，不能用作有效备份。

建议每日在维护时段运行、保留至少 7 个日备份和 4 个周备份，并加密复制到服务器之外；本项目不会擅自为你创建系统定时任务或删除备份。

恢复先在隔离环境演练。以下操作会覆盖目标库，生产执行前需确认目标与备份：

```sh
# 在备份目录验证 SHA256SUMS，然后回到项目根目录。
docker compose --env-file .env.production stop api worker
docker compose --env-file .env.production exec -T db pg_restore -U postgres --role=sapdesk -d sapdesk --clean --if-exists --no-owner < backups/选定时间/database.dump
docker compose --env-file .env.production run --rm -T --no-deps --entrypoint tar api -C /app/private-media -xzf - < backups/选定时间/attachments.tar.gz
docker compose --env-file .env.production up -d api worker web
```

恢复前后检查账号登录、工单数量、附件下载和时间线。不要运行 `docker compose down -v` 清理生产环境，它会删除数据卷。

每日还可运行 `python manage.py maintenance` 清理过期会话与登录限流记录，该命令不删除工单和审计数据。

## 验证

```sh
.venv/bin/python backend/manage.py test desk assistant
python3 -m unittest discover -s tests -p 'test_*.py'
node --test connector/connector.test.mjs
.venv/bin/python backend/manage.py makemigrations --check --dry-run
npm --prefix frontend run build
npm --prefix frontend test
npm --prefix frontend audit --omit=dev --registry=https://registry.npmjs.org
```

测试覆盖会话/CSRF、权限隔离、附件伪装与限制、回滚清理、工单全流程、版本冲突、提交内容指纹、筛选与流式导出、已读高水位、账号禁用/重置与改密交错、钉钉队列、AI 查询完整业务范围，以及真实 Vue 组件路由复用、迟到响应、发送中编辑、通知分页和详情更新提示。CI 执行 `desk + assistant` PostgreSQL 测试、全部 Python 脚本测试、Node 连接器测试、迁移检查、前端单测与构建，不包含自动发布步骤；本地无 PostgreSQL 时行锁并发测试跳过，不能据此宣称生产容器或 PostgreSQL 已经实机验收。

`design-v3/` 保留原型，仅供设计参考；它不是正式应用，生产 Docker 构建会排除这个目录。
