"""Hàm tiện ích ffmpeg / ffprobe."""
from __future__ import annotations

import functools
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import paths  # noqa: F401  (đảm bảo PATH đã có ffmpeg portable)
from .paths import IS_WINDOWS

_NOWIN = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("Không tìm thấy ffmpeg. Hãy chạy lại install.bat.")
    return exe


def ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("Không tìm thấy ffprobe. Hãy chạy lại install.bat.")
    return exe


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        [ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **_NOWIN,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe lỗi với {path}: {out.stderr.strip()[:500]}")
    return json.loads(out.stdout or "{}")


def duration(path: Path) -> float:
    info = probe(path)
    d = info.get("format", {}).get("duration")
    if d is None:
        for s in info.get("streams", []):
            if s.get("duration"):
                d = s["duration"]
                break
    return float(d or 0.0)


def has_audio(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path).get("streams", []))


# NVENC chất lượng cao cho card Turing trở lên (RTX 20xx): AQ theo không gian + thời gian, B-frame làm tham chiếu.
# Nhanh gần bằng p4 nhưng mịn hơn rõ ở cảnh tối / mảng màu phẳng (đỡ vỡ khối khi lên YouTube nén lại).
_NVENC_HQ = ["-preset", "p5", "-tune", "hq", "-rc", "vbr", "-b:v", "0", "-spatial-aq", "1", "-temporal-aq", "1",
             "-aq-strength", "8", "-bf", "3", "-b_ref_mode", "middle", "-rc-lookahead", "20"]
_NVENC_BASIC = ["-preset", "p4", "-rc", "vbr", "-b:v", "0"]


def _nvenc_try(extra: list[str]) -> bool:
    try:
        r = subprocess.run(
            [ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.5",
             "-c:v", "h264_nvenc", *extra, "-cq", "23", "-f", "null", "-"],
            capture_output=True, timeout=30, **_NOWIN,
        )
        return r.returncode == 0
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def nvenc_mode() -> str:
    """'hq' (RTX 20xx trở lên) | 'basic' (card NVIDIA cũ) | '' (không dùng được NVENC)."""
    if _nvenc_try(_NVENC_HQ):
        return "hq"
    return "basic" if _nvenc_try(_NVENC_BASIC) else ""


def nvenc_available() -> bool:
    """Thử mã hoá vài khung hình bằng h264_nvenc để biết máy có dùng được GPU NVIDIA không."""
    return bool(nvenc_mode())


def video_encoder_args(setting: str, crf: int, quality: str | None = None) -> list[str]:
    """quality: None/"high" = đẹp nhất máy làm được (NVENC HQ, libx264 medium); "fast" = ưu tiên tốc độ."""
    fast = (quality or "").lower() in ("fast", "low", "draft", "nhanh")
    use_nvenc = setting == "h264_nvenc" or (setting == "auto" and nvenc_available())
    if use_nvenc:
        opts = _NVENC_HQ if (nvenc_mode() == "hq" and not fast) else _NVENC_BASIC
        return ["-c:v", "h264_nvenc", *opts, "-cq", str(crf + 3), "-pix_fmt", "yuv420p"]
    preset = "medium" if (quality or "").lower() == "high" else "veryfast"
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]


def cuda_decode_args() -> list[str]:
    """Giải mã video nguồn bằng GPU NVIDIA (NVDEC) — đỡ cho CPU. Codec GPU không hỗ trợ thì ffmpeg tự quay về CPU."""
    return ["-hwaccel", "cuda"] if nvenc_available() else []


@functools.lru_cache(maxsize=1)
def total_ram_gb() -> float:
    """Tổng RAM máy (GB). Lỗi thì trả 0."""
    try:
        if IS_WINDOWS:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 1024 ** 3
        import os
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except Exception:  # noqa: BLE001
        return 0.0


def cpu_parallel(small: int, big: int) -> int:
    """Máy RAM lớn (≥ 48 GB — thường là cắm đủ nhiều thanh, nhiều kênh RAM) thì chạy song song nhiều hơn;
    RAM ít (thường 1–2 thanh) thì giữ mức thấp để không nghẽn băng thông RAM."""
    return big if total_ram_gb() >= 48 else small


def parallel_jobs(setting, gpu: int = 4, cpu: int = 6) -> int:
    """Số tiến trình ffmpeg chạy song song. 0/trống = tự chọn.
    GPU: card GeForce cho tối đa 8 phiên NVENC cùng lúc → 4 là an toàn.
    CPU: 2 Xeon 48 luồng nhưng RAM chạy 1 kênh → hơn 6 tiến trình là nghẽn RAM, không nhanh thêm."""
    try:
        n = int(setting or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return min(n, 16)
    return gpu if nvenc_available() else cpu_parallel(cpu, 12)


def fmt_ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:04.1f}"


def srt_ts(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def read_text_any(path: Path) -> str:
    """Đọc file chữ do agent/PowerShell ghi: UTF-8 (có/không BOM), UTF-16, hoặc ANSI."""
    b = Path(path).read_bytes()
    if b.startswith(b"\xef\xbb\xbf"):
        return b[3:].decode("utf-8", errors="replace")
    if b.startswith((b"\xff\xfe", b"\xfe\xff")):
        return b.decode("utf-16", errors="replace")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1258", errors="replace")


def write_text_bom(path: Path, text: str) -> None:
    """Ghi UTF-8 có BOM cho file .md/.txt để PowerShell 5.1 (Get-Content) đọc đúng tiếng Việt."""
    Path(path).write_text(text, encoding="utf-8-sig")
