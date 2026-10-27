# 贡献指南

感谢改进这些中文优先的 Codex Skills。

1. 每个 Skill 放在 `skills/<skill-name>/`，必须包含 UTF-8 `SKILL.md` 和 `agents/openai.yaml`。
2. 名称使用小写字母、数字和连字符；说明必须收窄触发范围，不得把普通任务偷换成营销、增长或固定风格流程。
3. 禁止提交真实账号、邮箱、设备名、坐标、路径、聊天、登录态、密钥、日志和私有仓库信息。路径用参数、环境变量或 `config.example.json`。
4. 读取私人数据、写文件、控制桌面、下载资源、调用 GPU 或改变外部状态的 Skill 必须 `allow_implicit_invocation: false`，并提供 `references/security-and-permissions.md`。
5. 带脚本的变更必须增加或更新离线测试；测试只能使用临时目录和虚构数据。
6. 第三方改编必须保留上游许可证、版权和 `SOURCE.md`，说明固定修订及本地改造。
7. 提交前运行：

   ```powershell
   python -X utf8 scripts/validate_repo.py
   python -X utf8 scripts/package_release.py --release all --verify-only
   ```

不要把扫描器豁免用于整文件或整目录。仅当代码已经有可验证的窄边界时，才允许在具体命中行写带理由的 `# noqa: SEC-AUDITOR`。

