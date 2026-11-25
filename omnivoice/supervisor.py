"""Giữ omnivoice_server.py chạy ngầm; tự khởi động lại nếu gặp lỗi.
Dừng hẳn bằng TAT-OMNIVOICE.bat (tạo cờ STOP và dừng tiến trình).
"""
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STOP = HERE / "STOP"

logging.basicConfig(
    filename=str(HERE / "supervisor.log"),
    level=logging.INFO,
    encoding="utf-8",
    format=f"%(asctime)s [supervisor {os.getpid()}] %(message)s",
)


def _find_python() -> str:
    candidates = [
        ROOT / "tools" / "omni_venv" / "Scripts" / "pythonw.exe",
        HERE / ".venv" / "Scripts" / "pythonw.exe",
        ROOT / "tools" / "omni_venv" / "Scripts" / "python.exe",
        HERE / ".venv" / "Scripts" / "python.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return sys.executable


PYW = _find_python()
HF_DIR = ROOT / "models" / "hf"
HF_DIR.mkdir(parents=True, exist_ok=True)

env = os.environ.copy()
env.setdefault("HF_HOME", str(HF_DIR))
env["PYTHONIOENCODING"] = "utf-8"
env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

if STOP.exists():
    STOP.unlink()

fails = 0
while not STOP.exists():
    t0 = time.time()
    p = subprocess.Popen(
        [PYW, str(HERE / "omnivoice_server.py")],
        cwd=str(HERE),
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    logging.info("started server pid=%s using %s", p.pid, PYW)
    code = p.wait()
    up = time.time() - t0
    logging.info("server pid=%s exited code=%s (0x%08X) after %.0fs", p.pid, code, code & 0xFFFFFFFF, up)
    if STOP.exists():
        break
    fails = fails + 1 if up < 60 else 0
    time.sleep(min(60, 3 * (2 ** min(fails, 4))))

logging.info("STOP flag found - supervisor exits")
