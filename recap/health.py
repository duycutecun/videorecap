"""Kiểm tra máy: GPU, ffmpeg, agent, model, giọng đọc."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from . import media, settings
from .paths import IS_WINDOWS, MODELS_DIR, ROOT

_NOWIN = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}


def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, **_NOWIN)
        return r.returncode, (r.stdout or r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def check() -> list[dict]:
    items: list[dict] = []

    def add(name: str, ok: bool | None, detail: str, fix: str = ""):
        items.append({"name": name, "ok": ok, "detail": detail, "fix": fix})

    # GPU
    if shutil.which("nvidia-smi"):
        rc, out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])
        add("GPU NVIDIA", rc == 0, out.splitlines()[0] if out else "không đọc được",
            "Cài/cập nhật driver NVIDIA mới nhất.")
    else:
        add("GPU NVIDIA", False, "không thấy nvidia-smi — sẽ chạy bằng CPU (chậm)", "Cài driver NVIDIA.")
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
        add("CUDA cho Whisper", n > 0, f"{n} GPU dùng được" if n else "không dùng được CUDA — Whisper chạy CPU",
            "Chạy lại install.bat để cài thư viện cuBLAS/cuDNN.")
    except Exception as e:  # noqa: BLE001
        add("CUDA cho Whisper", False, f"lỗi: {e}", "Chạy lại install.bat.")

    # ffmpeg
    try:
        rc, out = _run([media.ffmpeg(), "-version"])
        add("ffmpeg", rc == 0, out.splitlines()[0][:80] if out else "")
        add("Mã hoá bằng GPU (NVENC)", media.nvenc_available(), "có" if media.nvenc_available() else "không — dùng CPU (libx264)")
    except Exception as e:  # noqa: BLE001
        add("ffmpeg", False, str(e), "Chạy lại install.bat.")

    # yt-dlp
    rc, out = _run([sys.executable, "-m", "yt_dlp", "--version"])
    add("Bộ tải video (yt-dlp)", rc == 0, out or "chưa cài", "Cài đặt → Cập nhật bộ tải video.")

    # Agent
    cfg = settings.get("agent", {})
    for prov, tmpl in (cfg.get("commands") or {}).items():
        exe = shutil.which(tmpl[0]) if tmpl else None
        chosen = " (đang chọn)" if prov == cfg.get("provider") else ""
        if exe:
            rc, out = _run([exe, "--version"])
            add(f"Agent {prov}{chosen}", True, (out.splitlines()[0] if out else exe)[:80],
                f"Nhớ đăng nhập một lần: mở terminal, gõ `{tmpl[0]}` rồi làm theo hướng dẫn đăng nhập.")
        else:
            add(f"Agent {prov}{chosen}", False if chosen else None, "chưa cài",
                "Xem README mục 'Cài agent'." if chosen else "")

    # Whisper model / API nhận dạng
    eng = settings.get("transcribe.engine", "local")
    if eng != "local":
        k = (settings.get("transcribe.api_keys", {}) or {}).get(eng)
        add(f"Nhận dạng lời thoại: API {eng}", bool(k), "đã có API key" if k else "chưa có API key",
            "Nhập API key trong Cài đặt → Nhận dạng lời thoại.")
    wdir = MODELS_DIR / "whisper"
    has = wdir.is_dir() and any(wdir.rglob("model.bin"))
    add("Model nhận dạng lời thoại", has, "đã tải" if has else "chưa tải (sẽ tự tải lần đầu, vài GB)",
        "Chạy: run.bat → hoặc `python -m recap fetch-models`.")

    # TTS
    tcfg = settings.get("tts", {})
    prov = tcfg.get("provider")
    pc = (tcfg.get("providers") or {}).get(prov, {})
    import importlib.util
    from .tts import omnivoice_health
    oh = omnivoice_health()
    ok_omni = bool(oh and oh.get("status") == "ready")
    add("Giọng OmniVoice (GPU)", ok_omni if prov == "omnivoice" else (True if ok_omni else None),
        f"sẵn sàng trên {oh.get('device')}, giọng: {', '.join(v[-1] for v in oh.get('voices') or [])}" if ok_omni
        else (f"đang nạp ({oh.get('status')})" if oh else "dịch vụ chưa chạy"),
        "Bật START-OMNIVOICE.bat trong thư mục tool (hoặc chạy install.bat trên máy có GPU NVIDIA).")
    has_vn = importlib.util.find_spec("vieneu") is not None
    add("Giọng VieNeu", has_vn if prov == "vieneu" else (True if has_vn else None),
        "đã cài" if has_vn else "chưa cài (tuỳ chọn)", "Bấm 'Cài VieNeu' ở trang này.")
    if prov == "piper":
        m = Path(pc.get("model") or "")
        m = m if m.is_absolute() else ROOT / m
        add("Giọng đọc: piper", m.exists(), str(m.name) if m.exists() else "chưa có model giọng",
            "Chạy lại install.bat hoặc chọn nhà cung cấp giọng khác.")
    elif prov in ("openai", "elevenlabs"):
        add(f"Giọng đọc: {prov}", bool(pc.get("api_key")), "đã có API key" if pc.get("api_key") else "chưa có API key",
            "Nhập API key trong Cài đặt.")
    else:
        add(f"Giọng đọc: {prov}", None, "cần mạng" if prov == "edge" else "lệnh tuỳ chỉnh")

    # Git (cho nút cập nhật tool)
    add("Git (cập nhật tool)", bool(shutil.which("git")) and (ROOT / ".git").exists(),
        "sẵn sàng" if (ROOT / ".git").exists() else "thư mục tool không phải bản git — cập nhật bằng file zip",
        "Cài Git: winget install Git.Git")
    return items


def print_report() -> int:
    bad = 0
    for it in check():
        mark = "OK " if it["ok"] else ("-- " if it["ok"] is None else "LỖI")
        print(f"[{mark}] {it['name']}: {it['detail']}")
        if it["ok"] is False and it["fix"]:
            print(f"       → {it['fix']}")
            bad += 1
    return bad
