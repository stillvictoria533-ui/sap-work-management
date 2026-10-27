# 工单详情页阅读字号协调

状态：用户确认本次最终效果后，已于 2026-10-26 15:21（北京时间）发布至 https://43.130.36.105 。

## 调整结果

| 内容 | 调整前 | 调整后 |
| --- | --- | --- |
| 需求说明、右侧属性值、回复输入区 | 12px | 14px |
| 提交、状态和排期操作记录 | 10px | 13px，使用辅助文字颜色 |
| 状态和排期的具体说明 | 10px | 14px，使用正文颜色 |
| 人工和 AI 回复正文 | 12px | 14px |
| 回复人姓名与角色 | 11px | 13px |
| 操作及回复时间 | 10–11px | 12px |
| 时间线附件名／大小 | 11px／10px | 13px／12px |
| 详情区分节标题、右侧属性标签 | 10–11px | 12px |

主要正文统一为 14px、行高 1.8 倍；姓名和操作记录为 13px，时间和标签为 12px。记录间距由 22px 增至 24px，同步调整时间线圆点位置，长说明和附件名自动换行。

修改限于 `frontend/src/workspace.css` 的工单详情样式，以及 `frontend/src/product.css` 中将时间线文字压回 11px 的规则。

## 预览与验证

- [桌面首屏](detail-desktop.jpg)，视口 1440×1100。
- [需求说明、时间线与右侧属性对照](detail-context.jpg)。
- [手机预览](detail-mobile.jpg)，视口 390×844。
- 预览使用实际工作台外框、导航和工单详情组件，配以本地虚构数据；包含提交记录、附件、状态变更、排期说明、顾问回复和 AI 回复。
- 两种视口下浏览器计算样式均确认主要正文 14px、姓名与操作记录 13px、时间 12px；需求说明、右侧属性、回复输入区的字号与时间线正文一致。未出现横向溢出，手机上的长说明和附件名正常换行。
- 浏览器控制台无警告或错误。
- `npm --prefix frontend run build` 通过，包含 TypeScript 检查。
- `git diff --check` 通过。

## 发布验证

- 基于当前线上源码，仅更新 `frontend/src/workspace.css` 和 `frontend/src/product.css` 两个文件；已核对前端源码清单，变更范围一致。
- 已核对新镜像及公网的样式文件 SHA256，与本次审阅版完全一致。
- 29 个公网静态资源与新容器中的文件逐一校验通过；Nginx 配置检查和 `/api/health`（HTTP 200）通过。
- API、通知 worker、chat worker 和数据库容器的 ID、镜像及启动时间保持不变。
- 回退源码：`/root/sapdesk-detail-typography-20261026/source.tar.gz`。
- 回退镜像：`sapdesk-web:before-detail-typography-20261026`。
- 发布镜像：`sha256:6cdb5ff3028a58eb5a96c2fa8256d963aae32b8c3dce3039c7b9cd12e094143e`。
