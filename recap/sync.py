"""Đo độ khớp giữa lời đọc (thời lượng giọng thật) và clip của từng đoạn, và căn chỉnh clip khi dựng.

- report(): so sánh thời lượng giọng đọc thật (tts/) với tổng độ dài clip của mỗi đoạn.
- fit_clips(): dùng khi dựng — nới clip ra trong phạm vi CẢNH chứa nó, cắt bớt theo tỷ lệ,
  và chỉnh tốc độ hình rất nhẹ, để hình luôn chạy đúng bằng lời đọc mà không đứng khung hay chiếu sang cảnh khác.
"""
from __future__ import annotations

import json
from typing import Optional

from . import media, pronounce, settings
from .project import Project

def gap_seconds() -> float:
    """Khoảng nghỉ giữa hai câu lời bình (giây). Giọng đã được cắt bớt khoảng lặng đầu/cuối."""
    try:
        return max(0.0, min(1.5, float(settings.get("render.gap_seconds", 0.2))))
    except (TypeError, ValueError):
        return 0.2


def _speed_range() -> float:
    """Biên độ đổi tốc độ hình tối đa (0.06 = ±6%). Đổi tốc độ phải chèn/bỏ khung → dễ thấy khựng."""
    try:
        return max(0.0, min(0.2, float(settings.get("render.max_speed_change", 0.06))))
    except (TypeError, ValueError):
        return 0.06


TAIL = 0.2  # giữ tên cũ cho tương thích; dùng gap_seconds()


def tolerance() -> float:
    return float(settings.get("script.sync_tolerance", 0.15) or 0.15)


def _scenes(project: Project) -> list[dict]:
    p = project.f("scenes.json")
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def speech_durations(project: Project, script, real_only: bool = False) -> dict[int, tuple[float, bool]]:
    """{id: (giây, là_số_đo_thật)}. Dùng file giọng đã tạo nếu lời chưa đổi, không thì ước tính theo số chữ."""
    from .tts import TTS
    opts = project.options
    try:
        tts = TTS(opts.get("tts_provider") or None, opts.get("tts_voice") or None,
                  opts.get("tts_rate") if opts.get("tts_rate") not in (None, "") else None)
    except Exception:  # noqa: BLE001
        tts = None
    known = {}
    mp = project.f("tts", "manifest.json")
    if mp.exists():
        try:
            known = {m["file"]: float(m["duration"]) for m in json.loads(mp.read_text(encoding="utf-8"))}
        except Exception:  # noqa: BLE001
            known = {}
    lang = script.language or "vi"
    wpm = (settings.get("script.words_per_minute", {}) or {}).get(lang, 160)
    out: dict[int, tuple[float, bool]] = {}
    for s in script.segments:
        if tts:
            f = project.f("tts", f"seg_{s.id:03d}_{tts.cache_key(pronounce.spoken_text(s, lang))}.wav")
            rel = f"tts/{f.name}"
            if rel in known:
                out[s.id] = (known[rel], True)
                continue
            if f.exists():
                out[s.id] = (media.duration(f), True)
                continue
        if not real_only:
            out[s.id] = (len(pronounce.spoken_text(s, lang).split()) / wpm * 60, False)
    return out


def report(project: Project) -> list[dict]:
    from .script_schema import load
    sc = load(project.f("script.json"))
    sp = speech_durations(project, sc)
    scenes = _scenes(project)
    src_dur = float(project.state().get("source_duration") or 0)
    tol = tolerance()
    from .tts import TTS
    opts = project.options
    try:
        tts = TTS(opts.get("tts_provider") or None, opts.get("tts_voice") or None,
                  opts.get("tts_rate") if opts.get("tts_rate") not in (None, "") else None)
    except Exception:  # noqa: BLE001
        tts = None
    rows = []
    for s in sc.segments:
        speech, real = sp.get(s.id, (0.0, False))
        need = speech + gap_seconds()
        clip = sum(c.end - c.start for c in s.clips)
        fitted, k, frozen = fit_clips([(c.start, c.end) for c in s.clips], need, scenes, src_dur)
        diff = clip - need
        if abs(diff) <= max(0.6, need * tol):
            status = "ok"
        elif diff > 0:
            status = "long"   # clip dài hơn lời → khi dựng sẽ bị cắt đuôi, có thể mất hành động → bắt sửa
        elif frozen <= 0.05:
            status = "auto"   # clip hơi ngắn nhưng tool nới được trong cùng cảnh, không đứng hình
        else:
            status = "short"
        audio = None
        if tts:
            f = project.f("tts", f"seg_{s.id:03d}_{tts.cache_key(pronounce.spoken_text(s, sc.language))}.wav")
            if f.exists():
                audio = f"tts/{f.name}"
        rows.append({"id": s.id, "speech": round(speech, 2), "measured": real, "clip": round(clip, 2),
                     "diff": round(diff, 2), "status": status, "frozen": round(frozen, 2), "speed": round(k, 3),
                     "fitted": [[round(a, 2), round(b, 2)] for a, b in fitted], "audio": audio,
                     "need": round(need, 2)})
    return rows


def _scene_of(t: float, scenes: list[dict]) -> Optional[dict]:
    for sc in scenes:
        if sc["start"] - 0.05 <= t < sc["end"]:
            return sc
    return None


def _cuts(scenes: list[dict]) -> list[float]:
    return sorted({float(sc["start"]) for sc in scenes if float(sc.get("start", 0)) > 0.05})


def snap_to_cuts(a: float, b: float, cuts: list[float]) -> tuple[float, float]:
    """Clip chọn theo bảng ảnh (mỗi ~3 giây một khung) hay lẹm vài trăm ms ĐUÔI cảnh trước ở đầu clip,
    hoặc lố sang ĐẦU cảnh sau ở cuối → nhìn như hình chớp/khựng, và hành động thật đến muộn hơn lời đọc.
    Có điểm chuyển cảnh sát đầu/cuối clip thì kéo mép clip về đúng điểm đó."""
    head, tail, keep = _snap_cfg()
    inside = [x for x in cuts if a + 0.05 < x < b - 0.05]
    if inside and inside[0] - a <= head and b - inside[0] >= keep:
        a = inside[0]
    inside = [x for x in cuts if a + 0.05 < x < b - 0.05]
    if inside and b - inside[-1] <= tail and inside[-1] - a >= keep:
        b = inside[-1]
    return a, b


def _snap_cfg() -> tuple[float, float, float]:
    try:
        head = float(settings.get("render.snap_head_seconds", 1.5))
        tail = float(settings.get("render.snap_tail_seconds", 1.2))
    except (TypeError, ValueError):
        head, tail = 1.5, 1.2
    return max(0.0, head), max(0.0, tail), 1.0


def _sliver_cfg() -> tuple[float, float]:
    """(guard, min_shot): guard = lùi khỏi điểm cắt cảnh bao nhiêu giây (bù sai số dò cảnh ~1 khung);
    min_shot = mẩu cảnh khác ngắn hơn mức này ở đầu/cuối clip bị bỏ (mẩu 1–10 khung nhìn như hình giật/khựng)."""
    try:
        guard = float(settings.get("render.cut_guard_seconds", 0.04))
        min_shot = float(settings.get("render.min_shot_seconds", 0.35))
    except (TypeError, ValueError):
        guard, min_shot = 0.04, 0.35
    return max(0.0, min(0.2, guard)), max(0.0, min(1.5, min_shot))


def _drop_slivers(clips: list[list[float]], cuts: list[float], guard: float, min_shot: float) -> None:
    """Cắt bỏ mẩu cảnh khác lẹm vào đầu/cuối clip (kể cả điểm cắt nằm sát mép trong vòng guard)."""
    for c in clips:
        for _ in range(3):  # đầu clip: có thể có vài điểm cắt sát nhau
            near = [x for x in cuts if c[0] - guard < x < c[1] and x - c[0] < min_shot]
            if not near or c[1] - (near[-1] + guard) < 0.5:
                break
            c[0] = max(c[0], near[-1] + guard)
        for _ in range(3):  # cuối clip
            near = [x for x in cuts if c[0] < x < c[1] + guard and c[1] - x < min_shot]
            if not near or (near[0] - guard) - c[0] < 0.5:
                break
            c[1] = min(c[1], near[0] - guard)


def _extend_in_scene(clips: list[list[float]], missing: float, scenes: list[dict], guard: float) -> None:
    """Nới clip trong chính cảnh của nó, chừa guard ở hai mép cảnh để không lẹm khung cảnh bên cạnh."""
    for _ in range(4):
        if missing <= 0.01:
            return
        share = missing / len(clips)
        gained = 0.0
        for c in clips:
            sc = _scene_of((c[0] + c[1]) / 2, scenes)
            if not sc:
                continue
            lo, hi = float(sc["start"]) + guard, float(sc["end"]) - guard
            add = min(share, max(0.0, hi - c[1]))
            c[1] += add
            add2 = min(share - add, max(0.0, c[0] - lo))
            c[0] -= add2
            gained += add + add2
        missing -= gained
        if gained < 0.01:
            return


def fit_clips(clips: list[tuple[float, float]], need: float, scenes: list[dict],
              src_dur: float) -> tuple[list[tuple[float, float]], float, float]:
    """Như _fit_raw, rồi dọn các mẩu cảnh khác chỉ vài khung ở mép clip (nguyên nhân chính gây cảm giác khựng)."""
    fitted, k, frozen = _fit_raw(clips, need, scenes, src_dur)
    cuts = _cuts(scenes)
    if not cuts:
        return fitted, k, frozen
    guard, min_shot = _sliver_cfg()
    cl = [list(c) for c in fitted]
    _drop_slivers(cl, cuts, guard, min_shot)
    if all(abs(a - x) < 1e-6 and abs(b - y) < 1e-6 for (a, b), (x, y) in zip(cl, fitted)):
        return fitted, k, frozen
    total = sum(b - a for a, b in cl)
    if total < need:
        _extend_in_scene(cl, need - total, scenes, guard)
        total = sum(b - a for a, b in cl)
    if total <= 0:
        return fitted, k, frozen
    if total > need + 0.01:
        k = need / total
    else:
        k = min(1 + _speed_range(), need / total) if total < need else 1.0
    frozen = max(0.0, need - total * k)
    return [tuple(c) for c in cl], k, frozen


def _fit_raw(clips: list[tuple[float, float]], need: float, scenes: list[dict],
             src_dur: float) -> tuple[list[tuple[float, float]], float, float]:
    """Trả về (clip đã chỉnh, hệ số tốc độ hình k, số giây còn phải giữ khung cuối).
    k > 1: hình chậm lại (kéo dài); k < 1: hình nhanh lên. Thời lượng hình sau chỉnh = tổng clip * k.

    Thứ tự ưu tiên (đổi tốc độ là thứ dễ thấy khựng nhất nên để gần cuối):
      1. Bám mép clip vào điểm chuyển cảnh (bỏ mẩu cảnh khác chớp qua ở đầu/cuối).
      2. Thừa hình → cắt bớt đuôi các clip cho vừa, GIỮ NGUYÊN tốc độ.
      3. Thiếu hình → nới clip trong chính cảnh của nó (sau trước, rồi trước).
      4. Còn thiếu → làm chậm nhẹ (tối đa render.max_speed_change).
      5. Còn thiếu → nới sang cảnh kế bên (nếu bật script.extend_across_scenes), cuối cùng mới giữ khung."""
    cuts = _cuts(scenes)
    clips = [list(snap_to_cuts(a, b, cuts)) if cuts else [a, b] for a, b in clips]
    total = sum(b - a for a, b in clips)
    if total <= 0:
        return [tuple(c) for c in clips], 1.0, need

    if total > need:
        keep = need / total
        clips = [[a, a + max(0.5, (b - a) * keep)] for a, b in clips]
        total = sum(b - a for a, b in clips)
        if total > need + 0.01:   # clip nào cũng đã ngắn tới mức tối thiểu → đành tua nhanh nhẹ
            return [tuple(c) for c in clips], need / total, 0.0
        return [tuple(c) for c in clips], 1.0, 0.0

    def extend(missing: float, bounds) -> float:
        for _ in range(4):
            if missing <= 0.01:
                break
            share = missing / len(clips)
            gained = 0.0
            for c in clips:
                lo, hi = bounds(c)
                add = min(share, max(0.0, hi - c[1]))
                c[1] += add
                gained += add
                rest = share - add
                if rest > 0:
                    add2 = min(rest, max(0.0, c[0] - lo))
                    c[0] -= add2
                    gained += add2
            missing -= gained
            if gained < 0.01:
                break
        return missing

    end_all = src_dur or max(b for _, b in clips)

    def own_scene(c):
        sc = _scene_of(c[0] + 0.01, scenes) if scenes else None
        if not sc:
            return 0.0, end_all
        return max(0.0, sc["start"]), min(end_all, sc["end"])

    missing = extend(need - total, own_scene)
    total = sum(b - a for a, b in clips)
    k = min(1 + _speed_range(), need / total) if total < need else 1.0
    if total * k < need - 0.01 and settings.get("script.extend_across_scenes", True) and src_dur:
        # còn thiếu dù đã chậm hết mức → nới sang cảnh bên cạnh, rồi tính lại tốc độ
        extend(need / k - total, lambda c: (0.0, src_dur))
        total = sum(b - a for a, b in clips)
        k = min(1 + _speed_range(), need / total) if total < need else 1.0
    frozen = max(0.0, need - total * k)
    return [tuple(c) for c in clips], k, frozen
