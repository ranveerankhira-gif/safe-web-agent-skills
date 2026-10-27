#!/usr/bin/env python3
"""Build the stable public catalog for the safe web Skill collection."""

from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
NAMES = ["web-tool-router", "resource-fetcher-router", "shopping-research-assistant"]
PURPOSE = {
    "web-tool-router": "在用户配置的公开网页后端间进行只读路由与有界回退",
    "resource-fetcher-router": "调用用户配置的媒体后端并独立验证下载结果",
    "shopping-research-assistant": "基于当前商品、评价、投诉和卖家证据开展选购调研",
}
WRITES = {"resource-fetcher-router", "shopping-research-assistant"}
CREDENTIALS = {"web-tool-router", "shopping-research-assistant"}
COST = {"resource-fetcher-router"}
DEPENDENCIES = {
    "web-tool-router": [
        "至少一个由用户自行安装、审查并配置的免费网页检索后端",
        "可选：gh、yt-dlp、Ketch、Crawl4AI、Agent Reach或其他兼容只读工具",
        "可选：OpenCLI与独立浏览器profile（仅经授权的认证读取）",
        "可选Skill：resource-fetcher-router（获取并验证媒体内容）",
    ],
    "resource-fetcher-router": [
        "Python 3.10+（运行独立媒体结果验证器）",
        "ffprobe（由用户自行安装并配置）",
        "用户自行安装、审查并配置的兼容资源路由器及所需下载后端",
    ],
    "shopping-research-assistant": [
        "Python 3.10+（离线校验与可选长期记录）",
        "Node.js 20+（可选淘宝／天猫官方客户端读取器）",
        "可选Skill：web-tool-router（公开网页调研）",
        "可选Skill：diannao-kongzhi（经授权的Windows UI自动化）",
        "可选：用户自行安装并登录的官方客户端或已配置OpenCLI会话",
    ],
}


def display_name(name: str) -> str:
    text = (SKILLS / name / "agents" / "openai.yaml").read_text(encoding="utf-8")
    match = re.search(r'^\s*display_name:\s*["\']?(.+?)["\']?\s*$', text, re.M)
    return match.group(1) if match else name


def main() -> int:
    provenance = json.loads((ROOT / "provenance.json").read_text(encoding="utf-8"))
    approved = {row["name"] for row in provenance.get("skills", [])}
    planned = set(NAMES)
    directories = {path.name for path in SKILLS.iterdir() if path.is_dir()}
    if approved != planned or directories != approved:
        raise ValueError(
            "publication set differs from provenance approval: "
            f"unplanned={sorted(directories - approved)}, "
            f"missing={sorted(approved - directories)}, "
            f"metadata_gap={sorted(approved ^ planned)}"
        )
    rows = []
    for name in NAMES:
        rows.append({
            "name": name,
            "display_name_zh": display_name(name),
            "purpose_zh": PURPOSE[name],
            "release": "v0.1.0",
            "platforms": ["cross-platform"],
            "risk_level": "high",
            "permissions": {
                "network": True,
                "filesystem_read": True,
                "filesystem_write": name in WRITES,
                "credentials": name in CREDENTIALS,
                "external_state": True,
                "cost_or_quota": name in COST,
            },
            "dependencies": DEPENDENCIES[name],
            "invocation_policy": "explicit-only",
            "license": "MIT",
            "version": "0.1.0",
            "archive_sha256": None,
        })
    payload = {
        "schema_version": 1,
        "repository": "ranveerankhira-gif/safe-web-agent-skills",
        "skills": rows,
    }
    target = ROOT / "catalog.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target} with {len(rows)} skills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

