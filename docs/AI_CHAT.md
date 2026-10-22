# SAP AI 助手 · 聊天基础版

本版增加独立聊天页面，不恢复已暂停的 AI 工单自动回复。2026-10-13 经用户确认已发布至 https://43.130.36.105/#/assistant，服务器 Codex 订阅登录与两轮真实问答、公网登录态 SSE 均已验证。SAP 保持未接通。

## 使用范围

- “AI 助手”入口位于管理员“开发日历”下方，目前仅管理员可见和访问。普通顾问直达页面会返回工单页，聊天接口返回 403。
- 会话与问题保存在数据库。管理员只能访问自己的会话，不能读取其他人的私人会话。原有顾问聊天记录保留，当前暂停其访问。
- 可创建新对话、查看历史、选择 DS4/QS4 上下文、关联自己有权访问的工单、复制回答、停止生成和手动重试。
- 第一条问题发送后固定系统和关联工单；更换上下文需新建对话。工单正文和最近 8 条沟通会加入模型输入，附件不会自动读取，也不会把 AI 回答写回工单。
- SAP 永远返回 `connected: false`，没有注册 SAP 工具，也不加载 sap-eyes/sap-adt。系统选择只用于问题上下文，不代表已获得系统访问权。
- 页面发送问题后先持久化，再由独立 worker 处理，浏览器离开不终止后台任务。SSE 传送处理状态与已收到的回答，刷新可恢复。
- CLI 的 JSON 事件通常在一段回答完成时提供文本，不能承诺每个 token 都逐字显示；OpenAI API 适配器支持文本增量。

## 运行方式

`AI_PROVIDER` 支持三种值，默认 `disabled`：

| 值 | 配置 | 行为 |
| --- | --- | --- |
| `disabled` | 无 | 保存问题，显示未配置，不伪造 AI 回答 |
| `codex` | 服务进程能执行 `codex`，且该操作系统用户已登录 | 调用订阅账号的 CLI，消耗该账号的额度 |
| `openai` | `OPENAI_API_KEY`、`OPENAI_MODEL` | 调用 Responses API，服务端保管 API Key |

Codex 适配器要求 CLI 0.158.0 或更高版本，依赖已验证的 `--ignore-user-config`、`--ephemeral` 和功能开关。升级 CLI 后应重新验证参数兼容性和隔离配置。`AI_CODEX_BIN` 可指定管理员配置的可执行路径，`AI_CODEX_MODEL` 留空使用 Codex 默认模型；页面不能修改启动命令和模型。

每轮在空临时目录运行，问题通过 stdin 传入，使用固定参数数组和 `shell=False`。不继承 Django、SAP 或 API 密钥环境变量。禁用 Shell、命令执行、插件、MCP 配置、浏览器、电脑控制、图片读取、记忆、子代理和本机 skills 自动发现，并使用只读沙箱。只提取 agent message 和完成事件，不向网页返回原始日志、内部推理或认证信息。

订阅登录由 Codex 自身管理，本项目不读取、复制或导出 auth.json。CLI 进程会正常使用其服务账号已有的认证存储；不要将认证文件放进前端、代码包或仓库。

## 本地预览

当前预览使用独立的 `.local-ai-preview.sqlite3`，预览账号凭据位于权限 0600 的 `.local-ai-preview-credentials.json`。这两个文件都已忽略且不会进入发布包。原有本地数据库、线上数据库和 SAP 均不参与聊天验收。

在项目根目录检查 CLI 登录：

```sh
codex --version
codex login status
```

三个本地进程分别运行（现有预览已经启动，无需重复启动）：

```sh
AI_PROVIDER=codex SQLITE_PATH="$PWD/.local-ai-preview.sqlite3" .venv/bin/python backend/manage.py runserver 127.0.0.1:8011
```

```sh
AI_PROVIDER=codex SQLITE_PATH="$PWD/.local-ai-preview.sqlite3" .venv/bin/python backend/manage.py chat_worker
```

```sh
SAPDESK_API_ORIGIN=http://127.0.0.1:8011 npm --prefix frontend run dev -- --port 5175
```

预览地址：`http://127.0.0.1:5175/#/assistant`。需要登录平台账号；CLI 登录与平台登录是两个独立层次。

## 任务与权限

- 会话创建和问题提交使用幂等键；重复提交不会产生第二次生成。同一用户同时仅处理一个问题。
- 默认每人滚动 24 小时最多 100 个问题或生成尝试，每个会话最多 100 个问题，单问题最多 12,000 字符，单轮最多 150 秒，CLI 输出最多 40,000 字符。
- 模型只接收该会话最近可容纳的已完成对话，历史正文预算 24,000 字符，最多 16 轮；发生裁剪时界面提示。系统当前状态和问题单独加入。
- API、SSE、worker 都检查账号和工单权限。重置密码、停用账号或关联工单不再可读时停止后续返回。已经发往模型的内容无法撤回。
- CLI 超时或错误不自动重复调用。worker 崩溃留下的运行中任务，恢复服务后标记为中断，等待用户手动重试，避免隐藏的重复额度消耗。
- “停止生成”使任务立即转为取消。CLI 进程组会被终止；API 模式在下一次事件或读超时后关闭连接，取消不保证服务商立即停止计费。
- 只保存经过筛选的错误类型和用量。原始 provider 错误不会直接回显给用户。

## 服务器部署前的条件

Linux 服务器可以运行 Codex CLI，需允许访问 OpenAI，并在专用操作系统账号下完成 CLI 登录。订阅额度、登录状态、网络和模型服务都影响可用性，不承诺无限并发或固定响应时长。

生产新增独立 `chat-worker` 容器，安装经 npm SHA-512 校验的官方 Codex 0.158.0 Linux 原生运行包。进程以 app 用户运行，容器根文件系统只读、全部 capabilities 移除，不发布端口；认证存放在独立 `sapdesk_codex-auth` 卷，文件权限 0600。API 不挂载认证卷，通过数据库中的 worker 心跳读取登录就绪状态。Nginx 最多允许 8 条并发聊天流，API 使用 16 个线程，为普通工单请求保留容量。

设备代码登录在用户明确授权后开启，并使用用户已登录的 Chrome 完成服务器授权。认证由 Codex 自身刷新并持久化；登录辅助容器可清理，认证卷不得删除。现有备份停写与容器监控已纳入聊天 worker；认证卷未纳入每日归档，灾难恢复后应重新登录。

本次已通过服务器 Linux 容器 48 项测试、独立 PostgreSQL 48 项测试、线上会话保存与权限检查、真实 Codex 两轮问答及公网登录态 SSE。尚未进行多人负载、跨日凭据续期与长时间运行验收。SAP 网络/账号/权限属于下一阶段，需要 Basis 和网络团队配合。

## 验证

```sh
.venv/bin/python backend/manage.py test desk assistant
.venv/bin/python backend/manage.py makemigrations --check --dry-run
npm --prefix frontend test
npm --prefix frontend run build
```

重点覆盖：CSRF 和会话失效、会话跨账号访问、工单越权关联、提交幂等、实际 EventSource Accept 头、完成/失败/取消状态、中断恢复、错误脱敏、上下文隔离、CLI 参数和环境隔离。

官方参考：[Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[认证方式](https://learn.chatgpt.com/docs/auth)、[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)、[Responses 流式响应](https://developers.openai.com/api/docs/guides/streaming-responses)。

## 2026-10-14 管理员试用范围

本次线上调整仅包括入口、路由、聊天接口和后台任务的管理员权限校验；未发布本地 Mac/Windows 连接器原型，无数据库结构变更。后台在账号角色变化后停止为该账号生成回答。
