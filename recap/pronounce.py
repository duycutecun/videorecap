"""Phát âm cho giọng đọc tiếng Việt: thay từ nước ngoài / viết tắt bằng cách đọc kiểu Việt trước khi gửi sang TTS.

- Phụ đề và kịch bản vẫn giữ nguyên chữ gốc (YouTube, AI…); chỉ chữ gửi cho giọng đọc bị thay.
- Từ điển = mặc định bên dưới + `tts.pronunciation` trong Cài đặt (khách tự thêm/sửa, ưu tiên hơn mặc định).
- Mỗi đoạn có thể có trường `speak` (agent hoặc người duyệt viết sẵn cách đọc) → dùng thay cho lời bình.
"""
from __future__ import annotations

import re
import unicodedata

import json
from pathlib import Path
from typing import Optional

from . import settings

# Cách đọc phổ biến trong tiếng Việt. Khoá không phân biệt hoa thường.
DEFAULT_VI: dict[str, str] = {
    "AI": "ây ai",
    "OK": "ô kê",
    "okay": "ô kê",
    "YouTube": "diu túp",
    "TikTok": "tích tóc",
    "Facebook": "phây búc",
    "Google": "gu gồ",
    "iPhone": "ai phôn",
    "Apple": "áp pồ",
    "Samsung": "sam sung",
    "laptop": "láp tốp",
    "internet": "in tơ nét",
    "online": "on lai",
    "offline": "óp lai",
    "wifi": "wai phai",
    "camera": "ca mê ra",
    "video": "vi đê ô",
    "game": "ghêm",
    "gamer": "ghêm mơ",
    "anime": "a ni mê",
    "manga": "man ga",
    "selfie": "xeo phi",
    "show": "sô",
    "livestream": "lai xờ trim",
    "stream": "xờ trim",
    "team": "tim",
    "fan": "phan",
    "drone": "đờ rôn",
    "inox": "i nốc",
    "pizza": "pi da",
    "hamburger": "ham bơ gơ",
    "boss": "bót",
    "level": "le vồ",
    "vlog": "vi lốc",
    "vlogger": "vi lốc gơ",
    "blogger": "blốc gơ",
    "app": "áp",
    "website": "web sai",
    "email": "i meo",
    "robot": "rô bốt",
    "CEO": "xi i ô",
    "USA": "u ét a",
    "TV": "ti vi",
    "km": "ki lô mét",
    "kg": "ki lô gam",
    "cm": "xen ti mét",
    "mm": "mi li mét",
}

_WORD = re.compile(r"[A-Za-zÀ-ỹĐđ0-9][A-Za-zÀ-ỹĐđ0-9'’\-]*")


def _entries(extra: Optional[dict] = None) -> dict[str, str]:
    """{từ gốc (giữ hoa/thường): cách đọc}; cài đặt của khách ghi đè mặc định; bảng tên riêng của job ghi đè cả hai."""
    d = dict(DEFAULT_VI)
    user = settings.get("tts.pronunciation", {}) or {}
    for src in (user, extra or {}):
        if isinstance(src, dict):
            for k, v in src.items():
                k = str(k).strip()
                if k and str(v).strip():
                    for old in [x for x in d if x.lower() == k.lower()]:
                        d.pop(old)
                    d[k] = str(v).strip()
    return d


# --- bảng tên riêng / thuật ngữ theo nguyên tác của từng job (glossary.json) ----------
GLOSSARY_FILE = "glossary.json"


def load_glossary(job_dir: Path) -> Optional[dict]:
    """Đọc glossary.json trong thư mục job. None = chưa có; ném ValueError nếu file hỏng."""
    f = Path(job_dir) / GLOSSARY_FILE
    if not f.exists():
        return None
    from .media import read_text_any
    try:
        data = json.loads(read_text_any(f))
    except json.JSONDecodeError as e:
        raise ValueError(f"glossary.json không phải JSON hợp lệ: {e}") from e
    if isinstance(data, list):
        data = {"terms": data}
    if not isinstance(data, dict) or not isinstance(data.get("terms"), list):
        raise ValueError('glossary.json phải có dạng {"source": "...", "terms": [ {"name": ..., "read": ...} ]}')
    terms = []
    for t in data["terms"]:
        if not isinstance(t, dict) or not str(t.get("name", "")).strip():
            continue
        avoid = t.get("avoid") or []
        if isinstance(avoid, str):
            avoid = [avoid]
        terms.append({"name": str(t["name"]).strip(), "read": str(t.get("read") or "").strip(),
                      "type": str(t.get("type") or "").strip(), "note": str(t.get("note") or "").strip(),
                      "avoid": [str(a).strip() for a in avoid if str(a).strip()]})
    return {"source": str(data.get("source") or "").strip(), "terms": terms}


def job_terms(job_dir: Path) -> dict[str, str]:
    """{tên viết đúng nguyên tác: cách đọc} của job — dùng cho giọng đọc (SRT voice)."""
    try:
        g = load_glossary(job_dir)
    except ValueError:
        return {}
    return {t["name"]: t["read"] for t in (g or {}).get("terms", []) if t["read"]}


def dictionary() -> dict[str, str]:
    return {k.lower(): v for k, v in _entries().items()}


def _is_acronym(k: str) -> bool:
    # Viết tắt toàn chữ hoa (AI, TV…) chỉ thay khi viết hoa đúng như vậy — tránh thay nhầm "ai" (ai đó) thành "ây ai"
    return len(k) >= 2 and k.isupper()


def apply(text: str, lang: str = "vi", extra: Optional[dict] = None) -> str:
    """Thay các từ có trong từ điển (nguyên từ). Viết tắt hoa thì khớp đúng hoa; từ thường thì không phân biệt."""
    if (lang or "vi") != "vi" or not settings.get("tts.use_pronunciation", True):
        return text
    e = _entries(extra)
    if not e:
        return text
    b = r"(?<![\wÀ-ỹĐđ])({})(?![\wÀ-ỹĐđ])"
    exact = {k: v for k, v in e.items() if _is_acronym(k)}
    loose = {k.lower(): v for k, v in e.items() if not _is_acronym(k)}
    if exact:
        pat = re.compile(b.format("|".join(re.escape(k) for k in sorted(exact, key=len, reverse=True))))
        text = pat.sub(lambda m: exact[m.group(1)], text)
    if loose:
        pat = re.compile(b.format("|".join(re.escape(k) for k in sorted(loose, key=len, reverse=True))), re.IGNORECASE)
        text = pat.sub(lambda m: loose.get(m.group(1).lower(), m.group(1)), text)
    return text


def spoken_text(seg, lang: str = "vi", extra: Optional[dict] = None) -> str:
    """Chữ thật sự gửi cho giọng đọc (= nội dung SRT voice). `extra` = bảng tên riêng của job."""
    base = (getattr(seg, "speak", None) or "").strip() or seg.narration
    return apply(base, lang, extra)


# --- nhận ra từ không phải tiếng Việt -------------------------------------------------
_ONSET = "(?:ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|b|c|d|đ|g|h|k|l|m|n|p|r|s|t|v|x)?"
_VOWEL = "[aeiouy]{1,3}"
_CODA = "(?:ng|nh|ch|c|m|n|p|t|i|y|o|u)?"
_VI_SYL = re.compile(f"^{_ONSET}{_VOWEL}{_CODA}$")


def _strip_marks(w: str) -> str:
    w = w.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", w) if unicodedata.category(c) != "Mn")


def foreign_words(text: str, extra: Optional[dict] = None) -> list[str]:
    """Từ có vẻ là tiếng nước ngoài / viết tắt (giọng Việt dễ đọc sai). Từ đã có trong từ điển thì bỏ qua."""
    e = _entries(extra)
    # tên nhiều chữ trong bảng tên riêng ("Monkey D. Luffy") đã có cách đọc → bỏ ra trước khi xét từng từ
    for k in sorted((k for k in e if " " in k or "-" in k or "." in k), key=len, reverse=True):
        text = re.sub(r"(?<![\wÀ-ỹĐđ])" + re.escape(k) + r"(?![\wÀ-ỹĐđ])", " ", text, flags=re.IGNORECASE)
    d = {k.lower(): v for k, v in e.items() if not _is_acronym(k)}
    out: list[str] = []
    for w in _WORD.findall(text):
        if (w in e) or (w.lower() in d and not _is_acronym(w)) or any(ch.isdigit() for ch in w):
            continue
        if any(ord(c) > 127 for c in w):  # có dấu tiếng Việt → tiếng Việt
            continue
        if len(w) >= 2 and w.isupper():  # viết tắt: AI, NASA, FBI…
            out.append(w)
            continue
        if not _VI_SYL.match(_strip_marks(w).lower()):
            out.append(w)
    seen = set()
    return [w for w in out if not (w.lower() in seen or seen.add(w.lower()))]
