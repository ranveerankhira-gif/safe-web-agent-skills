---
name: web-tool-router
description: Route explicitly requested public web research through user-configured, reviewed backends with bounded fallbacks and evidence-aware reporting. Use when the user invokes $web-tool-router for live search, URL reading, public platform lookup, or a failed read-only web backend; do not use for private data, publishing, account changes, paid APIs, or credential extraction.
metadata:
  short-description: "Configurable read-only web routing with bounded fallbacks"
---

# 网页工具路由器

联网检索前读取 [安全与权限](references/security-and-permissions.md) 和 [回退矩阵](references/fallback-matrix.md)。只有任务确实需要既有登录会话时，才读取 [认证浏览器协议](references/authenticated-browser.md)；涉及视频、音频或动态页面结论时读取 [证据与报告](references/evidence-and-reporting.md)。

此 Skill 只在用户显式调用时工作。它不捆绑联网工具，也不自动安装、升级、修复或下载浏览器、扩展、CLI、MCP 服务或模型。只使用私有配置中启用且已由用户审查的免费后端；找不到工具时报告缺失依赖，不要在磁盘上自行搜索替代程序。

## 配置

将 `config.example.json` 复制到仓库外的私有位置并填写实际配置。命令可以来自 `PATH`，也可以来自私有配置指定的绝对目录。真实路径、浏览器资料目录、账号标识、Cookie、Token、代理信息和登录诊断不得提交。

配置必须明确：启用的后端、唯一浏览器会话所有者、是否允许认证浏览、每个后端的最大重试次数，以及是否禁止付费后端。公开默认值是：认证浏览关闭、付费后端关闭、每个后端最多重试一次。

## 路由流程

1. 将任务归类为公开搜索、已知 URL 提取、公开代码托管、字幕/元数据、动态网页，或明确授权的认证读取。
2. 按私有配置和 [回退矩阵](references/fallback-matrix.md) 选择最窄的只读能力。后端示例不是强制依赖，也不能覆盖用户指定的工具。
3. 先使用不需要登录的后端。只有公开路线不足、私有配置允许且用户已授权本次认证读取时，才进入认证浏览器协议。
4. 为每次尝试记录后端、能力、是否认证、结果类型和失败代码。只有出现可恢复故障才切换后端。
5. 根据实际取得的证据报告，不以命令退出码、页面标题、按钮、封面或截图代替正文、播放或账号状态证据。

## 平台边界

- 通用搜索与 URL：优先使用无登录的搜索或正文提取后端；只有 JavaScript 渲染确有必要时才用隔离浏览器。
- 公开 GitHub：可使用用户已安装的 `gh` 或公开页面读取；私有仓库或认证资源不在本 Skill 默认范围内。
- YouTube/Bilibili：优先字幕和公开元数据；页面可见不等于视频内容已核实。
- 微信视频号：元数据请求可走公开提取；观看、总结、转录或判断画面内容时，先显式调用已安装的 `$resource-fetcher-router` 获取并验证媒体。匿名 Playwright 二维码页不是播放回退。
- X、Reddit、小红书及其他登录敏感站点：无登录路线失败后停止；只有用户授权并已配置唯一浏览器会话所有者时才可读取既有登录会话。

## 故障与停止条件

超时、DNS、连接重置和 HTTP 5xx 每个后端最多重试一次。HTTP 429 最多进行一次有界等待，然后切换到下一个已配置免费后端。缺少命令或版本损坏时跳过该后端；不要在任务中安装或升级它。

遇到登录、二维码、验证码、账号挑战、私有内容、付费墙、区域限制、代理变更或浏览器桥断开时停止。`BROWSER_CONNECT`、`AUTH_REQUIRED` 或站点适配器失败不构成修复浏览器、复用其他登录态或创建更多窗口的许可。

禁止把 SocialCrawl、付费 API、消耗额度的服务或未经审查的抓取器作为隐藏回退。默认不发布、不评论、不点赞、不关注、不上传、不提交表单，也不修改账号设置；这些动作需要另行明确请求，并在动作前确认。

## 隔离

- 一个任务只允许一个浏览器会话所有者。其他浏览器自动化必须使用独立临时配置，不能连接、复制或读取该登录配置。
- 命令行抓取器、Python 抓取环境和浏览器环境相互隔离，不共享 Cookie。
- 不改变全局默认浏览器配置或默认 OpenCLI profile；多 profile 时每次显式指定。
- 只关闭当前工作流明确创建且标记为工具所有的页面。借用的用户页面只能释放控制，不能关闭。

## 报告

报告实际使用的后端、是否回退、认证状态、证据等级和失败代码。认证读取还要说明核验的是哪个站点表面，并区分 `public_site_authenticated`、`creator_dashboard_authenticated`、`browser_session_mismatch` 和 `auth_surface_mismatch`。全部允许后端失败时明确报告失败，不得以外部猜测补成成功。
