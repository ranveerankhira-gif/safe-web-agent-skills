# 证据与报告

## 动态媒体证据

- `metadata_only`：只有标题、作者、日期、描述、计数或 feed 元数据。
- `cover_only`：只有封面、二维码遮罩、预览截图、播放按钮或静态图片响应。
- `media_obtained`：已经取得并验证媒体字节或可播放媒体源，但尚未检查内容。
- `content_verified`：实际检查了相关画面、音频、字幕或可靠转录，并能说明核对范围。

只有 `content_verified` 支持“看过视频”“视频里说了什么”或基于具体内容的判断。成功加载页面、关闭二维码、点击播放、出现 `image/jpeg`、看到封面或取得媒体文件都不等于已核实内容。

微信匿名页遇到二维码或登录门槛时，标记 `wechat_qr_gate` 并停止重复截图或轮换匿名浏览器。公开分享链接需要内容时改用 `$resource-fetcher-router`；仍失败且没有获授权登录会话时，报告当前证据等级。根据标题或封面做的外部调查只能标记为推测。

## 建议失败代码

`BACKEND_MISSING`、`BACKEND_UNHEALTHY`、`NETWORK_FAILURE`、`RATE_LIMITED`、`AUTH_REQUIRED`、`CAPTCHA_REQUIRED`、`BROWSER_BRIDGE_UNAVAILABLE`、`BROWSER_SESSION_MISMATCH`、`AUTH_SURFACE_MISMATCH`、`PRIVATE_OR_PAID_CONTENT`、`NO_CONTENT_EVIDENCE`、`UNSUPPORTED_REQUEST`。

## 报告字段

报告任务类型、所用后端、是否回退、是否使用认证会话、目标 auth surface、最强证据等级、实际读取的正文/音频/字幕/转录范围，以及最终失败代码。不要记录账号标识、profile ID、Cookie、Token、窗口注册表或完整私有 URL。
