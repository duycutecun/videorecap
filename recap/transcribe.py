"""Bước 2: nhận dạng lời thoại bằng faster-whisper (GPU NVIDIA nếu có, không thì CPU)."""
from __future__ import annotations

import json
import re
import subprocess
import urllib.request
import uuid
from pathlib import Path

from . import media, settings
from .paths import MODELS_DIR, IS_WINDOWS
from .project import Project


def _pick_device(cfg: dict) -> tuple[str, str]:
    device = cfg.get("device") or "auto"
    compute = cfg.get("compute_type") or "auto"
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    if compute == "auto":
        # GPU: int8_float16 — chính xác gần như float16 nhưng chỉ tốn ~1 GB VRAM (dịch vụ OmniVoice
        # chạy ngầm đã giữ ~5 GB trên card 8 GB) và chạy nhanh trên nhân Tensor của RTX 20xx.
        compute = "int8_float16" if device == "cuda" else "int8"
    return device, compute


def load_model(cfg: dict | None = None):
    from faster_whisper import WhisperModel
    cfg = cfg or settings.get("transcribe", {})
    device, compute = _pick_device(cfg)
    name = cfg.get("model") or "large-v3-turbo"
    kw = {}
    if device == "cpu":
        # Máy nhiều nhân (vd Xeon 24 nhân / 48 luồng): mặc định thư viện chỉ dùng 4 luồng → tăng lên
        import os
        n = int(cfg.get("cpu_threads") or 0) or max(4, min(24, (os.cpu_count() or 8) // 2))
        kw["cpu_threads"] = n
    model = WhisperModel(name, device=device, compute_type=compute, download_root=str(MODELS_DIR / "whisper"), **kw)
    return model, device, compute


# ---------------- nguồn lời thoại thay cho Whisper tại máy ----------------
_TS = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


def _ts(t: str) -> float:
    m = _TS.search(t)
    if not m:
        return 0.0
    h, mi, se, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")) / 1000


def parse_srt(text: str) -> list[dict]:
    out, last = [], None
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = [ln for ln in block.strip().split("\n") if ln.strip()]
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        a, b = lines[idx].split("-->")[:2]
        txt = re.sub(r"<[^>]+>|\{[^}]+\}", "", " ".join(lines[idx + 1:])).strip()
        if not txt or txt == last:  # phụ đề tự động hay lặp dòng
            continue
        last = txt
        out.append({"start": round(_ts(a), 2), "end": round(_ts(b), 2), "text": txt})
    return out


def _pick_sub_file(project: Project) -> Path | None:
    files = sorted(project.path.glob("subs.*.srt"))
    if not files:
        return None
    def rank(p: Path) -> int:
        lang = p.name.split(".")[1]
        order = ["vi", "en", "vi-orig", "en-orig"]
        return order.index(lang) if lang in order else (10 if lang.endswith("-orig") else 20)
    return min(files, key=rank)


def from_embedded_subtitles(project: Project, src: Path) -> list[dict]:
    """File mkv/mp4 có sẵn track phụ đề dạng chữ → trích ra dùng luôn."""
    text_codecs = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}
    streams = [s for s in media.probe(src).get("streams", []) if s.get("codec_type") == "subtitle"
               and s.get("codec_name") in text_codecs]
    if not streams:
        return []
    pref = ["vie", "vi", "eng", "en"]
    streams.sort(key=lambda s: pref.index(s.get("tags", {}).get("language", "")) if s.get("tags", {}).get("language", "") in pref else 9)
    out = project.f("subs.embedded.srt")
    r = subprocess.run([media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
                        "-map", f"0:{streams[0]['index']}", "-f", "srt", str(out)],
                       **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}))
    if r.returncode != 0 or not out.exists():
        return []
    return parse_srt(out.read_text(encoding="utf-8", errors="replace"))


def _multipart(fields: dict, file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'
                 f'Content-Type: audio/mpeg\r\n\r\n'.encode() + file_path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


CLOUD = {
    "groq": ("https://api.groq.com/openai/v1/audio/transcriptions", "whisper-large-v3-turbo"),
    "openai": ("https://api.openai.com/v1/audio/transcriptions", "whisper-1"),
}


def cloud_transcribe(project: Project, wav: Path, engine: str, cfg: dict, lang: str | None) -> dict:
    """Gửi âm thanh lên API (Groq / OpenAI), chia đoạn 10 phút để dưới giới hạn dung lượng file."""
    url, default_model = CLOUD[engine]
    key = (cfg.get("api_keys") or {}).get(engine) or ""
    if not key:
        raise RuntimeError(f"Chưa nhập API key {engine} cho nhận dạng lời thoại (Cài đặt → Nhận dạng lời thoại).")
    model = (cfg.get("cloud_models") or {}).get(engine) or default_model
    cdir = project.f("render", "chunks")
    cdir.mkdir(parents=True, exist_ok=True)
    chunk = 600
    subprocess.run([media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav), "-ac", "1",
                    "-ar", "16000", "-b:a", "48k", "-f", "segment", "-segment_time", str(chunk),
                    str(cdir / "c%03d.mp3")], check=True,
                   **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}))
    files = sorted(cdir.glob("c*.mp3"))
    segs, language = [], ""
    for i, f in enumerate(files):
        fields = {"model": model, "response_format": "verbose_json", "temperature": "0"}
        if lang:
            fields["language"] = lang
        body, ctype = _multipart(fields, "file", f)
        for attempt in range(3):
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": ctype})
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    data = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")[:300]
                if e.code in (429, 500, 502, 503) and attempt < 2:
                    project.log(f"  API bận ({e.code}), chờ rồi thử lại…")
                    import time
                    time.sleep(20 * (attempt + 1))
                    continue
                raise RuntimeError(f"Lỗi API nhận dạng ({e.code}): {detail}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < 2:
                    project.log(f"  lỗi mạng ({e}), thử lại…")
                    import time
                    time.sleep(10)
                    continue
                raise RuntimeError(f"Không kết nối được API nhận dạng {engine}: {e}") from None
        language = language or data.get("language", "")
        off = i * chunk
        for s in data.get("segments") or []:
            segs.append({"start": round(off + float(s["start"]), 2), "end": round(off + float(s["end"]), 2),
                         "text": str(s.get("text", "")).strip()})
        project.log(f"  nhận dạng (cloud {engine}): {i + 1}/{len(files)} đoạn 10 phút")
    for f in files:
        f.unlink(missing_ok=True)
    return {"language": language, "segments": segs}


def _save(project: Project, data: dict) -> dict:
    project.f("transcript.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"[{media.fmt_ts(s['start'])} - {media.fmt_ts(s['end'])}] {s['text']}" for s in data["segments"]]
    media.write_text_bom(project.f("transcript.txt"), "\n".join(lines) + "\n")
    return data


def run(project: Project) -> dict:
    src = project.source()
    if not src:
        raise RuntimeError("Chưa có video nguồn.")
    if project.f("transcript.json").exists():
        project.log("Đã có transcript.json — bỏ qua bước nhận dạng lời thoại.")
        return json.loads(project.f("transcript.json").read_text(encoding="utf-8"))

    cfg = settings.get("transcribe", {})
    if not project.state().get("source_has_audio", True):
        data = {"language": "", "segments": []}
        project.f("transcript.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        project.f("transcript.txt").write_text("(Video không có âm thanh)\n", encoding="utf-8")
        return data

    # 1) Phụ đề có sẵn (tải kèm từ link hoặc nằm trong file) → không cần nhận dạng
    if cfg.get("use_source_subtitles", True):
        sub = _pick_sub_file(project)
        segs = parse_srt(sub.read_text(encoding="utf-8", errors="replace")) if sub else from_embedded_subtitles(project, src)
        if len(segs) >= 5:
            name = sub.name if sub else "track phụ đề trong file"
            project.log(f"Dùng phụ đề có sẵn ({name}, {len(segs)} dòng) — bỏ qua bước nhận dạng lời thoại.")
            lang_guess = sub.name.split(".")[1].replace("-orig", "") if sub else ""
            return _save(project, {"language": lang_guess, "segments": segs, "source": "subtitles"})

    # Tách âm thanh ra WAV 16 kHz mono cho nhanh và ổn định
    wav = project.f("render", "audio16k.wav")
    wav.parent.mkdir(exist_ok=True)
    subprocess.run([media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
                    "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True,
                   **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}))

    lang = (cfg.get("language") or project.options.get("source_language") or "").strip() or None
    engine = (cfg.get("engine") or "local").lower()
    if engine in CLOUD:
        project.log(f"Nhận dạng lời thoại bằng API: {engine}")
        data = cloud_transcribe(project, wav, engine, cfg, lang)
        try:
            wav.unlink()
        except OSError:
            pass
        project.log(f"Xong lời thoại: {len(data['segments'])} câu")
        return _save(project, data)

    model, device, compute = load_model(cfg)
    if device == "cpu" and cfg.get("cpu_model"):
        cfg = {**cfg, "model": cfg["cpu_model"]}
        model, device, compute = load_model(cfg)
    project.log(f"Nhận dạng lời thoại: model={cfg.get('model')}, device={device}, compute={compute}")
    speed = (cfg.get("speed") or "fast").lower()
    beam = int(cfg.get("beam_size") or (1 if speed == "fast" else 5))
    # Xử lý theo lô chỉ có lợi trên GPU. Trên CPU (nhất là RAM chạy 1 kênh) lô làm chậm hẳn
    # và không báo tiến độ — đã gặp: 10 phút âm thanh chạy hơn 30 phút chưa xong.
    batch = int(cfg.get("batch_size") or 0) or (16 if device == "cuda" else 0)
    if device != "cuda":
        batch = 0

    def _pass(model, device, batch):
        if speed == "fast" and batch > 0:
            # Chạy song song nhiều đoạn âm thanh cùng lúc trên GPU — nhanh hơn nhiều lần
            from faster_whisper import BatchedInferencePipeline
            project.log(f"  chế độ nhanh: batch={batch}, beam={beam}")
            segments, info = BatchedInferencePipeline(model=model).transcribe(
                str(wav), language=lang, beam_size=beam, batch_size=batch, without_timestamps=False)
        else:
            project.log(f"  chế độ {'chính xác' if speed != 'fast' else 'nhanh'} ({device}): beam={beam}")
            segments, info = model.transcribe(str(wav), language=lang, vad_filter=True, beam_size=beam,
                                              condition_on_previous_text=(speed != "fast"))
        total = max(1.0, float(project.state().get("source_duration") or info.duration or 1))
        out = []
        last_pct = -10
        for s in segments:
            out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
            pct = int(s.end / total * 100)
            if pct >= last_pct + 10:
                project.log(f"  nhận dạng: {pct}%")
                last_pct = pct
        return out, info

    # GPU hết bộ nhớ (vd đang dựng video hoặc OmniVoice đang đọc) → giảm batch, cuối cùng chuyển CPU
    while True:
        try:
            out, info = _pass(model, device, batch)
            break
        except RuntimeError as e:
            if device != "cuda" or "out of memory" not in str(e).lower():
                raise
            if batch > 4:
                batch //= 2
                project.log(f"  GPU hết bộ nhớ — thử lại với batch={batch}")
                continue
            project.log("  GPU vẫn hết bộ nhớ — chuyển sang CPU (chậm hơn)")
            del model
            import gc
            gc.collect()
            cfg = {**cfg, "device": "cpu", "compute_type": "int8"}
            if cfg.get("cpu_model"):
                cfg["model"] = cfg["cpu_model"]
            model, device, compute = load_model(cfg)
            batch = 0
    # trả VRAM ngay cho bước sau (OmniVoice / NVENC) thay vì chờ Python tự dọn
    del model
    import gc
    gc.collect()
    data = _save(project, {"language": info.language, "segments": out})
    try:
        wav.unlink()
    except OSError:
        pass
    project.log(f"Xong lời thoại: {len(out)} câu, ngôn ngữ phát hiện: {info.language}")
    return data
