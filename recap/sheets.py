"""Bảng ảnh (contact sheet) phủ TOÀN BỘ video: cứ vài giây (mặc định 1 giây) lấy một khung hình, ghép thành lưới
(mặc định 4x4) kèm mốc giây.

Agent nhìn bảng ảnh để biết chính xác giây nào đang diễn ra việc gì → chọn clip đúng hành động, đúng thời điểm.
Một lần giải mã video duy nhất bằng ffmpeg (fps → scale → drawtext → tile).
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import threading
import time
from collections import deque

from . import media, settings
from .paths import IS_WINDOWS
from .project import Cancelled, ProcessRunner, Project

_NOWIN = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}


def _cfg() -> dict:
    return settings.get("frames", {}) or {}


def grid() -> tuple[int, int]:
    """Số cột x hàng mỗi bảng ảnh (mặc định 4x4 = 16 khung/bảng)."""
    cfg = _cfg()
    c = int(cfg.get("sheet_cols") or 4)
    r = int(cfg.get("sheet_rows") or 4)
    return max(1, min(8, c)), max(1, min(8, r))


def interval_for(duration: float) -> float:
    """Khoảng cách giữa hai khung. Cố định nếu đặt sheet_interval_seconds; không thì tự tính:
    dày nhất là sheet_min_interval (mặc định 1 giây), thưa dần khi video dài để không vượt sheet_max_cells."""
    cfg = _cfg()
    fixed = float(cfg.get("sheet_interval_seconds") or 0)
    if fixed > 0:
        return max(0.25, fixed)
    max_cells = int(cfg.get("sheet_max_cells", 1800) or 1800)
    min_iv = max(0.25, float(cfg.get("sheet_min_interval", 1.0) or 1.0))
    return max(min_iv, round(duration / max(1, max_cells), 1))


def overview_enabled() -> bool:
    return bool(_cfg().get("overview", True))


def overview_grid() -> tuple[int, int]:
    cfg = _cfg()
    c = int(cfg.get("overview_cols") or 5)
    r = int(cfg.get("overview_rows") or 5)
    return max(1, min(8, c)), max(1, min(8, r))


def overview_interval(duration: float) -> float:
    """Bảng TỔNG QUAN (agent xem hết): thưa, để cả video chỉ còn khoảng overview_max_cells khung.
    Ví dụ video 47 phút → 5 giây/khung → ~23 bảng 5x5 thay vì 355 bảng chi tiết."""
    cfg = _cfg()
    max_cells = int(cfg.get("overview_max_cells", 600) or 600)
    min_iv = max(0.5, float(cfg.get("overview_min_interval", 2.0) or 2.0))
    iv = max(min_iv, duration / max(1, max_cells))
    return math.ceil(iv * 2) / 2  # làm tròn lên 0.5 giây cho nhãn dễ đọc


def _label(iv: float, fontsize: int = 22) -> str:
    # mốc giây: số nguyên khi khoảng cách là số nguyên, có 1 số lẻ (vd 12.5s) khi không
    if float(iv).is_integer():
        ts = r"%{eif\:t\:d}"
    else:
        ts = r"%{eif\:floor(t)\:d}.%{eif\:mod(floor(t*10)\,10)\:d}"
    return f"drawtext=text='{ts}s':x=6:y=6:fontsize={fontsize}:fontcolor=yellow:box=1:boxcolor=black@0.65,"


def _spec(d, iv: float, cols: int, rows: int, width: int, dur: float, prefix: str, fontsize: int) -> dict:
    per = cols * rows
    return {"dir": d, "iv": iv, "cols": cols, "rows": rows, "width": width, "prefix": prefix, "fontsize": fontsize,
            "expected": math.ceil((int(dur / iv) + 1) / per)}


def _index_ok(f, iv: float, cols: int, rows: int) -> bool:
    if not f.exists():
        return False
    try:
        old = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return False
    return abs(float(old.get("interval", 0)) - iv) < 0.05 and old.get("cols") == cols and old.get("rows") == rows


def make(project: Project, runner: ProcessRunner | None = None) -> dict:
    """Tạo bảng CHI TIẾT (sheets/, dày) và bảng TỔNG QUAN (overview/, thưa) trong MỘT lần giải mã video.
    Agent xem hết bảng tổng quan, chỉ mở bảng chi tiết cho đoạn cần căn giây chính xác → đỡ rất nhiều token."""
    src = project.source()
    dur = float(project.state().get("source_duration") or media.duration(src))
    cols, rows = grid()
    iv = interval_for(dur)
    d = project.f("sheets")
    need: list[dict] = []
    if not _index_ok(project.f("sheets", "index.json"), iv, cols, rows):
        dense = _spec(d, iv, cols, rows, int(_cfg().get("sheet_cell_width", 320) or 320), dur, "sheet", 22)
        have = sorted(d.glob("sheet_*.jpg")) if d.exists() else []
        if not project.f("sheets", "index.json").exists() and have and len(have) == dense["expected"] \
                and all(f.stat().st_size > 0 for f in have):
            # Lần trước ffmpeg đã ghi đủ nhưng bị treo/huỷ trước khi kịp ghi index.json → dùng lại luôn
            project.log(f"Đã có đủ {len(have)} bảng ảnh chi tiết từ lần trước — dùng lại.")
            _write_index(project, dense)
        else:
            if project.f("sheets", "index.json").exists():
                project.log("Cài đặt bảng ảnh chi tiết đã đổi — tạo lại.")
            need.append(dense)
    if overview_enabled():
        ocols, orows = overview_grid()
        oiv = overview_interval(dur)
        if not _index_ok(project.f("overview", "index.json"), oiv, ocols, orows):
            need.append(_spec(project.f("overview"), oiv, ocols, orows,
                              int(_cfg().get("overview_cell_width", 256) or 256), dur, "overview", 18))
    if need:
        _render(project, runner, src, need)
        for sp in need:
            _write_index(project, sp)
    return json.loads(project.f("sheets", "index.json").read_text(encoding="utf-8"))


def _render(project: Project, runner: ProcessRunner | None, src, specs: list[dict]) -> None:
    for sp in specs:
        if sp["dir"].exists():
            shutil.rmtree(sp["dir"], ignore_errors=True)
        sp["dir"].mkdir()
        kind = "chi tiết" if sp["prefix"] == "sheet" else "tổng quan"
        project.log(f"Tạo bảng ảnh {kind}: cứ {sp['iv']:g} giây một khung, {sp['cols']}x{sp['rows']} khung mỗi bảng "
                    f"(~{sp['expected']} bảng)…")
    # canh chừng theo bảng có nhiều file nhất (thường là bảng chi tiết)
    watch = max(specs, key=lambda x: x["expected"])
    attempts = [(["-hwaccel", "auto"], True, "GPU"), ([], True, "CPU"), ([], False, "CPU, không nhãn giây")]
    for hw, use_label, name in attempts:
        chains, maps = [], []
        n = len(specs)
        head = f"[0:v]split={n}" + "".join(f"[s{i}]" for i in range(n)) + ";" if n > 1 else ""
        for i, sp in enumerate(specs):
            lab = _label(sp["iv"], sp["fontsize"]) if use_label else ""
            inp = f"[s{i}]" if n > 1 else "[0:v]"
            chains.append(f"{inp}fps=1/{sp['iv']}:round=down,scale={sp['width']}:-2,{lab}"
                          f"tile={sp['cols']}x{sp['rows']}:padding=4:color=white[o{i}]")
            maps += ["-map", f"[o{i}]", "-q:v", "4", str(sp["dir"] / f"{sp['prefix']}_%04d.jpg")]
        cmd = [media.ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *hw, "-i", str(src),
               "-an", "-sn", "-filter_complex", head + ";".join(chains), *maps]
        rc, err = _run_watched(project, runner, cmd, watch["dir"], watch["expected"], watch["prefix"])
        if rc == 0 and all(any(sp["dir"].glob(f"{sp['prefix']}_*.jpg")) for sp in specs):
            return
        project.log(f"  ffmpeg ({name}) lỗi khi tạo bảng ảnh (mã {rc}): {err or 'không có thông báo'}")
        for sp in specs:
            for f in sp["dir"].glob(f"{sp['prefix']}_*.jpg"):
                f.unlink()
    raise RuntimeError("ffmpeg lỗi khi tạo bảng ảnh.")


def _run_watched(project: Project, runner: ProcessRunner | None, cmd: list[str], d, expected: int,
                 prefix: str = "sheet", idle_after_done: float = 20.0, stall: float = 600.0) -> tuple[int, str]:
    """Chạy ffmpeg và canh chừng. ffmpeg trên Windows đôi khi ghi xong bảng cuối rồi KHÔNG tự thoát
    (gặp với video dài, giải mã GPU) → khi đã đủ số bảng và không ghi thêm gì nữa thì tool tự dừng nó.
    Trả về (mã thoát, vài dòng lỗi cuối)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", **_NOWIN)
    tail: deque[str] = deque(maxlen=6)

    def pump() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            if line.strip():
                tail.append(line.strip())
    th = threading.Thread(target=pump, daemon=True)
    th.start()
    if runner:
        runner.proc = proc
    last_count, last_change, last_logged = 0, time.time(), 0
    forced = False
    try:
        while proc.poll() is None:
            time.sleep(2)
            if runner and runner.cancelled:
                proc.kill()
                raise Cancelled()
            count = sum(1 for _ in d.glob(f"{prefix}_*.jpg"))
            now = time.time()
            if count != last_count:
                last_count, last_change = count, now
                if count - last_logged >= 50:
                    project.log(f"  bảng ảnh: {count}/{expected}")
                    last_logged = count
            idle = now - last_change
            # bảng cuối chỉ được ghi khi ffmpeg đọc hết video; thiếu 1 bảng (do làm tròn) thì đợi lâu hơn cho chắc
            if (count >= expected and idle > idle_after_done) or (count >= expected - 1 > 0 and idle > 90):
                project.log(f"  ffmpeg đã ghi đủ {count} bảng nhưng không tự thoát — tool tự dừng nó.")
                forced = True
                proc.kill()
                break
            if idle > stall:
                project.log(f"  ffmpeg đứng yên {int(stall // 60)} phút ở {count}/{expected} bảng — dừng và thử cách khác.")
                proc.kill()
                break
        proc.wait(timeout=30)
    finally:
        if runner:
            runner.proc = None
    th.join(timeout=5)
    if forced:
        return 0, ""
    return proc.returncode, " | ".join(tail)


def _write_index(project: Project, sp: dict) -> dict:
    d, iv, cols, rows = sp["dir"], sp["iv"], sp["cols"], sp["rows"]
    dur = float(project.state().get("source_duration") or media.duration(project.source()))
    per = cols * rows
    files = sorted(d.glob(f"{sp['prefix']}_*.jpg"))
    sheets = []
    for i, f in enumerate(files):
        times = [round((i * per + k) * iv, 2) for k in range(per) if (i * per + k) * iv < dur]
        sheets.append({"file": f"{d.name}/{f.name}", "times": times})
    idx = {"interval": iv, "cols": cols, "rows": rows, "sheets": sheets}
    (d / "index.json").write_text(json.dumps(idx, indent=1), encoding="utf-8")
    kind = "chi tiết" if sp["prefix"] == "sheet" else "tổng quan"
    project.log(f"Xong bảng ảnh {kind}: {len(sheets)} bảng ({sum(len(s['times']) for s in sheets)} khung).")
    return idx


def load_overview(project: Project) -> dict:
    f = project.f("overview", "index.json")
    if overview_enabled() and f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {}


def sheets_for_range(idx: dict, a: float, b: float) -> list[str]:
    out = []
    for s in idx.get("sheets", []):
        t = s["times"]
        if t and t[0] <= b and t[-1] + idx.get("interval", 0) >= a:
            out.append(f"{s['file']} ({t[0]:g}s-{t[-1]:g}s)")
    return out
