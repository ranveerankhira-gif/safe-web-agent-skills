---
name: resource-fetcher-router
description: "Route an explicitly requested public song or media acquisition task through a user-configured local downloader, then independently verify the resulting bytes. Use when the user invokes $resource-fetcher-router to discover, download, or probe media; do not use for private content, access-control bypass, or automatic tool installation."
---

# 资源获取路由器

开始前读取 [安全与权限](references/security-and-permissions.md)。配置或接入下载器时再读取 [路由器接口](references/router-contract.md)。

此 Skill 只在用户显式调用时工作。它不捆绑下载器，也不自动安装、升级或下载任何程序、浏览器、模型或媒体后端。先确认用户已经审查并配置兼容路由器；不要在磁盘上自行搜索可执行文件。

## 配置

将 `config.example.json` 复制到仓库外的私有位置，填入本机值。优先通过命令参数传入配置；兼容旧路由器时，可把程序路径和策略文件分别映射到 `RESOURCE_FETCHER_ROUTER`、`RESOURCE_FETCHER_POLICY`。真实配置、Cookie、Token、登录态、代理信息和下载日志不得进入 Skill 目录。

缺少路由器、`ffprobe` 或必要后端时，报告缺失依赖并停止。不要为完成当前任务临时安装或切换到付费服务。

## 运行边界

只处理用户明确给出的公开 HTTPS URL、歌曲查询、候选目录，或用户明确指定的本地媒体文件。把查询和 URL 作为单独参数传递，使用参数数组调用程序；禁止拼接 shell 命令。

- `discover`：只读发现候选；不得创建下载目录或路由日志。
- `acquire`：只能按已保存目录中唯一匹配的候选 ID 获取资源。
- `download`：用于用户明确给出的查询或公开 URL；必须指定新的输出目录。
- `probe`：只读检查用户指定的现有本地文件。

兼容命令示例见 [路由器接口](references/router-contract.md)。命令行参数优先于环境变量；不得把真实配置值写回公开文件。

## 下载与回退

下载前确认输出根目录。每次运行使用新的子目录；不得覆盖同名不同内容文件，不得写入输出根目录之外，不得跟随符号链接或 Windows 重解析点。默认串行执行；并发、超大文件或批量任务需要用户明确要求并给出限制。

只对超时、DNS、连接重置、HTTP 5xx 等可恢复故障进行一次有界重试；HTTP 429 最多等待一次后切换到已配置的免费后端。登录、二维码、验证码、DRM、私有内容、付费墙、区域限制、代理变更或账号动作属于终止条件，立即停止并交还用户。

P2P/Soulseek 只有在用户明确要求、理解对外连接与凭据风险，并单独配置后才能使用。不得从环境变量、浏览器资料目录或其他工具中自行寻找凭据。

## 独立验证

不要相信退出码、标题、封面或下载器自己的“成功”文字。路由器返回结果后，运行：

```powershell
python scripts/verify_media_result.py --result "<router-result.json>" --output-root "<本次输出目录>" --ffprobe "<ffprobe>"
```

验证器要求文件位于本次输出目录内、不是链接/重解析点、大小受限、SHA-256 与声明一致，并由 `ffprobe` 检出正时长及音频或视频流。

始终按以下证据等级报告：

- `metadata_only`：只有标题、作者、候选或网页元数据。
- `cover_only`：只有封面或静态截图。
- `media_obtained`：媒体字节已经独立探测并计算哈希，但尚未核对内容。
- `content_verified`：已经实际检查相关画面、音频或可靠转录，并能指出核对范围。

验证器最高只授予 `media_obtained`。只有完成实际内容检查后才能升级为 `content_verified`，也只有此等级才能声称“看过视频”、复述内容或据此作判断。

## 结果报告

报告所用后端、是否发生回退、证据等级、最终文件、字节数、时长、流类型、SHA-256 和失败代码。日志只保留排错所需的最少信息；公开 URL 可去除查询参数，禁止记录 Cookie、Token、授权头、登录态、代理凭据或完整命令环境。

`PermissionError` 或 `invalid_router_output` 若发生在日志目录，必须报告为日志写入/路由器输出问题，不能误报成媒体解析失败。失败重试只处理失败项；已有文件必须先探测和核对哈希，避免重复请求。
