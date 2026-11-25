"""Mở video bằng phần mềm chỉnh sửa có giao diện (LosslessCut — giao diện đồ hoạ cho ffmpeg)."""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional

from . import settings
from .paths import IS_WINDOWS, TOOLS_DIR

LC_URL = "https://github.com/mifi/lossless-cut/releases/latest/download/LosslessCut-win-x64.7z"
LC_DIR = TOOLS_DIR / "LosslessCut"


def find_losslesscut() -> Optional[Path]:
    custom = (settings.get("editors.losslesscut_path") or "").strip()
    if custom and Path(custom).is_file():
        return Path(custom)
    cands: list[str] = [str(p) for p in LC_DIR.rglob("LosslessCut.exe")] if LC_DIR.exists() else []
    la = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", "")
    for pat in (f"{la}/Programs/LosslessCut/LosslessCut.exe", f"{pf}/LosslessCut/LosslessCut.exe",
                f"{la}/Microsoft/WinGet/Packages/*LosslessCut*/**/LosslessCut.exe",
                f"{os.path.expanduser('~')}/PortableApps/LosslessCutPortable/**/LosslessCut*.exe"):
        if la or pf:
            cands += glob.glob(pat, recursive=True)
    w = shutil.which("LosslessCut") or shutil.which("losslesscut")
    if w:
        cands.append(w)
    for c in cands:
        if c and Path(c).is_file():
            return Path(c)
    return None


def open_in_losslesscut(file: Path) -> None:
    exe = find_losslesscut()
    if not exe:
        raise FileNotFoundError("Chưa có LosslessCut")
    kw = {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)} if IS_WINDOWS else {"start_new_session": True}
    subprocess.Popen([str(exe), str(file)], cwd=str(file.parent), **kw)


SEVENZR_URL = "https://www.7-zip.org/a/7zr.exe"


def _find_7z() -> Optional[str]:
    """Tìm công cụ giải nén .7z: 7-Zip đã cài, hoặc 7zr.exe (bản độc lập chính thức) trong tools/."""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    for c in (str(TOOLS_DIR / "7zr.exe"), f"{pf}/7-Zip/7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe",
              shutil.which("7z") or "", shutil.which("7zr") or ""):
        if c and Path(c).is_file():
            return c
    return None


def _download(url: str, out: Path, log=print) -> None:
    tmp = out.with_suffix(out.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as fh:
        shutil.copyfileobj(r, fh, length=4 * 1024 * 1024)
    tmp.replace(out)


def install_losslesscut(log=print) -> tuple[bool, str]:
    """Tải bản portable chính thức từ GitHub (~230 MB) và giải nén vào tools/LosslessCut."""
    if not IS_WINDOWS:
        return False, "Chỉ hỗ trợ cài tự động trên Windows."
    if find_losslesscut():
        return True, f"Đã có LosslessCut: {find_losslesscut()}"
    LC_DIR.mkdir(parents=True, exist_ok=True)
    arc = TOOLS_DIR / "LosslessCut-win-x64.7z"
    try:
        # 1) Công cụ giải nén (bản LosslessCut mới nén kiểu BCJ2 — thư viện Python không đọc được)
        seven = _find_7z()
        if not seven:
            log("Tải công cụ giải nén 7zr.exe (7-zip.org, ~600 KB)…")
            _download(SEVENZR_URL, TOOLS_DIR / "7zr.exe", log)
            seven = str(TOOLS_DIR / "7zr.exe")
        # 2) Tải LosslessCut (giữ lại file nếu đã tải xong lần trước)
        if not arc.exists() or arc.stat().st_size < 50_000_000:
            log("Đang tải LosslessCut (~230 MB) từ GitHub…")
            _download(LC_URL, arc, log)
        # 3) Giải nén
        log("Đang giải nén…")
        r = subprocess.run([seven, "x", "-y", f"-o{LC_DIR}", str(arc)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout)[-400:])
    except Exception as e:  # noqa: BLE001
        return False, (f"Không cài được LosslessCut: {e}. Có thể tự tải tại github.com/mifi/lossless-cut/releases "
                       f"(file LosslessCut-win-x64.7z), giải nén bằng 7-Zip vào {LC_DIR}.")
    exe = find_losslesscut()
    if exe:
        try:
            arc.unlink()
        except OSError:
            pass
        return True, f"Đã cài LosslessCut: {exe}"
    return False, "Giải nén xong nhưng không thấy LosslessCut.exe."
