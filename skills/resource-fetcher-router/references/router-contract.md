# 路由器接口

公开 Skill 不捆绑下载器。用户提供的路由器应支持下列等价接口；命令名称可以不同，但安全语义和结果字段必须一致。

## 建议命令

```powershell
python "<router-program>" discover "<查询>" --limit 10 --json
python "<router-program>" acquire --catalog "<candidates.json>" --candidate-id "<ID>" --output "<新目录>"
python "<router-program>" probe "<本地媒体>"
python "<router-program>" "<公开 HTTPS URL 或歌曲查询>" --output "<新目录>"
```

调用时使用参数数组并禁用 shell。URL 或查询必须作为一个参数传入，不能拼接到命令字符串中。`--dry-run` 如被支持，必须严格零写入且不得发起下载。

## 发现结果

发现结果至少包含：

```json
{
  "schema_version": "1.0",
  "mode": "discover",
  "ok": true,
  "writes_performed": false,
  "candidates": [
    {
      "candidate_id": "stable-id",
      "title": "Example",
      "source": "provider-name",
      "rights_status": "unknown"
    }
  ]
}
```

候选 ID 必须稳定；`rights_status: unknown` 不能解释为允许再分发或商用。获取时必须让 ID 在保存的候选目录中唯一匹配，不能接受模型临时编造的 provider 或 URL。

## 获取结果

获取结果必须是 UTF-8 JSON 对象，并至少包含：

```json
{
  "schema_version": "1.0",
  "mode": "acquire",
  "ok": true,
  "provider": "provider-name",
  "fallback_used": false,
  "failure_code": null,
  "files": [
    {
      "path": "<absolute-path-inside-output-root>",
      "size": 123,
      "sha256": "<64-lowercase-hex>"
    }
  ]
}
```

公开验证器不信任 `ok`、`size`、`sha256` 或退出码，而会重新读取文件并调用 `ffprobe`。文件通过后只能得到 `media_obtained`；内容检查由调用者另行完成。

## 失败代码

推荐使用：`CONFIG_MISSING`、`DEPENDENCY_MISSING`、`SOURCE_NOT_FOUND`、`NETWORK_FAILURE`、`RATE_LIMITED`、`ACCESS_OR_AUTH_REQUIRED`、`NO_VALID_MEDIA`、`INVALID_ROUTER_OUTPUT`、`OUTPUT_BOUNDARY_VIOLATION`、`PROVIDER_FAILURE`。

认证、验证码、DRM、私有内容或付费要求不得归类为可恢复网络错误。日志目录写入失败应单独报告为 `LOG_WRITE_FAILURE`，不能伪装成媒体解析失败。
