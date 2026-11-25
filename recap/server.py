"""Giao diện web chạy ở localhost (FastAPI) + hàng đợi job (chạy lần lượt từng job để không tranh GPU)."""
from __future__ import annotations

import json
import queue
import shutil
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, health, maintenance, pipeline, settings
from .paths import JOBS_DIR, PRESETS_DIR, WEB_DIR
from .project import ProcessRunner, Project
from .tts import PROVIDERS as TTS_PROVIDERS, availability as tts_availability

app = FastAPI(title="Video Recap Tool", version=__version__)

# ---------------- hàng đợi ----------------
_q: "queue.Queue[tuple[str, str]]" = queue.Queue()
_runners: dict[str, ProcessRunner] = {}
_current: dict[str, Optional[str]] = {"job": None}
_queued: list[str] = []
_qlock = threading.Lock()


def enqueue(kind: str, job_id: str) -> None:
    with _qlock:
        if job_id in _queued or _current["job"] == job_id:
            raise HTTPException(409, "Job này đang chạy hoặc đang chờ.")
        _queued.append(job_id)
    _q.put((kind, job_id))


def _worker() -> None:
    while True:
        kind, jid = _q.get()
        with _qlock:
            if jid in _queued:
                _queued.remove(jid)
            _current["job"] = jid
        try:
            proj = Project.open(jid)
        except FileNotFoundError:
            _current["job"] = None
            continue
        if proj.state().get("status") == "cancelled":
            _current["job"] = None
            continue
        runner = ProcessRunner(proj)
        _runners[jid] = runner
        try:
            if kind == "run":
                pipeline.run_job(proj, runner)
            elif kind == "render":
                pipeline.render_job(proj, runner)
            elif kind == "ffmpeg":
                _run_user_ffmpeg(proj, runner)
        finally:
            _runners.pop(jid, None)
            _current["job"] = None


def _housekeeping() -> None:
    while True:
        try:
            removed = maintenance.cleanup({_current["job"] or "", *_queued})
            if removed:
                print("Đã tự xoá job cũ:", removed)
        except Exception as e:  # noqa: BLE001
            print("Lỗi dọn dẹp:", e)
        time.sleep(3600)


def _recover() -> None:
    """Khi mở lại tool: job đang chạy dở → báo lỗi; job đang chờ → xếp lại hàng."""
    if not JOBS_DIR.exists():
        return
    for d in sorted(JOBS_DIR.iterdir()):
        if not (d / "job.json").exists():
            continue
        p = Project(d)
        st = p.state().get("status")
        if st in ("running", "rendering"):
            p.update(status="error", stage="Lỗi", error="Tool bị tắt khi đang chạy. Bấm 'Chạy lại' để làm tiếp.")
        elif st == "queued":
            enqueue("run", p.id)


# ---------------- tiện ích ----------------
def _job_summary(p: Project) -> dict[str, Any]:
    st = p.state()
    o = st.get("options", {})
    return {
        "id": st.get("id"), "title": st.get("title") or o.get("url") or st.get("id"),
        "status": st.get("status"), "stage": st.get("stage"), "error": st.get("error"),
        "created": st.get("created"), "updated": st.get("updated"),
        "mode": o.get("mode"), "aspect": o.get("aspect"),
        "has_output": p.f("output", "final.mp4").exists(),
        "has_script": p.f("script.json").exists(),
        "queued_position": (_queued.index(p.id) + 1) if p.id in _queued else None,
        "is_current": _current["job"] == p.id,
    }


def _open(job_id: str) -> Project:
    try:
        return Project.open(job_id)
    except FileNotFoundError:
        raise HTTPException(404, "Không tìm thấy job") from None


MASK = "********"


def _is_secret(key: str, parent: str) -> bool:
    return key == "api_key" or parent == "api_keys"


def _mask(cfg: dict) -> dict:
    def walk(x, parent=""):
        if isinstance(x, dict):
            return {k: (MASK if _is_secret(k, parent) and isinstance(v, str) and v else walk(v, k)) for k, v in x.items()}
        return x
    return walk(cfg)


def _unmask(new: dict, old: dict, parent: str = "") -> dict:
    out = {}
    for k, v in new.items():
        if isinstance(v, dict):
            out[k] = _unmask(v, old.get(k, {}) if isinstance(old.get(k), dict) else {}, k)
        elif _is_secret(k, parent) and v == MASK:
            out[k] = old.get(k, "")
        else:
            out[k] = v
    return out


# ---------------- API ----------------
@app.get("/api/meta")
def meta():
    return {
        "version": __version__,
        "presets": sorted(p.stem for p in PRESETS_DIR.glob("*.md")),
        "tts_providers": list(TTS_PROVIDERS),
        "tts_status": {k: {"ok": v[0], "note": v[1]} for k, v in tts_availability().items()},
        "agents": list((settings.get("agent.commands", {}) or {}).keys()),
        "defaults": {
            "agent": settings.get("agent.provider"),
            "agent_models": settings.get("agent.models", {}),
            "codex_reasoning": settings.get("agent.codex_reasoning", ""),
            "claude_effort": settings.get("agent.claude_effort", ""),
            "codex_service_tier": settings.get("agent.codex_service_tier", ""),
            "tts_provider": settings.get("tts.provider"),
            "tts_voice": settings.get("tts.voice"),
            "tts_rate": settings.get("tts.rate"),
            "burn_subtitles": settings.get("render.burn_subtitles"),
            "bgm_folder": settings.get("render.bgm_folder"),
        },
    }


_probe_cache: dict[str, dict] = {}


@app.get("/api/probe")
def probe_url(url: str):
    """Lấy độ dài + tiêu đề video từ link (không tải video) để thanh trượt số phút khớp với nguồn."""
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Link không hợp lệ")
    if url in _probe_cache:
        return _probe_cache[url]
    import subprocess
    import sys as _sys
    from .ingest import _common_args
    from .paths import IS_WINDOWS
    cmd = [_sys.executable, "-m", "yt_dlp", "--no-playlist", "--skip-download", "--no-warnings",
           "--print", "%(duration)s", "--print", "%(title)s", *_common_args(settings.get("download", {}) or {}), url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                           **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}))
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Quá lâu không lấy được thông tin video") from None
    lines = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
    if r.returncode != 0 or not lines:
        raise HTTPException(400, "Không lấy được thông tin video từ link")
    try:
        dur = float(lines[0])
    except ValueError:
        dur = None
    out = {"duration": dur, "title": lines[1] if len(lines) > 1 else ""}
    _probe_cache[url] = out
    return out


@app.get("/api/agent-models")
def agent_models(provider: str):
    from .agent import list_models
    found = list_models(provider)
    if not found and provider == "claude":
        return {"models": ["opus", "sonnet", "haiku", "fable"], "from_agent": False}
    return {"models": found, "from_agent": bool(found)}


@app.get("/api/settings")
def get_settings():
    return _mask(settings.load())


@app.put("/api/settings")
async def put_settings(req: Request):
    data = await req.json()
    if not isinstance(data, dict):
        raise HTTPException(400, "Dữ liệu cài đặt không hợp lệ")
    merged = _unmask(data, settings.load())
    settings.save(merged)
    return {"ok": True}


@app.get("/api/health")
def get_health():
    return health.check()


@app.post("/api/update/ytdlp")
def post_update_ytdlp():
    rc, out = maintenance.update_ytdlp()
    return {"ok": rc == 0, "output": out}


@app.post("/api/install/vieneu")
def post_install_vieneu():
    rc, out = maintenance.install_vieneu()
    return {"ok": rc == 0, "output": out + ("\n\nĐã cài. Lần tạo giọng đầu tiên VieNeu sẽ tự tải model (cần mạng)."
                                            if rc == 0 else "")}


_vn_cache: dict[str, list[str]] = {}


# ---------------- nghe thử giọng ----------------
PREVIEW_DIR = JOBS_DIR.parent / "cache" / "voice_preview"
PREVIEW_TEXT = ("Các bạn có tin được không, cậu thiếu niên yếu ớt nhất làng lại chính là người duy nhất "
                "đứng dậy khi con quái vật xuất hiện. Và đây mới chỉ là khởi đầu.")
_preview_lock = threading.Lock()


def _make_preview(provider: str, voice: str, rate: Optional[float], text: str) -> str:
    import hashlib
    from . import pronounce
    from .tts import TTS, trim_silence
    t = TTS(provider or None, voice or None, rate)
    ok, note = tts_availability().get(t.provider, (True, ""))
    if not ok:
        raise RuntimeError(f"Nhà cung cấp giọng '{t.provider}' chưa dùng được: {note}")
    say = pronounce.apply(text, "vi")
    key = hashlib.sha1(f"{t.provider}|{t.voice}|{t.rate}|{say}".encode("utf-8")).hexdigest()[:16]
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out = PREVIEW_DIR / f"{key}.wav"
    if not out.exists():
        with _preview_lock:  # mỗi lần chỉ tạo một mẫu (VieNeu nạp model khá nặng)
            if not out.exists():
                tmp = PREVIEW_DIR / f"{key}.tmp.wav"
                t.synth(say, tmp, ProcessRunner(None))
                if settings.get("tts.trim_silence", True):
                    trim_silence(tmp)
                tmp.replace(out)
    # dọn bớt: giữ 60 mẫu gần nhất
    files = sorted(PREVIEW_DIR.glob("*.wav"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[60:]:
        f.unlink(missing_ok=True)
    return key


@app.post("/api/tts/preview")
async def post_tts_preview(req: Request):
    import asyncio
    b = await req.json()
    text = (b.get("text") or "").strip() or PREVIEW_TEXT
    rate = b.get("rate")
    try:
        rate = float(rate) if rate not in (None, "") else None
    except (TypeError, ValueError):
        rate = None
    try:
        key = await asyncio.to_thread(_make_preview, b.get("provider") or "", (b.get("voice") or "").strip(),
                                      rate, text[:400])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Không tạo được giọng thử: {e}")
    return {"url": f"/api/tts/preview/{key}.wav"}


@app.get("/api/tts/preview/{name}")
def get_tts_preview(name: str):
    import re
    if not re.fullmatch(r"[0-9a-f]{16}\.wav", name):
        raise HTTPException(404)
    f = PREVIEW_DIR / name
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="audio/wav")


@app.get("/api/omnivoice/voices")
def get_omnivoice_voices():
    from .tts import omnivoice_voices
    return {"voices": omnivoice_voices()}


@app.get("/api/elevenlabs/voices")
def get_elevenlabs_voices(refresh: bool = False):
    from .tts import elevenlabs_voices
    return {"voices": elevenlabs_voices(force=refresh)}


@app.get("/api/vieneu/voices")
def get_vieneu_voices():
    if "v" not in _vn_cache or not _vn_cache["v"]:
        _vn_cache["v"] = maintenance.vieneu_voices()
    return {"voices": _vn_cache["v"]}


@app.post("/api/update/tool")
def post_update_tool():
    rc, out = maintenance.update_tool()
    return {"ok": rc == 0, "output": out}


@app.get("/api/jobs")
def list_jobs():
    items = []
    if JOBS_DIR.exists():
        for d in JOBS_DIR.iterdir():
            if (d / "job.json").exists():
                items.append(_job_summary(Project(d)))
    items.sort(key=lambda x: x.get("created") or 0, reverse=True)
    return items


@app.post("/api/jobs")
async def create_job(options: str = Form("{}"), file: Optional[UploadFile] = File(None)):
    try:
        opts = json.loads(options or "{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "options không phải JSON") from None
    url = (opts.get("url") or "").strip()
    if not url and not file:
        raise HTTPException(400, "Cần dán link hoặc chọn file video.")
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Link phải bắt đầu bằng http:// hoặc https://")
    opts["url"] = url
    proj = Project.create(opts)
    if file is not None and file.filename:
        ext = Path(file.filename).suffix.lower() or ".mp4"
        tmp = proj.f("upload" + ext)
        with open(tmp, "wb") as fh:
            while True:
                chunk = await file.read(8 * 1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
        if not proj.state().get("title"):
            proj.update(title=Path(file.filename).stem)
        from .ingest import import_file
        import_file(proj, tmp)
    enqueue("run", proj.id)
    return _job_summary(proj)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    p = _open(job_id)
    st = p.state()
    return {**_job_summary(p), "options": _mask(st.get("options", {})), "warnings": st.get("warnings", []),
            "source_duration": st.get("source_duration")}


@app.get("/api/jobs/{job_id}/log")
def get_log(job_id: str):
    return JSONResponse({"log": _open(job_id).log_tail()})


@app.get("/api/jobs/{job_id}/script")
def get_script(job_id: str):
    p = _open(job_id)
    if not p.f("script.json").exists():
        raise HTTPException(404, "Chưa có kịch bản")
    from .media import read_text_any
    try:
        data = json.loads(read_text_any(p.f("script.json")))
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"script.json hỏng: {e}") from None
    errs, warns = pipeline.validate(p)
    return {"script": data, "errors": errs, "warnings": warns}


@app.get("/api/jobs/{job_id}/sync")
def get_sync(job_id: str):
    p = _open(job_id)
    if not p.f("script.json").exists():
        raise HTTPException(404, "Chưa có kịch bản")
    from .sync import report
    notes = {}
    if p.f("check_notes.json").exists():
        from .media import read_text_any
        try:
            notes = {int(n["id"]): {"on_screen": n.get("on_screen", ""), "matches": n.get("matches")}
                     for n in json.loads(read_text_any(p.f("check_notes.json")))}
        except Exception:  # noqa: BLE001
            notes = {}
    try:
        rows = report(p)
        for r in rows:
            if r["id"] in notes:
                r["note"] = notes[r["id"]]
        return {"rows": rows}
    except Exception as e:  # noqa: BLE001 — kịch bản đang lỗi thì không đo được
        return {"rows": [], "error": str(e)}


@app.put("/api/jobs/{job_id}/script")
async def put_script(job_id: str, req: Request):
    p = _open(job_id)
    if _current["job"] == job_id or job_id in _queued:
        raise HTTPException(409, "Job đang chạy, chưa sửa được kịch bản.")
    data = await req.json()
    if p.f("script.json").exists():
        shutil.copyfile(p.f("script.json"), p.f("script.backup.json"))
    p.f("script.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    errs, warns = pipeline.validate(p)
    p.update(warnings=warns)
    p.log("Người dùng đã sửa kịch bản trên giao diện.")
    return {"errors": errs, "warnings": warns}


@app.post("/api/jobs/{job_id}/render")
async def post_render(job_id: str, req: Request):
    p = _open(job_id)
    try:
        body = await req.json()
    except Exception:
        body = {}
    if isinstance(body, dict) and body:
        st = p.state()
        for k in ("tts_provider", "tts_voice", "tts_rate", "burn_subtitles", "use_bgm", "aspect"):
            if k in body:
                st["options"][k] = body[k]
        p.write_state(st)
    errs, _ = pipeline.validate(p)
    if errs:
        raise HTTPException(400, "Kịch bản còn lỗi: " + "; ".join(errs[:3]))
    p.update(status="queued", stage="Chờ dựng video", error=None)
    enqueue("render", job_id)
    return _job_summary(p)


@app.post("/api/jobs/{job_id}/rerun")
async def post_rerun(job_id: str, req: Request):
    """Chạy lại từ đầu phần agent (giữ lại video nguồn và dữ liệu đã chuẩn bị)."""
    p = _open(job_id)
    body = {}
    try:
        body = await req.json()
    except Exception:
        pass
    polish = bool(body.get("polish"))
    if polish:
        if not p.f("script.json").exists():
            raise HTTPException(400, "Job chưa có kịch bản để viết lại lời.")
        shutil.copy2(str(p.f("script.json")), p.f("script.before_polish.json"))
    elif body.get("new_script", True) and p.f("script.json").exists():
        shutil.move(str(p.f("script.json")), p.f("script.previous.json"))
    st = p.state()
    o = st.setdefault("options", {})
    o["rewrite"] = "polish" if polish else None
    o["polish_note"] = (body.get("polish_note") or "").strip() if polish else ""
    if body.get("narration_style"):
        o["narration_style"] = body["narration_style"]
    if body.get("instructions") is not None:
        o["instructions"] = body["instructions"]
    p.write_state(st)
    p.update(status="queued", stage="Đang chờ", error=None)
    enqueue("run", job_id)
    return _job_summary(p)


@app.post("/api/jobs/{job_id}/cancel")
def post_cancel(job_id: str):
    p = _open(job_id)
    r = _runners.get(job_id)
    if r:
        r.cancel()
    with _qlock:
        if job_id in _queued:
            _queued.remove(job_id)
    if p.state().get("status") in ("queued", "running", "rendering"):
        p.update(status="cancelled", stage="Đã huỷ")
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    p = _open(job_id)
    if _current["job"] == job_id:
        raise HTTPException(409, "Job đang chạy — huỷ trước rồi mới xoá.")
    with _qlock:
        if job_id in _queued:
            _queued.remove(job_id)
    shutil.rmtree(p.path, ignore_errors=True)
    return {"ok": True}


# ---------------- khách tự chỉnh bằng ffmpeg ----------------
FFMPEG_PRESETS = [
    {"name": "Cắt một đoạn (giây bắt đầu → kết thúc)", "args": '-ss 0 -to 60 -i "{in}" -c copy "{out}"'},
    {"name": "Tăng âm lượng lên 1,5 lần", "args": '-i "{in}" -c:v copy -af volume=1.5 "{out}"'},
    {"name": "Tăng tốc toàn video 1,1 lần", "args": '-i "{in}" -filter_complex "[0:v]setpts=PTS/1.1[v];[0:a]atempo=1.1[a]" -map "[v]" -map "[a]" "{out}"'},
    {"name": "Thêm logo góc phải trên (đổi đường dẫn logo.png)", "args": '-i "{in}" -i "C:/duong/dan/logo.png" -filter_complex "[1:v]scale=160:-1[l];[0:v][l]overlay=W-w-30:30" -c:a copy "{out}"'},
    {"name": "Thêm chữ đầu video 3 giây", "args": '-i "{in}" -vf "drawtext=text=\'TÓM TẮT\':fontsize=64:fontcolor=white:borderw=3:x=(w-tw)/2:y=80:enable=\'lt(t,3)\'" -c:a copy "{out}"'},
    {"name": "Làm mờ dần đầu/cuối (1 giây)", "args": '-i "{in}" -vf "fade=t=in:st=0:d=1" -af "afade=t=in:st=0:d=1" "{out}"'},
    {"name": "Xuất bản nhẹ 720p", "args": '-i "{in}" -vf scale=-2:720 -c:v libx264 -preset veryfast -crf 24 -c:a aac -b:a 128k "{out}"'},
    {"name": "Tách riêng âm thanh (mp3)", "args": '-i "{in}" -vn -c:a libmp3lame -q:a 2 "{out_mp3}"'},
]


def _outputs(p: Project) -> list[dict]:
    d = p.f("output")
    if not d.exists():
        return []
    items = []
    for f in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.suffix.lower() in (".mp4", ".mov", ".mkv", ".mp3", ".wav", ".srt"):
            items.append({"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime})
    return items


def _next_edit_name(p: Project, ext: str) -> str:
    n = 1
    while p.f("output", f"edit_{n:02d}{ext}").exists():
        n += 1
    return f"edit_{n:02d}{ext}"


def _run_user_ffmpeg(p: Project, runner: ProcessRunner) -> None:
    import shlex
    from . import media
    st = p.state()
    job = st.get("ffmpeg_job") or {}
    prev = st.get("status") if st.get("status") not in ("queued",) else "done"
    try:
        p.update(status="rendering", stage="Đang chạy lệnh ffmpeg của bạn", error=None)
        args = shlex.split(job["args"], posix=True)
        cmd = [media.ffmpeg(), "-hide_banner", "-y", *args]
        p.log("Lệnh ffmpeg của người dùng: " + " ".join(cmd))
        rc = runner.run(cmd, cwd=p.f("output"))
        if rc != 0:
            raise RuntimeError(f"ffmpeg báo lỗi (mã {rc}) — xem Nhật ký để biết chi tiết.")
        p.log(f"Xong: output/{job['out']}")
        p.update(status=job.get("prev_status") or "done", stage="Hoàn tất", last_edit=job["out"])
    except Exception as e:  # noqa: BLE001
        p.log(f"LỖI ffmpeg: {e}")
        p.update(status=job.get("prev_status") or prev or "done", stage="Lệnh ffmpeg lỗi", error=str(e))


@app.get("/api/ffmpeg/presets")
def ffmpeg_presets():
    return FFMPEG_PRESETS


@app.get("/api/jobs/{job_id}/outputs")
def list_outputs(job_id: str):
    return _outputs(_open(job_id))


@app.post("/api/jobs/{job_id}/ffmpeg")
async def post_ffmpeg(job_id: str, req: Request):
    p = _open(job_id)
    body = await req.json()
    args = (body.get("args") or "").strip()
    src_name = Path(body.get("input") or "final.mp4").name
    src = p.f("output", src_name)
    if not args:
        raise HTTPException(400, "Chưa nhập lệnh")
    if not src.exists():
        raise HTTPException(400, f"Không có file output/{src_name}")
    if "{out" not in args:
        raise HTTPException(400, 'Lệnh phải có "{out}" (hoặc "{out_mp3}") làm file kết quả — tool không cho ghi đè video gốc')
    out_name = _next_edit_name(p, ".mp3" if "{out_mp3}" in args else (Path(src_name).suffix or ".mp4"))
    args = (args.replace("{in}", src.as_posix()).replace("{out_mp3}", out_name).replace("{out}", out_name))
    st = p.state()
    st["ffmpeg_job"] = {"args": args, "out": out_name, "prev_status": st.get("status")}
    p.write_state(st)
    enqueue("ffmpeg", job_id)
    return {"out": out_name}


@app.get("/api/editors")
def editors_status():
    from .editors import find_losslesscut
    exe = find_losslesscut()
    return {"losslesscut": str(exe) if exe else None}


@app.post("/api/install/losslesscut")
def post_install_losslesscut():
    from .editors import install_losslesscut
    ok, msg = install_losslesscut()
    return {"ok": ok, "output": msg}


@app.post("/api/jobs/{job_id}/open-losslesscut")
async def open_lc(job_id: str, req: Request):
    from .editors import find_losslesscut, open_in_losslesscut
    p = _open(job_id)
    try:
        body = await req.json()
    except Exception:
        body = {}
    name = Path((body or {}).get("file") or "final.mp4").name
    f = p.f("output", name)
    if not f.exists():
        raise HTTPException(404, f"Không có file output/{name}")
    if not find_losslesscut():
        raise HTTPException(409, "Máy chưa có LosslessCut")
    open_in_losslesscut(f)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/open-folder")
def open_folder(job_id: str):
    import os
    import subprocess
    from .paths import IS_WINDOWS
    p = _open(job_id)
    d = p.f("output") if p.f("output").exists() else p.path
    if IS_WINDOWS:
        os.startfile(str(d))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(d)])
    return {"ok": True, "path": str(d)}


@app.post("/api/jobs/{job_id}/open-terminal")
def open_terminal(job_id: str):
    """Mở cửa sổ dòng lệnh ngay trong thư mục video, đã có sẵn ffmpeg (bản portable của tool)."""
    import os
    import subprocess
    from .paths import IS_WINDOWS
    p = _open(job_id)
    d = p.f("output") if p.f("output").exists() else p.path
    if not IS_WINDOWS:
        raise HTTPException(400, "Chỉ hỗ trợ trên Windows")
    banner = ("echo. & echo  Cua so ffmpeg - thu muc: %CD% & echo  Vi du: ffmpeg -i final.mp4 -ss 0 -to 60 -c copy cat.mp4 "
              "& echo  Huong dan: ffmpeg -h & echo.")
    subprocess.Popen(["cmd.exe", "/k", f"chcp 65001 >nul & {banner}"], cwd=str(d), env=os.environ.copy(),
                     creationflags=subprocess.CREATE_NEW_CONSOLE)  # type: ignore[attr-defined]
    return {"ok": True}


_ALLOWED_EXT = {".mp3", ".mp4", ".mkv", ".webm", ".mov", ".jpg", ".png", ".srt", ".wav", ".md", ".txt", ".json"}


@app.get("/api/jobs/{job_id}/file")
def get_file(job_id: str, path: str, download: int = 0):
    p = _open(job_id)
    target = (p.path / path).resolve()
    if p.path.resolve() not in target.parents or target.suffix.lower() not in _ALLOWED_EXT or not target.exists():
        raise HTTPException(404, "Không có file")
    name = None
    if download:
        title = (p.state().get("title") or p.id)
        safe = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:80] or p.id
        name = f"{safe}{target.suffix}"
    return FileResponse(target, filename=name)


@app.get("/api/jobs/{job_id}/source")
def get_source(job_id: str):
    p = _open(job_id)
    src = p.source()
    if not src:
        raise HTTPException(404, "Chưa có video nguồn")
    return FileResponse(src)


app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


def serve(host: Optional[str] = None, port: Optional[int] = None, open_browser: bool = True) -> None:
    import logging
    import uvicorn
    # Trình duyệt ngắt kết nối giữa chừng khi tua video → Windows in ra lỗi WinError 10054 vô hại; ẩn đi cho đỡ rối
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
    host = host or settings.get("server.host", "127.0.0.1")
    port = int(port or settings.get("server.port", 7860))
    JOBS_DIR.mkdir(exist_ok=True)
    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_housekeeping, daemon=True).start()
    _recover()
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}"
    print(f"\n  Video Recap Tool {__version__} đang chạy tại: {url}\n  (Đóng cửa sổ này để tắt tool)\n")
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
