#!/usr/bin/env python3
"""Independently validate media files named in an untrusted router result."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


ALLOWED_SUFFIXES = {
    ".aac", ".avi", ".flac", ".m4a", ".mkv", ".mov", ".mp3",
    ".mp4", ".ogg", ".opus", ".ts", ".wav", ".webm", ".wma",
}
MAX_RESULT_BYTES = 2 * 1024 * 1024
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except AttributeError:
        return path.is_symlink()
    return bool(attributes & 0x400)


def load_result(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValidationError("INVALID_ROUTER_OUTPUT", "result file does not exist")
    if path.stat().st_size > MAX_RESULT_BYTES:
        raise ValidationError("INVALID_ROUTER_OUTPUT", "result JSON exceeds 2 MiB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError("INVALID_ROUTER_OUTPUT", "result is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError("INVALID_ROUTER_OUTPUT", "result JSON must be an object")
    return payload


def file_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    records = result.get("files")
    if records is None and isinstance(result.get("attempt"), dict):
        records = result["attempt"].get("files")
    if not isinstance(records, list) or not records:
        raise ValidationError("NO_VALID_MEDIA", "router result contains no media files")
    if not all(isinstance(record, dict) for record in records):
        raise ValidationError("INVALID_ROUTER_OUTPUT", "every file record must be an object")
    return records


def lexical_relative_path(path: Path, root: Path) -> Path:
    normalized_path = Path(os.path.normpath(str(path)))
    normalized_root = Path(os.path.normpath(str(root)))
    try:
        return normalized_path.relative_to(normalized_root)
    except ValueError as exc:
        raise ValidationError("OUTPUT_BOUNDARY_VIOLATION", "media path is outside the output root") from exc


def validate_path(record: dict[str, Any], root: Path) -> Path:
    raw = record.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError("INVALID_ROUTER_OUTPUT", "file record has no path")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValidationError("OUTPUT_BOUNDARY_VIOLATION", "media path must be absolute")
    relative = lexical_relative_path(candidate, root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() and (current.is_symlink() or is_reparse_point(current)):
            raise ValidationError("OUTPUT_BOUNDARY_VIOLATION", "media path crosses a link or reparse point")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValidationError("OUTPUT_BOUNDARY_VIOLATION", "media path cannot be resolved inside output root") from exc
    if not resolved.is_file() or resolved.suffix.casefold() not in ALLOWED_SUFFIXES:
        raise ValidationError("NO_VALID_MEDIA", "result path is not a supported regular media file")
    return resolved


def probe_media(path: Path, ffprobe_program: str) -> dict[str, Any]:
    try:
        process = subprocess.run(
            [
                ffprobe_program,
                "-v", "error",
                "-show_entries",
                "format=duration,size,format_name:stream=codec_type,codec_name,sample_rate,channels",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationError("DEPENDENCY_MISSING", "ffprobe could not be executed") from exc
    if process.returncode != 0 or len(process.stdout.encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValidationError("NO_VALID_MEDIA", "ffprobe rejected the media file")
    try:
        payload = json.loads(process.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration") or 0)
        streams = payload.get("streams") or []
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValidationError("NO_VALID_MEDIA", "ffprobe returned an invalid result") from exc
    media_streams = [
        stream for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") in {"audio", "video"}
    ]
    if not math.isfinite(duration) or duration <= 0.1 or not media_streams:
        raise ValidationError("NO_VALID_MEDIA", "media has no positive duration or audio/video stream")
    return {
        "duration_seconds": duration,
        "format_name": (payload.get("format") or {}).get("format_name"),
        "streams": media_streams,
    }


def verify_result(
    result: dict[str, Any],
    output_root: Path,
    ffprobe_program: str,
    max_media_bytes: int,
) -> dict[str, Any]:
    if not output_root.is_absolute() or not output_root.is_dir():
        raise ValidationError("OUTPUT_BOUNDARY_VIOLATION", "output root must be an existing absolute directory")
    if output_root.is_symlink() or is_reparse_point(output_root):
        raise ValidationError("OUTPUT_BOUNDARY_VIOLATION", "output root cannot be a link or reparse point")
    if max_media_bytes < 1:
        raise ValidationError("INVALID_LIMIT", "maximum media bytes must be positive")
    if result.get("ok") is not True:
        raise ValidationError(str(result.get("failure_code") or "PROVIDER_FAILURE"), "router did not report success")

    verified: list[dict[str, Any]] = []
    seen: set[Path] = set()
    total_size = 0
    for record in file_records(result):
        path = validate_path(record, output_root)
        if path in seen:
            raise ValidationError("INVALID_ROUTER_OUTPUT", "router result repeats the same media path")
        seen.add(path)
        initial_stat = path.stat()
        size = initial_stat.st_size
        total_size += size
        if size < 1 or total_size > max_media_bytes:
            raise ValidationError("MEDIA_SIZE_LIMIT", "media files exceed the configured byte limit")
        declared_size = record.get("size")
        if not isinstance(declared_size, int) or declared_size != size:
            raise ValidationError("INVALID_ROUTER_OUTPUT", "declared media size does not match the file")
        digest = sha256_file(path)
        declared_digest = record.get("sha256")
        if not isinstance(declared_digest, str) or not SHA256_RE.fullmatch(declared_digest) or declared_digest != digest:
            raise ValidationError("INVALID_ROUTER_OUTPUT", "declared SHA-256 does not match the file")
        probe = probe_media(path, ffprobe_program)
        final_stat = path.stat()
        if (
            final_stat.st_size != size
            or final_stat.st_mtime_ns != initial_stat.st_mtime_ns
            or final_stat.st_ino != initial_stat.st_ino
            or sha256_file(path) != digest
        ):
            raise ValidationError("MEDIA_CHANGED_DURING_VALIDATION", "media changed during validation")
        verified.append({
            "path": str(path),
            "size": size,
            "sha256": digest,
            **probe,
        })

    provider = result.get("provider")
    if not isinstance(provider, str):
        provider = None
    return {
        "status": "verified",
        "evidence_level": "media_obtained",
        "provider": provider,
        "fallback_used": result.get("fallback_used") is True,
        "files": verified,
        "content_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--max-media-bytes", type=int, default=4 * 1024 * 1024 * 1024)
    args = parser.parse_args(argv)
    try:
        report = verify_result(
            load_result(args.result),
            args.output_root,
            args.ffprobe,
            args.max_media_bytes,
        )
    except ValidationError as exc:
        print(json.dumps({"status": "failed", "failure_code": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
