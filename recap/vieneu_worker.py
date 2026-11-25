"""Chạy VieNeu-TTS trong một tiến trình riêng: nạp model MỘT lần rồi đọc lần lượt mọi đoạn.

Cách gọi:
  python -m recap.vieneu_worker --batch batch.json     (batch.json = {"voice": "...", "items": [{"text":..., "out":...}]})
  python -m recap.vieneu_worker --list-voices
In ra từng dòng: "OK <đường dẫn>" | "ERR <lỗi>" | "VOICES <json>".
"""
from __future__ import annotations

import argparse
import json
import sys


def _load(mode: str = "v3turbo"):
    from vieneu import Vieneu
    return Vieneu(mode=mode or "v3turbo")


def list_voices(tts) -> list[str]:
    for name in ("list_preset_voices", "list_voices", "get_voices", "voices"):
        obj = getattr(tts, name, None)
        try:
            v = obj() if callable(obj) else obj
        except Exception:  # noqa: BLE001
            continue
        if not v:
            continue
        if isinstance(v, dict):
            return [str(k) for k in v.keys()]
        out = []
        for x in v:
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, (tuple, list)) and x:
                out.append(str(x[-1]))   # list_preset_voices() trả (nhãn, mã giọng) → lấy mã giọng
            elif isinstance(x, dict):
                out.append(str(x.get("name") or x.get("id") or x))
            else:
                out.append(str(getattr(x, "name", x)))
        return out
    return []


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch")
    ap.add_argument("--list-voices", action="store_true")
    ap.add_argument("--mode", default="v3turbo")
    a = ap.parse_args()
    try:
        tts = _load(a.mode)
    except Exception as e:  # noqa: BLE001
        print(f"ERR Không nạp được VieNeu: {e}", flush=True)
        return 2
    if a.list_voices:
        print("VOICES " + json.dumps(list_voices(tts), ensure_ascii=False), flush=True)
        return 0
    job = json.loads(open(a.batch, encoding="utf-8").read())
    voice = (job.get("voice") or "").strip() or None
    resolve = getattr(tts, "resolve_voice_name", None)
    if voice and callable(resolve) and resolve(voice) is None:
        import re
        cands = [re.sub(r"\(.*?\)", "", voice).strip(), voice.split("(")[0].strip(), voice.replace("⭐", "").strip()]
        found = next((c for c in cands if c and resolve(c)), None)
        if found:
            voice = resolve(found)
        else:
            names = list_voices(tts)
            print(f"WARN Không có giọng '{voice}' — dùng giọng mặc định. Giọng có sẵn: {', '.join(names[:20])}", flush=True)
            voice = None
    rc = 0
    for it in job["items"]:
        try:
            kw = {"voice": voice} if voice else {}
            audio = tts.infer(it["text"], **kw)
            tts.save(audio, it["out"])
            print(f"OK {it['out']}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"ERR {e}", flush=True)
            rc = 1
            break
    return rc


if __name__ == "__main__":
    sys.exit(main())
