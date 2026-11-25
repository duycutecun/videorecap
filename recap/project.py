"""Thư mục dự án cho từng video (jobs/<id>/), file trạng thái job.json, log, chạy tiến trình con."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .paths import IS_WINDOWS, JOBS_DIR

# Cấu trúc một thư mục dự án:
#   job.json         trạng thái + tùy chọn
#   log.txt          toàn bộ log (tool + agent + ffmpeg)
#   source.mp4       video nguồn
#   transcript.json / transcript.txt
#   scenes.json
#   frames/          ảnh khung hình + context.md
#   TASK.md          nhiệm vụ cho agent
#   script.json      kịch bản (agent viết, người duyệt sửa)
#   tts/             file giọng đọc từng đoạn
#   render/          file trung gian khi dựng
#   output/final.mp4 video thành phẩm

STATUSES = ("queued", "running", "review", "rendering", "done", "error", "cancelled")


def new_job_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


class Project:
    def __init__(self, path: Path):
        self.path = Path(path)

    # ---------- tạo / mở ----------
    @classmethod
    def create(cls, options: dict[str, Any]) -> "Project":
        jid = new_job_id()
        p = JOBS_DIR / jid
        p.mkdir(parents=True, exist_ok=False)
        proj = cls(p)
        proj.write_state({
            "id": jid,
            "created": time.time(),
            "status": "queued",
            "stage": "Đang chờ",
            "error": None,
            "options": options,
            "title": options.get("title") or "",
        })
        return proj

    @classmethod
    def open(cls, job_id: str) -> "Project":
        if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
            raise FileNotFoundError(job_id)
        p = JOBS_DIR / job_id
        if not (p / "job.json").exists():
            raise FileNotFoundError(job_id)
        return cls(p)

    @classmethod
    def from_cwd(cls) -> "Project":
        """Dùng cho lệnh `recap ...` mà agent chạy bên trong thư mục dự án."""
        cwd = Path.cwd()
        for p in [cwd, *cwd.parents]:
            if (p / "job.json").exists():
                return cls(p)
        raise SystemExit("Không tìm thấy job.json. Hãy chạy lệnh bên trong thư mục dự án (jobs/<id>/).")

    # ---------- trạng thái ----------
    _lock = threading.RLock()

    @property
    def id(self) -> str:
        return self.path.name

    def state(self) -> dict[str, Any]:
        try:
            return json.loads((self.path / "job.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"id": self.id, "status": "error", "stage": "job.json hỏng", "options": {}}

    def write_state(self, data: dict[str, Any]) -> None:
        with self._lock:
            tmp = self.path / "job.json.tmp"
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path / "job.json")

    def update(self, **kw: Any) -> dict[str, Any]:
        with self._lock:
            st = self.state()
            st.update(kw)
            st["updated"] = time.time()
            self.write_state(st)
            return st

    @property
    def options(self) -> dict[str, Any]:
        return self.state().get("options", {})

    # ---------- file ----------
    def f(self, *parts: str) -> Path:
        return self.path.joinpath(*parts)

    def source(self) -> Optional[Path]:
        for name in ("source.mp4", "source.mkv", "source.webm", "source.mov"):
            p = self.f(name)
            if p.exists():
                return p
        for p in self.path.glob("source.*"):
            if p.suffix not in (".part", ".ytdl", ".json"):
                return p
        return None

    # ---------- log ----------
    def log(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        # Khi agent gọi lệnh recap, log đã được ghi vào log.txt — không in lại ra stdout để tránh lặp
        rp = os.environ.get("RECAP_PROJECT")
        if not rp or os.path.normcase(os.path.abspath(rp)) != os.path.normcase(os.path.abspath(self.path)):
            print(line, flush=True)
        with open(self.f("log.txt"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def log_tail(self, max_bytes: int = 20000) -> str:
        p = self.f("log.txt")
        if not p.exists():
            return ""
        size = p.stat().st_size
        with open(p, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            data = fh.read()
        return data.decode("utf-8", errors="replace")


# ---------- chạy tiến trình con (có huỷ được, có timeout, ghi log) ----------

class Cancelled(Exception):
    pass


class ProcessRunner:
    """Chạy lệnh, đẩy stdout/stderr vào log của dự án; huỷ được toàn bộ cây tiến trình."""

    def __init__(self, project: Optional[Project] = None):
        self.project = project
        self.proc: Optional[subprocess.Popen] = None
        self.cancelled = False
        self.children: list["ProcessRunner"] = []

    def child(self) -> "ProcessRunner":
        """Runner con cho tiến trình chạy song song; huỷ runner cha thì huỷ cả con."""
        c = ProcessRunner(self.project)
        c.cancelled = self.cancelled
        self.children.append(c)
        return c

    def _emit(self, line: str) -> None:
        if self.project:
            with open(self.project.f("log.txt"), "a", encoding="utf-8") as fh:
                fh.write(line)
        else:
            print(line, end="", flush=True)

    def run(self, cmd: list[str], cwd: Optional[Path] = None, timeout: Optional[float] = None,
            env: Optional[dict] = None, stdin_text: Optional[str] = None,
            on_line: Optional[Callable[[str], None]] = None) -> int:
        if self.cancelled:
            raise Cancelled()
        kwargs: dict[str, Any] = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(
            cmd, cwd=str(cwd) if cwd else None, env=env or os.environ.copy(),
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, **kwargs,
        )
        if stdin_text is not None:
            try:
                self.proc.stdin.write(stdin_text)
                self.proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        deadline = time.time() + timeout if timeout else None
        timed_out = threading.Event()

        def watchdog():
            while self.proc and self.proc.poll() is None:
                if self.cancelled or (deadline and time.time() > deadline):
                    if not self.cancelled:
                        timed_out.set()
                    self.kill()
                    return
                time.sleep(0.5)

        threading.Thread(target=watchdog, daemon=True).start()
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._emit(line)
            if on_line:
                on_line(line)
        rc = self.proc.wait()
        self.proc = None
        if self.cancelled:
            raise Cancelled()
        if timed_out.is_set():
            self._emit(f"\n[!] Quá thời gian chờ ({timeout:.0f}s) — đã dừng tiến trình.\n")
            return 124
        return rc

    # ---- chạy trong pseudo-terminal (Windows, cho agy: agy -p bị treo khi không có terminal thật) ----
    _ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>]")

    def run_pty(self, cmd: list[str], cwd: Optional[Path] = None, timeout: Optional[float] = None,
                env: Optional[dict] = None) -> int:
        if not IS_WINDOWS:
            return self.run(cmd, cwd=cwd, timeout=timeout, env=env)
        try:
            from winpty import PtyProcess  # pywinpty
        except ImportError:
            self._emit("[!] Chưa có pywinpty — chạy agent không qua terminal ảo.\n")
            return self.run(cmd, cwd=cwd, timeout=timeout, env=env)
        if self.cancelled:
            raise Cancelled()
        proc = PtyProcess.spawn(cmd, cwd=str(cwd) if cwd else None,
                                env=env or os.environ.copy(), dimensions=(50, 200))
        self._pty = proc
        deadline = time.time() + timeout if timeout else None
        timed_out = False
        buf = ""

        def reader(out: list):
            try:
                while True:
                    data = proc.read(4096)
                    if data:
                        out.append(data)
            except (EOFError, OSError):
                pass

        chunks: list[str] = []
        t = threading.Thread(target=reader, args=(chunks,), daemon=True)
        t.start()
        while t.is_alive():
            t.join(0.5)
            while chunks:
                buf += self._ANSI.sub("", chunks.pop(0)).replace("\r\n", "\n")
            if "\n" in buf:
                done, buf = buf.rsplit("\n", 1)
                lines = [ln.split("\r")[-1] for ln in done.split("\n")]
                self._emit("\n".join(ln for ln in lines if ln.strip()) + "\n")
            if self.cancelled or (deadline and time.time() > deadline):
                timed_out = not self.cancelled
                try:
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    proc.terminate(force=True)
                except Exception:
                    pass
                break
        if buf.strip():
            self._emit(buf + "\n")
        self._pty = None
        if self.cancelled:
            raise Cancelled()
        if timed_out:
            self._emit(f"\n[!] Quá thời gian chờ ({timeout:.0f}s) — đã dừng agent.\n")
            return 124
        try:
            proc.wait()
        except Exception:
            pass
        rc = proc.exitstatus
        return 0 if rc is None else int(rc)

    def kill(self) -> None:
        pty = getattr(self, "_pty", None)
        if pty is not None:
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(pty.pid)], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                pass
        p = self.proc
        if not p or p.poll() is not None:
            return
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)],
                               capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    def cancel(self) -> None:
        self.cancelled = True
        for c in self.children:
            c.cancel()
        self.kill()
