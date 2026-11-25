"""Bước 3: cắt cảnh (ffmpeg scene detection), trích khung hình, gộp thành context.md cho agent đọc."""
from __future__ import annotations

import json
import re
import subprocess

from . import media, settings
from .paths import IS_WINDOWS
from .project import ProcessRunner, Project

_NOWIN = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}
_PTS = re.compile(r"pts_time:([0-9.]+)")


def detect(project: Project, runner: ProcessRunner | None = None) -> list[dict]:
    if project.f("scenes.json").exists():
        project.log("Đã có scenes.json — bỏ qua bước cắt cảnh.")
        return json.loads(project.f("scenes.json").read_text(encoding="utf-8"))
    src = project.source()
    cfg = settings.get("scenes", {})
    thr = float(cfg.get("threshold", 0.3))
    min_len = float(cfg.get("min_scene_seconds", 1.0))
    max_len = float(cfg.get("max_scene_seconds", 0) or 0)  # 0 = không chia nhỏ cảnh dài
    total = float(project.state().get("source_duration") or media.duration(src))
    project.log(f"Cắt cảnh (ngưỡng {thr})…")

    cuts: list[float] = []

    def on_line(line: str) -> None:
        if "showinfo" in line:
            m = _PTS.search(line)
            if m:
                cuts.append(float(m.group(1)))

    runner = runner or ProcessRunner(project)
    from collections import deque
    # GPU giải mã trước; GPU lỗi (vd bộ nhớ GPU còn bị bước nhận dạng giọng giữ) thì chuyển CPU
    for hw, name in ((["-hwaccel", "auto"], "GPU"), ([], "CPU")):
        cuts.clear()
        tail: deque[str] = deque(maxlen=6)
        cmd = [media.ffmpeg(), "-hide_banner", "-nostats", "-nostdin", *hw, "-i", str(src), "-an", "-sn",
               "-vf", f"scale=320:-2,select='gt(scene,{thr})',showinfo", "-f", "null", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace", **_NOWIN)
        runner.proc = proc
        assert proc.stderr is not None
        for line in proc.stderr:
            if "showinfo" in line:
                on_line(line)
            elif line.strip():
                tail.append(line.strip())
        proc.wait()
        runner.proc = None
        if runner.cancelled:
            from .project import Cancelled
            raise Cancelled()
        if proc.returncode == 0:
            break
        project.log(f"  ffmpeg ({name}) lỗi khi cắt cảnh (mã {proc.returncode}): {' | '.join(tail) or 'không có thông báo'}")
    else:
        raise RuntimeError("ffmpeg lỗi khi cắt cảnh (đã thử cả GPU và CPU) — xem các dòng ngay phía trên để biết lý do.")

    # Ghép danh sách điểm cắt thành cảnh, gộp cảnh quá ngắn, chia nhỏ cảnh quá dài
    bounds = [0.0] + sorted(c for c in cuts if 0 < c < total) + [total]
    raw = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    merged: list[list[float]] = []
    for a, b in raw:
        if merged and (b - a) < min_len:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    if len(merged) > 1 and (merged[0][1] - merged[0][0]) < min_len:
        merged[1][0] = merged[0][0]
        merged.pop(0)
    scenes: list[dict] = []
    for a, b in merged:
        d = b - a
        parts = (max(1, int(d // max_len) + (1 if d % max_len > 0.01 else 0)) if max_len > 0 and d > max_len else 1)
        step = d / parts
        for k in range(parts):
            s, e = a + k * step, a + (k + 1) * step
            scenes.append({"id": len(scenes) + 1, "start": round(s, 2), "end": round(e, 2)})
    project.f("scenes.json").write_text(json.dumps(scenes, indent=1), encoding="utf-8")
    project.log(f"Xong cắt cảnh: {len(scenes)} cảnh.")
    return scenes


def extract_frames(project: Project, scenes: list[dict], runner: ProcessRunner | None = None) -> list[dict]:
    src = project.source()
    cfg = settings.get("frames", {})
    cap = int(cfg.get("max_frames", 900))
    width = int(cfg.get("width", 512))
    fdir = project.f("frames")
    fdir.mkdir(exist_ok=True)

    def n_for(d: float) -> int:
        # khoảng 2 giây một khung, tối đa 8 khung mỗi cảnh
        return max(1, min(8, int(d // 2)))

    plan = {s["id"]: n_for(s["end"] - s["start"]) for s in scenes}
    if sum(plan.values()) > cap:
        plan = {k: 1 for k in plan}
    if len(plan) > cap:
        keep = {scenes[int(i * len(scenes) / cap)]["id"] for i in range(cap)}
        plan = {k: (1 if k in keep else 0) for k in plan}

    total = sum(plan.values())
    project.log(f"Trích {total} khung hình (tối đa {cap})…")
    done = 0
    for s in scenes:
        n = plan.get(s["id"], 0)
        s["frames"] = []
        d = s["end"] - s["start"]
        for k in range(n):
            t = s["start"] + d * (k + 1) / (n + 1)
            name = f"s{s['id']:04d}_{k + 1}.jpg"
            out = fdir / name
            if not out.exists():
                subprocess.run([media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}",
                                "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(out)],
                               **_NOWIN)
            if out.exists():
                s["frames"].append({"file": f"frames/{name}", "time": round(t, 2)})
            done += 1
            if runner and runner.cancelled:
                from .project import Cancelled
                raise Cancelled()
        if total and done and done % 50 == 0:
            project.log(f"  khung hình: {done}/{total}")
    project.f("scenes.json").write_text(json.dumps(scenes, indent=1), encoding="utf-8")
    return scenes


def build_context(project: Project, scenes: list[dict]) -> str:
    """Gộp cảnh + lời thoại + ảnh thành một file dễ đọc cho agent."""
    tr = {"segments": []}
    if project.f("transcript.json").exists():
        tr = json.loads(project.f("transcript.json").read_text(encoding="utf-8"))
    segs = tr.get("segments", [])
    sidx = {}
    if project.f("sheets", "index.json").exists():
        sidx = json.loads(project.f("sheets", "index.json").read_text(encoding="utf-8"))
    lines = [
        "# Source context",
        f"Source duration: {media.fmt_ts(float(project.state().get('source_duration') or 0))}",
        f"Detected dialogue language: {tr.get('language') or 'unknown'}",
        f"Scenes: {len(scenes)}",
        "",
        "Each scene lists its time range (seconds), the images that show it, and the dialogue spoken during it.",
        "",
    ]
    from .sheets import load_overview
    oidx = load_overview(project) if sidx else {}
    if sidx and oidx:
        lines += [
            f"OVERVIEW SHEETS (look at ALL of them, in order): {len(oidx['sheets'])} images in `overview/`. Each is a "
            f"{oidx['cols']}x{oidx['rows']} grid read left-to-right, top-to-bottom; one cell every {oidx['interval']:g} s, "
            f"the second is printed in yellow. Use them to understand the whole video and to find roughly WHERE each "
            f"moment happens.",
            "",
            f"DETAIL SHEETS (open ONLY when needed): {len(sidx['sheets'])} images in `sheets/`, "
            f"{sidx['cols']}x{sidx['rows']} grid, one cell every {sidx['interval']:g} s. Do NOT open them all. Open a "
            f"detail sheet only to find the exact second an action starts/ends for a clip you are choosing, when the "
            f"overview is not precise enough. Each scene below lists which detail sheet covers it.",
            "",
        ]
    elif sidx:
        lines += [
            f"PICTURE SHEETS: the whole video is covered by {len(sidx['sheets'])} images in `sheets/`. Each image is a "
            f"{sidx['cols']}x{sidx['rows']} grid read left-to-right, top-to-bottom; every cell is the frame at the second "
            f"printed in yellow (one cell every {sidx['interval']:g} s). Use them to see WHAT happens at WHICH second; "
            f"cells are close together, so follow an action cell by cell to find the exact second it starts and ends.",
            "",
        ]
    j = 0
    for s in scenes:
        lines.append(f"## Scene {s['id']}  [{s['start']:.2f}s → {s['end']:.2f}s]  ({media.fmt_ts(s['start'])} → {media.fmt_ts(s['end'])})")
        fr = s.get("frames") or []
        if sidx:
            from .sheets import sheets_for_range
            if oidx:
                lines.append("Overview: " + "; ".join(sheets_for_range(oidx, s["start"], s["end"])))
                lines.append("Detail: " + "; ".join(sheets_for_range(sidx, s["start"], s["end"])))
            else:
                lines.append("Sheets: " + "; ".join(sheets_for_range(sidx, s["start"], s["end"])))
        elif fr:
            lines.append("Frames: " + ", ".join(f["file"] for f in fr))
        said = []
        while j < len(segs) and segs[j]["start"] < s["end"]:
            if segs[j]["end"] > s["start"]:
                said.append(f"- ({segs[j]['start']:.1f}s) {segs[j]['text']}")
            j += 1
        # một câu có thể kéo sang cảnh sau
        if j > 0 and segs and j <= len(segs) and segs[j - 1]["end"] > s["end"]:
            j -= 1
        lines.extend(said or ["- (no dialogue)"])
        lines.append("")
    text = "\n".join(lines)
    media.write_text_bom(project.f("context.md"), text)
    project.log("Đã tạo context.md cho agent.")
    return text
