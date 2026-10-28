# 工单详情刷新按钮布局与发布记录

状态：用户确认本次最终效果后，已于 **2026-10-28 08:48（北京时间）** 完成上线验证。上线代码 [`67f4803`](https://github.com/stillvictoria533-ui/sap-work-management/commit/67f4803ecbd47cdd5892de791a404ba84c561af8) 已推送至 GitHub `main`，并实时读回确认。

## 调整结果

- 将刷新操作与“返回列表／返回日历”放在同一行，靠右对齐。
- 使用刷新图标与紧凑按钮，将“保留草稿”拆为灰色辅助说明。
- 刷新时按钮显示“刷新中…”并禁用，图标旋转；系统要求减少动画时停止旋转。
- 有新回复或变更时，单独显示提示条，刷新成功后消失。
- 沿用现有刷新、草稿保护、版本冲突和提交逻辑，仅修改详情组件的模板及局部样式。

## 预览

- [手机页头局部](header-mobile.png)
- [桌面完整首屏](desktop.png)
- [手机完整首屏](mobile.png)

预览使用实际构建后的工作台与工单详情组件、虚构账号和工单数据；未连接生产服务。

## 验证记录

- `npm --prefix frontend run build`：通过，包含 TypeScript 检查。
- `npm --prefix frontend test`：41 项通过，包含刷新保留回复、附件、操作说明和排期草稿，以及排期冲突处理的现有回归测试。
- `git diff --check`：通过。
- 浏览器 1440px 桌面、390px 手机和 320px 窄屏：返回链接与刷新按钮保持同一行，无横向溢出；手机按钮高度 40px。
- 实际点击刷新：回复文字和已选附件保留；加载期间按钮禁用并显示“刷新中…”，不再额外插入加载段落。
- 模拟请求返回 HTTP 503：已加载工单、回复文字和附件保留，错误可见，按钮恢复可用；恢复连接后可正常刷新。
- 后台检测到新版本时出现状态提示，刷新后提示消失，草稿保留。
- 键盘聚焦有可见轮廓；减少动画偏好下刷新图标的计算动画为 `none`。
- 本地浏览器仅记录预期模拟 HTTP 503 对应的资源错误，未发现应用脚本异常。

## 修改范围与版本

原工作区处于 `main`，落后已缓存的 `origin/main` 10 个提交，并包含其他未提交修改。截图所示按钮存在于较新版本，因此在独立工作区以 `c0c8c6b` 为基线修改，保留原工作区已有内容。

- 代码：`frontend/src/pages/TicketDetail.vue`。
- 可审阅补丁：[change.patch](change.patch)。
- 独立工作区：`/Users/apple/.codex/worktrees/ticket-refresh-layout/SAP工作管理平台`。

## 发布与 GitHub 同步

- 仅发布 `frontend/src/pages/TicketDetail.vue` 的模板和样式，未改动刷新、草稿保护及后端业务逻辑。
- 服务器 59 份构建输入与审阅版本一致；候选镜像和公网的 30 个应用静态资源均逐文件匹配本地审阅构建的 SHA256。
- HTTPS 证书验证、`/api/health` 和 Nginx 配置检查通过；公网登录页正常，浏览器控制台无警告或错误。
- 仅重建 web 容器；API、通知 worker、chat worker、数据库的容器 ID、镜像和启动时间均未变化。没有数据库迁移或生产工单写入。
- 构建时排除服务器中三份旧备份文件，原文件予以保留。
- 回退源码：`/root/sapdesk-ticket-refresh-20261028/backup-source.tar.gz`。
- 回退镜像：`sapdesk-web:before-ticket-refresh-20261028`。
- 发布镜像：`sha256:3b795881edad01895f084d5981136f43c29fb8f1084aec25247996e5383b9f8e`。
- GitHub `main` 已包含上线代码 `67f4803ecbd47cdd5892de791a404ba84c561af8`；已通过实时远端读取验证，非仅检查本地缓存。
- GitHub 自动检查：[运行记录](https://github.com/stillvictoria533-ui/sap-work-management/actions/runs/40611238267)，已全部通过，包括 PostgreSQL 后端、脚本、迁移检查、前端测试和构建；本次本地构建与 41 项前端回归也已通过。
- 结构化证据见 [deployment-verification.json](deployment-verification.json)。本发布记录作为后续文档提交，不改变已上线应用代码。
