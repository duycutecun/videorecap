"""Đường dẫn của tool và môi trường chạy (PATH cho ffmpeg, thư viện CUDA)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
MODELS_DIR = ROOT / "models"
JOBS_DIR = ROOT / "jobs"
CONFIG_DIR = ROOT / "config"
PRESETS_DIR = ROOT / "presets"
WEB_DIR = ROOT / "web"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
SETTINGS_EXAMPLE = CONFIG_DIR / "settings.example.json"
IS_WINDOWS = os.name == "nt"


def _extra_bin_dirs() -> list[Path]:
    dirs: list[Path] = []
    # ffmpeg bản portable do install.ps1 giải nén vào tools/ffmpeg/bin
    ff = TOOLS_DIR / "ffmpeg" / "bin"
    if ff.is_dir():
        dirs.append(ff)
    # Thư viện cuBLAS / cuDNN cài bằng pip (nvidia-*-cu12) cho faster-whisper trên Windows
    for sp in sys.path:
        nv = Path(sp) / "nvidia"
        if nv.is_dir():
            for sub in nv.iterdir():
                b = sub / "bin"
                if b.is_dir():
                    dirs.append(b)
    return dirs


def setup_env() -> None:
    """Thêm ffmpeg portable + thư viện CUDA vào PATH của tiến trình hiện tại (và tiến trình con)."""
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep)
    for d in _extra_bin_dirs():
        s = str(d)
        if s not in parts:
            parts.insert(0, s)
            if IS_WINDOWS and hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(s)
                except OSError:
                    pass
    os.environ["PATH"] = os.pathsep.join(parts)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def python_exe() -> str:
    return sys.executable


setup_env()
