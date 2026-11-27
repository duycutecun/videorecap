"""Nạp lại SRT voice đã sửa tay: mỗi dòng (cue) = cách đọc của một đoạn trong script.json.

Luồng dùng: dựng video → tải `final.voice.srt` → sửa chữ cho giọng đọc đúng ý (KHÔNG thêm/bớt/đổi thứ tự dòng)
→ nạp lại → tool ghi phần đã sửa vào trường `speak` của đúng đoạn đó → dựng lại, chỉ đoạn đổi chữ mới tạo lại giọng.
SRT hiển thị (lời bình) không bị đụng tới.
"""
from __future__ import annotations

import json
import re
import shutil

from . import pronounce
from .project import Project
from .script_schema import Script

_TIME = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}")
_TAG = re.compile(r"<[^>]+>|\{\\[^}]*\}")


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1258"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse(text: str) -> list[str]:
    """Chữ của từng cue theo thứ tự (gộp các dòng trong một cue, bỏ thẻ định dạng)."""
    cues: list[str] = []
    cur: list[str] | None = None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i, line in enumerate(lines):
        if _TIME.match(line):
            if cur is not None:
                cues.append(" ".join(cur))
            cur = []
            continue
        if cur is None:
            continue
        s = _TAG.sub("", line).strip()
        if not s:
            continue
        # số thứ tự của cue kế tiếp (dòng chỉ có số, ngay trước một dòng thời gian) không phải lời đọc
        if s.isdigit() and i + 1 < len(lines) and _TIME.match(lines[i + 1]):
            continue
        cur.append(s)
    if cur is not None:
        cues.append(" ".join(cur))
    return [" ".join(c.split()) for c in cues]


def apply(project: Project, text: str) -> dict:
    """Ghi các dòng đã sửa vào `speak`. Trả về {"total", "changed": [id...]}; ném ValueError nếu file không khớp."""
    f = project.f("script.json")
    if not f.exists():
        raise ValueError("Job chưa có kịch bản (script.json).")
    from .media import read_text_any
    raw = json.loads(read_text_any(f))
    sc = Script.model_validate(raw)
    cues = parse(text)
    if not cues:
        raise ValueError("Không đọc được dòng phụ đề nào — file phải là .srt (có dòng thời gian 00:00:01,000 --> 00:00:03,000).")
    if len(cues) != len(sc.segments):
        raise ValueError(f"SRT voice có {len(cues)} dòng nhưng kịch bản có {len(sc.segments)} đoạn. Mỗi dòng ứng với "
                         f"đúng một đoạn: chỉ sửa chữ, không thêm/bớt/gộp/tách dòng. Nếu kịch bản đã đổi sau lần dựng "
                         f"cuối, hãy dựng lại rồi tải SRT voice mới về sửa.")
    empty = [s.id for s, c in zip(sc.segments, cues) if not c]
    if empty:
        raise ValueError("Dòng của đoạn " + ", ".join(map(str, empty[:10])) + " đang trống — mỗi đoạn phải có lời đọc.")
    terms = pronounce.job_terms(project.path)
    lang = sc.language or "vi"
    changed: list[int] = []
    for seg, rseg, cue in zip(sc.segments, raw["segments"], cues):
        current = " ".join(pronounce.spoken_text(seg, lang, terms).split())
        if cue != current:
            rseg["speak"] = cue
            changed.append(seg.id)
    if changed:
        shutil.copyfile(f, project.f("script.backup.json"))
        f.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        project.log(f"Đã nạp SRT voice sửa tay: đổi cách đọc {len(changed)} đoạn ({', '.join(map(str, changed[:20]))}"
                    f"{'…' if len(changed) > 20 else ''}). Dựng lại để tạo giọng mới cho các đoạn này.")
    return {"total": len(cues), "changed": changed}
