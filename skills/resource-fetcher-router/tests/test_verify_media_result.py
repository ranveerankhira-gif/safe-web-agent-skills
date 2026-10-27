from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import wave


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import verify_media_result as verifier


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fake_probe(returncode: int = 0) -> subprocess.CompletedProcess[str]:
    payload = {
        "format": {"duration": "1.000000", "size": "96044", "format_name": "wav"},
        "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": "48000", "channels": 1}],
    }
    return subprocess.CompletedProcess(["ffprobe"], returncode, json.dumps(payload), "")


class VerifyMediaResultTests(unittest.TestCase):
    def make_media(self, root: Path, name: str = "tone.wav") -> Path:
        path = root / name
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(48000)
            handle.writeframes(b"\x00\x00" * 48000)
        return path

    def result_for(self, path: Path) -> dict:
        return {
            "ok": True,
            "provider": "fake-downloader",
            "fallback_used": False,
            "files": [{"path": str(path), "size": path.stat().st_size, "sha256": digest(path)}],
        }

    @mock.patch.object(verifier.subprocess, "run", return_value=fake_probe())
    def test_fake_downloader_media_is_independently_verified(self, run_mock):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            media = self.make_media(root)
            before = digest(media)
            report = verifier.verify_result(self.result_for(media), root, "ffprobe", 1024 * 1024)
            self.assertEqual("media_obtained", report["evidence_level"])
            self.assertFalse(report["content_verified"])
            self.assertEqual(before, digest(media))
            self.assertEqual(["ffprobe", "-v"], run_mock.call_args.args[0][:2])

    @unittest.skipUnless(shutil.which("ffprobe"), "ffprobe is not installed")
    def test_real_ffprobe_accepts_temporary_wav(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            media = self.make_media(root)
            report = verifier.verify_result(self.result_for(media), root, "ffprobe", 1024 * 1024)
            self.assertEqual("media_obtained", report["evidence_level"])
            self.assertGreater(report["files"][0]["duration_seconds"], 0.9)

    @mock.patch.object(verifier.subprocess, "run", return_value=fake_probe())
    def test_repeated_validation_is_idempotent(self, _run_mock):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            media = self.make_media(root)
            result = self.result_for(media)
            first = verifier.verify_result(result, root, "ffprobe", 1024 * 1024)
            second = verifier.verify_result(result, root, "ffprobe", 1024 * 1024)
            self.assertEqual(first, second)

    @mock.patch.object(verifier.subprocess, "run", return_value=fake_probe())
    def test_path_outside_output_root_is_rejected(self, _run_mock):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw).resolve()
            root = base / "output"
            root.mkdir()
            outside = self.make_media(base, "outside.wav")
            with self.assertRaisesRegex(verifier.ValidationError, "outside"):
                verifier.verify_result(self.result_for(outside), root, "ffprobe", 1024 * 1024)

    @mock.patch.object(verifier.subprocess, "run", return_value=fake_probe())
    def test_same_path_cannot_be_repeated(self, _run_mock):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            media = self.make_media(root)
            result = self.result_for(media)
            result["files"].append(dict(result["files"][0]))
            with self.assertRaisesRegex(verifier.ValidationError, "repeats"):
                verifier.verify_result(result, root, "ffprobe", 1024 * 1024)

    @mock.patch.object(verifier.subprocess, "run", return_value=fake_probe())
    def test_hash_mismatch_is_rejected(self, _run_mock):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            media = self.make_media(root)
            result = self.result_for(media)
            result["files"][0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(verifier.ValidationError, "SHA-256"):
                verifier.verify_result(result, root, "ffprobe", 1024 * 1024)

    @mock.patch.object(verifier.subprocess, "run", return_value=fake_probe())
    def test_size_limit_is_enforced(self, _run_mock):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            media = self.make_media(root)
            with self.assertRaisesRegex(verifier.ValidationError, "byte limit"):
                verifier.verify_result(self.result_for(media), root, "ffprobe", 16)

    @mock.patch.object(verifier.subprocess, "run", return_value=fake_probe(returncode=1))
    def test_damaged_media_is_rejected(self, _run_mock):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            media = self.make_media(root)
            with self.assertRaisesRegex(verifier.ValidationError, "ffprobe rejected"):
                verifier.verify_result(self.result_for(media), root, "ffprobe", 1024 * 1024)

    def test_concurrent_media_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            media = self.make_media(root)
            result = self.result_for(media)

            def mutate_during_probe(*_args, **_kwargs):
                with media.open("ab") as handle:
                    handle.write(b"changed")
                return fake_probe()

            with mock.patch.object(verifier.subprocess, "run", side_effect=mutate_during_probe):
                with self.assertRaisesRegex(verifier.ValidationError, "changed during validation"):
                    verifier.verify_result(result, root, "ffprobe", 1024 * 1024)

    def test_malformed_and_oversized_result_json_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            malformed = root / "malformed.json"
            malformed.write_text("{broken", encoding="utf-8")
            with self.assertRaises(verifier.ValidationError):
                verifier.load_result(malformed)
            oversized = root / "oversized.json"
            oversized.write_bytes(b"x" * (verifier.MAX_RESULT_BYTES + 1))
            with self.assertRaisesRegex(verifier.ValidationError, "2 MiB"):
                verifier.load_result(oversized)

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlinks are unavailable")
    @mock.patch.object(verifier.subprocess, "run", return_value=fake_probe())
    def test_symbolic_link_is_rejected_when_supported(self, _run_mock):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            media = self.make_media(root)
            link = root / "linked.wav"
            try:
                link.symlink_to(media)
            except OSError:
                self.skipTest("current account cannot create symlinks")
            result = self.result_for(media)
            result["files"][0]["path"] = str(link)
            with self.assertRaisesRegex(verifier.ValidationError, "link or reparse"):
                verifier.verify_result(result, root, "ffprobe", 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
