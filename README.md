# 安全网络研究与资源获取 Skills

**让 Codex 查网页、取公开媒体、比商品，但不偷偷登录、付费或替你下单。**

这里有 3 个中文优先、仅显式调用的 Agent Skills：一个负责选择可用的网页研究后端，一个负责验证媒体文件，一个负责基于真实卖家和当前商品证据做购买研究。失败时只做有界回退，不把“帮我查”扩大成账号操作。

> 3 explicit-only Codex skills for bounded public-web research, verified media acquisition, and seller-specific shopping evidence.

[![Skills](https://img.shields.io/badge/skills-3-2563eb)](#skill-目录)
[![Latest release](https://img.shields.io/github/v/release/ranveerankhira-gif/safe-web-agent-skills?label=release)](https://github.com/ranveerankhira-gif/safe-web-agent-skills/releases/latest)
[![Validate](https://github.com/ranveerankhira-gif/safe-web-agent-skills/actions/workflows/validate.yml/badge.svg)](https://github.com/ranveerankhira-gif/safe-web-agent-skills/actions/workflows/validate.yml)
[![Explicit only](https://img.shields.io/badge/invocation-explicit--only-d97706)](#共同安全边界)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](LICENSE)

## 30 秒找到适合你的 Skill

| 你现在想做什么 | 使用哪个 Skill |
|---|---|
| 搜索或读取公开网页，并在后端失败时安全切换 | [`web-tool-router`](skills/web-tool-router) |
| 获取公开媒体，并确认拿到的不是 HTML、封面或错误页 | [`resource-fetcher-router`](skills/resource-fetcher-router) |
| 比较具体在售商品、卖家、价格和差评证据 | [`shopping-research-assistant`](skills/shopping-research-assistant) |

**立即使用：**前往 [最新 Release](https://github.com/ranveerankhira-gif/safe-web-agent-skills/releases/latest)，下载所需 Skill，并用同一页面的 `SHA256SUMS.txt` 核对文件。

## 为什么建立这个仓库

网络工具最容易出现三类问题：来源不清、失败后无限回退，以及把“帮我查”偷偷扩大成登录、发布、购买或付费调用。

本仓库把公开研究、媒体获取和商品调研拆成三个边界明确的Skill。它们可以调用用户自行安装并审查的后端，但不捆绑下载器、不保存真实账号，也不把安装解释为授权执行外部操作。

## Skill 目录

| Skill | 用途 | 核心边界 |
|---|---|---|
| `web-tool-router` | 在用户配置的公开网页后端之间进行只读路由与有界回退 | 不用于私人数据、发布、账号变更或付费API |
| `resource-fetcher-router` | 调用用户配置的媒体获取后端，并独立核验返回文件 | 不绕过访问控制，不自动安装下载器 |
| `shopping-research-assistant` | 基于当前商品、评价、投诉和卖家证据进行选购调研 | 不提交订单，不处理付款或卖家账号 |

## 安装

正式版本请从 [GitHub Releases](https://github.com/ranveerankhira-gif/safe-web-agent-skills/releases/latest) 下载，并核对同一版本的 `SHA256SUMS.txt`。

1. 下载`<skill-name>-<version>.zip`。
2. 核对SHA-256。
3. 阅读`SKILL.md`、`agents/openai.yaml`、示例配置、脚本及权限说明。
4. 将完整目录复制到公共Skill位置，例如：

   ```text
   C:\Users\<你的用户名>\.agents\skills\<skill-name>
   ```

5. 真实后端路径、浏览器资料和账号配置必须保存在仓库外。
6. 重启Codex后，通过`$skill-name`显式调用。

整套下载文件名为`safe-web-agent-skills-v0.1.0.zip`。建议按需安装，而不是默认启用全部网络后端。

## 共同安全边界

- 三个Skill全部设置`allow_implicit_invocation: false`。
- 默认面向公开、合法、只读的网络信息。
- 登录、验证码、认证浏览器读取和受限内容必须由用户逐次授权。
- 不提交订单、付款、评论、帖子或账号修改。
- 不自动安装或升级后端，不使用来源不明的可执行文件。
- 后端失败只能执行有界回退；不得把失败解释为扩大权限。
- 下载结果必须检查文件类型、大小、摘要和HTML伪装等异常。
- 示例配置只含虚构值；Token、Cookie、浏览器资料和真实记录不得提交。

详细边界见各Skill的`references/security-and-permissions.md`、[安全政策](SECURITY.md)、[目录](catalog.json)与[来源审计](provenance.json)。

## 外部依赖

Skill只负责路由与验证。`ffprobe`、网页检索后端、媒体下载器、浏览器会话或官方购物客户端均应由用户自行合法安装、审查和配置。

## 开发与验证

```powershell
python -X utf8 scripts/build_catalog.py
python -X utf8 scripts/validate_repo.py
python -X utf8 scripts/package_release.py --release all --verify-only
python -X utf8 -m unittest discover -s scripts/tests -v
```

---

如果你也需要“能联网，但不越权”的 Agent 工作流，欢迎点一个 ⭐，帮助更多人找到这三个安全路由 Skill。

