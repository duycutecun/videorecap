"""Cấu trúc script.json (kịch bản) và hàm kiểm tra."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, ValidationError, field_validator


class Clip(BaseModel):
    start: float = Field(ge=0, description="Giây bắt đầu trong video nguồn")
    end: float = Field(gt=0, description="Giây kết thúc trong video nguồn")


class Segment(BaseModel):
    id: int
    narration: str = Field(min_length=1, description="Lời bình đọc trong đoạn này")
    speak: Optional[str] = Field(default=None, description="(tuỳ chọn) Cách đọc cho giọng máy, vd viết phiên âm từ "
                                                        "tiếng Anh. Phụ đề vẫn dùng narration")
    clips: list[Clip] = Field(min_length=1, description="Các đoạn video nguồn chiếu trong lúc đọc")
    original_audio_volume: float = Field(default=0.0, ge=0.0, le=1.0,
                                         description="Âm lượng tiếng gốc (0 = tắt). Dùng khi muốn nghe thoại gốc nhỏ phía sau")

    @field_validator("narration")
    @classmethod
    def strip_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("narration trống")
        return v


class Script(BaseModel):
    title: str = ""
    language: str = "vi"
    target_minutes: Optional[float] = Field(default=None, gt=0, description="Độ dài AI tự chọn (phút)")
    length_reason: str = ""
    segments: list[Segment] = Field(min_length=1)


SCHEMA_EXAMPLE = {
    "title": "Tóm tắt: <tên>",
    "language": "vi",
    "segments": [
        {"id": 1, "narration": "Mở đầu câu chuyện, ...", "clips": [{"start": 12.5, "end": 17.0}, {"start": 40.0, "end": 43.5}],
         "original_audio_volume": 0.0},
    ],
}


def _read(path: Path) -> str:
    from .media import read_text_any
    return read_text_any(path)


def load(path: Path) -> Script:
    return Script.model_validate(json.loads(_read(path)))


def count_sentences(text: str) -> int:
    parts = [p for p in re.split(r"(?<=[.!?…])(?<!\b[A-ZĐ]\.)\s+(?=[A-ZÀ-Ỹ\"“(])", text.strip()) if p.strip()]
    return max(1, len(parts))


def validate_file(path: Path, source_duration: float, max_clip_seconds: float,
                  target_seconds: Optional[float], words_per_minute: float,
                  auto_bounds: Optional[tuple[float, float]] = None,
                  one_sentence: bool = False, anime: bool = False) -> tuple[list[str], list[str], Optional[Script]]:
    """Trả về (lỗi, cảnh báo, script). Lỗi = phải sửa; cảnh báo = nên xem lại."""
    errors: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return ["Chưa có file script.json"], [], None
    try:
        raw = json.loads(_read(path))
    except json.JSONDecodeError as e:
        return [f"script.json không phải JSON hợp lệ: {e}"], [], None
    try:
        sc = Script.model_validate(raw)
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            errors.append(f"{loc}: {err['msg']}")
        return errors, warnings, None

    ids = [s.id for s in sc.segments]
    if len(set(ids)) != len(ids):
        errors.append("Trùng id giữa các segment")
    words = 0
    for s in sc.segments:
        words += len(s.narration.split())
        if one_sentence and count_sentences(s.narration) > 1:
            errors.append(f"segment {s.id}: có {count_sentences(s.narration)} câu — chế độ khớp chặt yêu cầu mỗi đoạn "
                          f"đúng 1 câu (tách thành nhiều đoạn, mỗi đoạn có clip riêng)")
        for i, c in enumerate(s.clips):
            tag = f"segment {s.id} clip {i + 1}"
            if c.end <= c.start:
                errors.append(f"{tag}: end ({c.end}) phải lớn hơn start ({c.start})")
            if c.end > source_duration + 0.5:
                errors.append(f"{tag}: end ({c.end}s) vượt quá độ dài nguồn ({source_duration:.1f}s)")
            if c.end - c.start < 0.4:
                errors.append(f"{tag}: clip quá ngắn (< 0.4s)")
            if c.end - c.start > max_clip_seconds + 0.01:
                warnings.append(f"{tag}: dài {c.end - c.start:.1f}s, vượt mức khuyến nghị {max_clip_seconds:g}s cho mỗi clip gốc")
    from . import pronounce
    glossary = None
    try:
        glossary = pronounce.load_glossary(path.parent)
    except ValueError as e:
        errors.append(str(e))
    g_err, g_warn = fidelity_checks(sc, glossary, anime)
    errors.extend(g_err)
    warnings.extend(g_warn)
    terms = {t["name"]: t["read"] for t in (glossary or {}).get("terms", []) if t["read"]}
    warnings.extend(style_warnings(sc))
    if anime:
        warnings.extend(anime_style_warnings(sc))
    warnings.extend(pronunciation_warnings(sc, terms))
    if target_seconds is None and auto_bounds:
        lo, hi = auto_bounds
        if sc.target_minutes is None:
            errors.append("Chế độ AI tự quyết độ dài: script.json phải có \"target_minutes\" (số phút đã chọn)")
        else:
            if not (lo - 1e-6 <= sc.target_minutes <= hi + 1e-6):
                errors.append(f"target_minutes = {sc.target_minutes:g} nằm ngoài khoảng cho phép {lo:g}–{hi:.1f} phút")
            target_seconds = sc.target_minutes * 60
    if target_seconds and words_per_minute:
        est = words / words_per_minute * 60
        if est < target_seconds * 0.7:
            warnings.append(f"Lời bình ước tính {est/60:.1f} phút — ngắn hơn nhiều so với mục tiêu {target_seconds/60:.1f} phút")
        elif est > target_seconds * 1.3:
            warnings.append(f"Lời bình ước tính {est/60:.1f} phút — dài hơn nhiều so với mục tiêu {target_seconds/60:.1f} phút")
    return errors, warnings, (sc if not errors else None)


CAMERA_TALK = ("nhìn từ bên trong", "nhìn từ trong", "nhìn từ ngoài", "từ góc này", "góc máy", "khung hình",
               "cảnh quay", "máy quay", "camera", "ống kính", "từ cuối đường hầm nhìn ra", "từ sát mặt đất nhìn ra")


def _opener(text: str) -> str:
    w = re.findall(r"[^\W\d_]+", text.lower())
    return w[0] if w else ""


def style_warnings(sc: "Script") -> list[str]:
    """Cảnh báo giọng văn khô: mở câu lặp lại, nói về góc máy."""
    out: list[str] = []
    segs = sc.segments
    if len(segs) >= 8:
        openers = [_opener(s.narration) for s in segs]
        from collections import Counter
        word, n = Counter(o for o in openers if o).most_common(1)[0] if any(openers) else ("", 0)
        if n > max(4, len(segs) * 0.15):
            ids = [s.id for s, o in zip(segs, openers) if o == word]
            out.append(f"Giọng văn: {n}/{len(segs)} câu mở đầu bằng \"{word}\" — đọc như liệt kê. Đổi cách mở câu "
                       f"(bỏ chủ ngữ, mở bằng hành động/thời điểm/cảm xúc, dùng từ nối). Các đoạn: "
                       + ", ".join(map(str, ids[:30])) + ("…" if len(ids) > 30 else ""))
        run, start = 1, 0
        for i in range(1, len(openers) + 1):
            if i < len(openers) and openers[i] and openers[i] == openers[i - 1]:
                run += 1
                continue
            if run >= 3:
                out.append(f"Giọng văn: {run} câu liền nhau (đoạn {segs[start].id}–{segs[i - 1].id}) cùng mở bằng "
                           f"\"{openers[i - 1]}\" — viết lại cho liền mạch, tự nhiên hơn")
            run, start = 1, i
    cam = [s.id for s in segs if any(p in s.narration.lower() for p in CAMERA_TALK)]
    if cam:
        out.append("Giọng văn: đoạn " + ", ".join(map(str, cam)) + " nói về góc máy/khung hình — kể chuyện, "
                   "đừng tả camera")
    return out


def _has_word(text: str, word: str) -> bool:
    return re.search(r"(?<![\wÀ-ỹĐđ])" + re.escape(word) + r"(?![\wÀ-ỹĐđ])", text, re.IGNORECASE) is not None


def fidelity_checks(sc: "Script", glossary: Optional[dict], anime: bool) -> tuple[list[str], list[str]]:
    """Chuẩn nguyên tác: tên nhân vật / chiêu thức / địa danh phải viết đúng theo glossary.json của job.

    - SRT hiển thị (narration) dùng đúng chữ `name`; SRT voice (giọng đọc) dùng `read`.
    - `avoid` = các cách viết sai / tự chế → có trong lời bình là LỖI.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if glossary is None:
        if anime:
            errors.append('Thiếu glossary.json — nội dung anime bắt buộc có bảng tên riêng theo nguyên tác '
                          '(xem "Anime: source fidelity" trong TASK.md)')
        return errors, warnings
    terms = glossary["terms"]
    if anime and not terms:
        errors.append("glossary.json chưa có mục nào — liệt kê tên nhân vật, tổ chức, địa danh, chiêu thức theo nguyên tác")
    if anime and not glossary.get("source"):
        warnings.append('Nguyên tác: glossary.json chưa ghi "source" (tên tác phẩm gốc + tập/arc)')
    from . import pronounce
    vi = (sc.language or "vi") == "vi"
    for t in terms:
        if vi and not t["read"] and pronounce.foreign_words(t["name"]):
            errors.append(f'glossary.json: "{t["name"]}" chưa có "read" (cách đọc kiểu Việt cho giọng máy, vd "Luffy" → "lu phi")')
    for s in sc.segments:
        for t in terms:
            for a in t["avoid"]:
                if a.lower() != t["name"].lower() and _has_word(s.narration, a):
                    errors.append(f'segment {s.id}: viết "{a}" — sai so với nguyên tác, phải là "{t["name"]}"')
            # lời bình là chữ lên hình: không được viết phiên âm ở đây
            if t["read"] and t["read"].lower() != t["name"].lower() and _has_word(s.narration, t["read"]) \
                    and len(t["read"].split()) > 1:
                errors.append(f'segment {s.id}: lời bình (chữ hiển thị) đang viết phiên âm "{t["read"]}" — '
                              f'phải viết "{t["name"]}"; phiên âm chỉ dùng cho giọng đọc')
        sp = (s.speak or "").strip()
        ratio = len(sp.split()) / max(1, len(s.narration.split()))
        if sp and (ratio < 0.5 or ratio > 2.5):  # phiên âm làm tăng số âm tiết, nên chỉ báo khi lệch hẳn
            warnings.append(f"Nguyên tác: segment {s.id}: \"speak\" khác xa lời bình — speak phải là CÙNG câu đó, "
                            f"chỉ đổi cách viết cho giọng đọc")
    return errors, warnings


ANIME_STIFF = ("tiến hành", "thực hiện", "đối tượng", "nhân vật chính của chúng ta", "bộ phim này",
               "trong video này", "trong tập phim này", "như các bạn đã thấy")
ANIME_PRONOUN = ("anh ấy", "cô ấy", "ông ấy", "bà ấy", "anh ta", "cô ta")


def anime_style_warnings(sc: "Script") -> list[str]:
    """Văn phong recap anime: không viết kiểu báo cáo, không lạm dụng 'anh ấy/cô ấy'."""
    out: list[str] = []
    segs = sc.segments
    stiff = [s.id for s in segs if any(p in s.narration.lower() for p in ANIME_STIFF)]
    if stiff:
        out.append("Giọng văn: đoạn " + ", ".join(map(str, stiff[:30])) + " dùng từ kiểu báo cáo/giới thiệu video "
                   "(tiến hành, thực hiện, đối tượng, \"trong video này\"...) — kể như người trong cuộc, vào thẳng chuyện")
    pro = [s.id for s in segs if any(_has_word(s.narration, p) for p in ANIME_PRONOUN)]
    if len(segs) >= 8 and len(pro) > max(3, len(segs) * 0.12):
        out.append(f"Giọng văn: {len(pro)}/{len(segs)} đoạn gọi nhân vật bằng \"anh ấy/cô ấy/anh ta\" — văn recap anime "
                   "gọi bằng TÊN hoặc cậu/cô/hắn/gã/lão/nhóc... đúng vai và tuổi nhân vật. Các đoạn: "
                   + ", ".join(map(str, pro[:30])))
    return out


def pronunciation_warnings(sc: "Script", terms: Optional[dict] = None) -> list[str]:
    """Lời Việt có từ nước ngoài / viết tắt mà chưa có cách đọc → giọng Việt dễ đọc sai."""
    if (sc.language or "vi") != "vi":
        return []
    from . import pronounce
    bad = []
    for s in sc.segments:
        if (s.speak or "").strip():
            continue
        fw = pronounce.foreign_words(s.narration, terms)
        if fw:
            bad.append(f"{s.id} ({', '.join(fw[:4])})")
    if not bad:
        return []
    return ["Phát âm: lời Việt có từ nước ngoài/viết tắt, giọng đọc dễ đọc sai — thay bằng từ Việt, hoặc thêm trường "
            "\"speak\" ghi cách đọc kiểu Việt (vd \"Ferrari\" → \"phe ra ri\"); tên riêng của tác phẩm thì thêm vào "
            "glossary.json kèm \"read\". Các đoạn: " + "; ".join(bad[:25])
            + ("…" if len(bad) > 25 else "")]
