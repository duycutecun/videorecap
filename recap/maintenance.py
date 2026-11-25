"""Bảo trì: tự xoá job cũ, cập nhật yt-dlp, cập nhật tool qua git."""
from __future__ import annotations

import shutil
import subprocess
import sys
import time

from . import settings
from .paths import IS_WINDOWS, JOBS_DIR, ROOT

_NOWIN = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}


def cleanup(active_ids: set[str] | None = None) -> list[str]:
    cfg = settings.get("retention", {}) or {}
    if not cfg.get("enabled"):
        return []
    days = float(cfg.get("days", 30))
    limit = time.time() - days * 86400
    removed = []
    active_ids = active_ids or set()
    for d in JOBS_DIR.iterdir() if JOBS_DIR.exists() else []:
        if not d.is_dir() or d.name in active_ids or not (d / "job.json").exists():
            continue
        if (d / "job.json").stat().st_mtime < limit:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
    return removed


def _run(cmd: list[str], cwd=None, timeout=1800) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, **_NOWIN)
    return r.returncode, ((r.stdout or "") + "\n" + (r.stderr or "")).strip()[-3000:]


def _pip_install(args: list[str]) -> tuple[int, str]:
    local_uv = ROOT / "tools" / "uv" / ("uv.exe" if IS_WINDOWS else "uv")
    uv = str(local_uv) if local_uv.exists() else shutil.which("uv")
    if uv:
        return _run([uv, "pip", "install", "--python", sys.executable, *args], cwd=ROOT)
    return _run([sys.executable, "-m", "pip", "install", *args], cwd=ROOT)


def update_ytdlp() -> tuple[int, str]:
    return _pip_install(["-U", "yt-dlp[default]", "edge-tts"])


def update_tool() -> tuple[int, str]:
    if not (ROOT / ".git").exists() or not shutil.which("git"):
        return 1, "Thư mục tool không phải bản git (hoặc máy chưa cài Git). Hãy cập nhật bằng file zip mới."
    rc, out = _run(["git", "pull", "--ff-only"], cwd=ROOT)
    if rc != 0:
        return rc, out
    rc2, out2 = _pip_install(["-r", str(ROOT / "requirements.txt")])
    return rc2, out + "\n" + out2 + "\n\nHãy tắt và mở lại run.bat để dùng bản mới."


def install_vieneu() -> tuple[int, str]:
    """Cài VieNeu-TTS bản CPU (ONNX, không cần torch). Lần đầu chạy sẽ tự tải model."""
    return _pip_install(["-U", "vieneu"])


def vieneu_voices() -> list[str]:
    import json
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + (__import__("os").pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    try:
        from .tts import vieneu_mode
        r = subprocess.run([sys.executable, "-m", "recap.vieneu_worker", "--list-voices", "--mode", vieneu_mode()],
                           capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600, env=env, **_NOWIN)
    except Exception:  # noqa: BLE001
        return []
    for line in (r.stdout or "").splitlines():
        if line.startswith("VOICES "):
            try:
                return json.loads(line[7:])
            except json.JSONDecodeError:
                return []
    return []
