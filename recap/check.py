"""`recap check` — tạo ảnh đối chiếu cho từng đoạn để agent TỰ SOÁT lời bình có khớp với hình không.

Mỗi đoạn → check/seg_XXX.jpg: lưới các khung hình lấy tại đầu / giữa / cuối của mỗi clip
(đúng phần sẽ được chiếu sau khi tool căn thời gian), kèm mốc giây. Agent mở ảnh, đọc lời bình
trong check.md và sửa clip nào không khớp.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import media, settings
from .paths import IS_WINDOWS
from .project import Project
from .script_schema import load
from .sync import _scenes, fit_clips, gap_seconds, speech_durations

_NOWIN = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}
def _cell() -> tuple[int, int]:
    """Kích thước mỗi khung trong ảnh đối chiếu. Nhỏ hơn = ít token hơn; 256x144 vẫn đủ nhận ra ai/làm gì."""
    w = int((settings.get("frames", {}) or {}).get("check_cell_width", 256) or 256)
    w = max(160, min(640, w)) // 2 * 2
    return w, (w * 9 // 16) // 2 * 2


def _frame(src: Path, t: float, out: Path, label: str) -> bool:
    W, H = _cell()
    base = [media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(src),
            "-frames:v", "1"]
    vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black"
    lab = f",drawtext=text='{label}':x=4:y=4:fontsize={max(14, W // 16)}:fontcolor=yellow:box=1:boxcolor=black@0.6"
    for f in (vf + lab, vf):  # máy không có font cho drawtext thì bỏ nhãn
        r = subprocess.run([*base, "-vf", f, str(out)], capture_output=True, **_NOWIN)
        if r.returncode == 0 and out.exists():
            return True
    return False


def _check_times(fitted: list[tuple[float, float]]) -> list[float]:
    """Khung đối chiếu: đầu + cuối mỗi clip và cứ check_step_seconds (mặc định 1 giây) một khung ở giữa,
    tối đa check_max_frames khung mỗi đoạn (lấy đều nếu vượt)."""
    cfg = settings.get("frames", {}) or {}
    step = max(0.25, float(cfg.get("check_step_seconds", 1.0) or 1.0))
    cap = max(3, int(cfg.get("check_max_frames", 12) or 12))
    times: list[float] = []
    for a, b in fitted:
        d = b - a
        if d <= 1.2:
            times.append(a + d / 2)
            continue
        s, e = a + 0.2, b - 0.2
        k = max(2, int((e - s) / step) + 1)
        times += [s + (e - s) * i / (k - 1) for i in range(k)]
    if len(times) > cap:
        times = [times[round(i * (len(times) - 1) / (cap - 1))] for i in range(cap)]
    return times


def _key(narration: str, fitted: list[tuple[float, float]], times: list[float]) -> str:
    return json.dumps([narration, [[round(a, 2), round(b, 2)] for a, b in fitted], [round(t, 2) for t in times],
                       _cell()], ensure_ascii=False)


def run(project: Project) -> tuple[Path, list[int]]:
    """Tạo ảnh đối chiếu. CHỈ làm lại ảnh cho đoạn mới/đã đổi (lời hoặc clip); đoạn không đổi được đánh dấu
    UNCHANGED để agent khỏi mở lại → lần check thứ 2, 3… gần như không tốn token ảnh.
    Trả về (check.md, danh sách id đoạn cần xem)."""
    sc = load(project.f("script.json"))
    src = project.source()
    scenes = _scenes(project)
    src_dur = float(project.state().get("source_duration") or media.duration(src))
    speech = speech_durations(project, sc)
    cdir = project.f("check")
    cdir.mkdir(exist_ok=True)
    state_f = project.f("check_state.json")
    try:
        old_state = json.loads(state_f.read_text(encoding="utf-8")) if state_f.exists() else {}
    except Exception:
        old_state = {}
    new_state: dict[str, str] = {}
    tmp = cdir / "_tmp"
    todo: list[int] = []
    body: list[str] = []
    for s in sc.segments:
        need = speech.get(s.id, (0.0, False))[0] + gap_seconds()
        fitted, k, frozen = fit_clips([(c.start, c.end) for c in s.clips], need, scenes, src_dur)
        times = _check_times(fitted)
        out = cdir / f"seg_{s.id:03d}.jpg"
        key = _key(s.narration, fitted, times)
        new_state[str(s.id)] = key
        changed = old_state.get(str(s.id)) != key or not out.exists()
        if changed:
            todo.append(s.id)
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir()
            n = 0
            for t in times:
                if _frame(src, t, tmp / f"f{n:02d}.png", f"{t:.1f}s"):
                    n += 1
            if out.exists():
                out.unlink()
            if n:
                cols = min(4, n)
                rows = (n + cols - 1) // cols
                subprocess.run([media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-framerate", "1",
                                "-i", str(tmp / "f%02d.png"), "-vf", f"tile={cols}x{rows}:padding=3:color=white",
                                "-frames:v", "1", "-q:v", "5", str(out)], **_NOWIN)
        clips_txt = ", ".join(f"{a:.1f}-{b:.1f}s" for a, b in fitted)
        status = ("NEW/CHANGED - open the image" if changed
                  else "UNCHANGED since your previous check - you already saw it, do NOT open it again")
        body += [f"## Segment {s.id}", f"Status: {status}", f"Narration: {s.narration}",
                 f"Clips shown: {clips_txt}", f"Image: check/{out.name}", ""]
    shutil.rmtree(tmp, ignore_errors=True)
    keep = {f"seg_{s.id:03d}.jpg" for s in sc.segments}
    for f in cdir.glob("seg_*.jpg"):
        if f.name not in keep:
            f.unlink()
    state_f.write_text(json.dumps(new_state, ensure_ascii=False), encoding="utf-8")
    todo_txt = ", ".join(map(str, todo)) if todo else "none"
    lines = ["# Visual check", "",
             f"Segments to look at this time ({len(todo)} of {len(sc.segments)}): {todo_txt}", "",
             "Open ONLY the images of segments marked NEW/CHANGED and verify that what is ON SCREEN matches what the",
             "NARRATION says. Segments marked UNCHANGED have the same narration and the same frames as last time -",
             "keep your previous note for them. Frames are taken across each clip (start, every ~second, end), exactly",
             "as they will be shown after timing adjustment (yellow label = second in the source).", ""] + body
    media.write_text_bom(project.f("check.md"), "\n".join(lines))
    project.log(f"Ảnh đối chiếu: {len(todo)}/{len(sc.segments)} đoạn mới hoặc đã đổi (check.md, check/).")
    return project.f("check.md"), todo
