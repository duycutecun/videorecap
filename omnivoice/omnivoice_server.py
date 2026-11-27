"""OmniVoice voice-clone service (GPU) on 127.0.0.1:8766 (portable cho VideoRecap).

Voices live in voices/<slug>/ref_1.wav + ref_1.txt (3-10 s clean clip + exact transcript).
The voice name is voices/<slug>/name.txt (e.g. "Hải Đăng"); the clone prompt is cached as prompt.pt.
  GET  /health -> {"status","backend","device","model","load_seconds","voices":[[label,name]],"sample_rate"}
  POST /tts    {"text","voice","path","speed"?,"num_step"?}  (path: a .wav inside omnivoice/outputs) -> {"seconds","duration","sample_rate"}
  POST /tts_batch {"texts":[...],"voice","speed"?,"num_step"?} -> {"items":[{"wav_b64","duration"}],"seconds","sample_rate"}
"""
import os
for _k, _v in (
    ("HF_HUB_DISABLE_TELEMETRY", "1"),
    ("HF_HUB_DISABLE_SYMLINKS_WARNING", "1"),
    ("PYTHONIOENCODING", "utf-8"),
    ("OMP_NUM_THREADS", "8"),
    ("MKL_NUM_THREADS", "8"),
    ("OPENBLAS_NUM_THREADS", "8"),
    ("NUMEXPR_NUM_THREADS", "8"),
    ("NUMBA_NUM_THREADS", "8"),
    ("OMP_WAIT_POLICY", "PASSIVE"),
):
    os.environ.setdefault(_k, _v)

_LOCAL_HF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "hf")
if not os.environ.get("HF_HOME") and os.path.isdir(_LOCAL_HF):
    os.environ["HF_HOME"] = _LOCAL_HF

import atexit
import faulthandler
import json
import logging
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Runs windowless under pythonw.exe: there is no console, so send stdout/stderr to a file.
if sys.stdout is None or sys.stderr is None:
    _con = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "omnivoice-console.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _con
    sys.stderr = sys.stderr or _con
_fh = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "omnivoice-crash.log"), "a", encoding="utf-8")
faulthandler.enable(file=_fh)

HERE = Path(__file__).resolve().parent
VOICES = HERE / "voices"
OUTPUT_DIR = (HERE / "outputs").resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get("VIENEU_PORT", "8766"))

_model_file = HERE / "model.txt"
_default_model = _model_file.read_text(encoding="utf-8").strip() if _model_file.exists() else ""
MODEL = os.environ.get("OMNI_MODEL") or _default_model or "kjanh/KhanhTTS-OmniVoice"
STEPS = int(os.environ.get("OMNI_STEPS", "32"))
BATCH = max(1, int(os.environ.get("OMNI_BATCH", "8")))
BATCH_CHARS = max(200, int(os.environ.get("OMNI_BATCH_CHARS", "1400")))
CPU_THREADS = max(1, min(16, int(os.environ.get("OMNI_CPU_THREADS", str(max(1, min(8, (os.cpu_count() or 8) // 2)))))))
SR = 24000

logging.basicConfig(filename=str(HERE / "omnivoice-server.log"), level=logging.INFO, encoding="utf-8",
                    format=f"%(asctime)s %(levelname)s [omni {os.getpid()}] %(message)s")
log = logging.getLogger("omnivoice")
STATE = {"status": "loading", "backend": "omnivoice", "device": None, "model": MODEL, "load_seconds": None,
         "error": None, "voices": [], "sample_rate": SR}
M, PROMPTS, LOCK = None, {}, threading.Lock()


def load_voices(model):
    from omnivoice import VoiceClonePrompt
    if not VOICES.is_dir():
        STATE["voices"] = []
        return
    dirs = sorted((p for p in VOICES.iterdir() if p.is_dir()), key=lambda p: (p.name != "haidang", p.name.lower()))
    for d in dirs:
        name = (d / "name.txt").read_text(encoding="utf-8").strip() if (d / "name.txt").exists() else d.name
        cache = d / f"prompt_{MODEL.replace('/', '_')}.pt"
        try:
            pr = None
            if cache.exists():
                try:
                    pr = VoiceClonePrompt.load(str(cache))
                except Exception as ce:
                    log.warning("voice cache %s invalid (%s) - rebuilding from ref audio", d.name, ce)
                    cache.unlink(missing_ok=True)
            if pr is None:
                ref = d / "ref_1.wav"
                txt = (d / "ref_1.txt").read_text(encoding="utf-8").strip()
                pr = model.create_voice_clone_prompt(ref_audio=str(ref), ref_text=txt)
                pr.save(str(cache))
            PROMPTS[name] = pr
            log.info("voice ready: %s (%s)", name, d.name)
        except Exception as e:
            log.error("voice %s failed: %s\n%s", d.name, e, traceback.format_exc())
    STATE["voices"] = [[f"{n} (clone)", n] for n in PROMPTS]


def local_snapshot(repo):
    """Thư mục mô hình đã tải sẵn trong cache HF (để nạp không cần mạng), hoặc None."""
    if os.path.isdir(repo):
        return repo
    home = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
    base = Path(home) / "hub" / ("models--" + repo.replace("/", "--"))
    ref = base / "refs" / "main"
    cands = []
    if ref.exists():
        cands.append(base / "snapshots" / ref.read_text(encoding="utf-8").strip())
    if (base / "snapshots").is_dir():
        cands += sorted((base / "snapshots").iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
    for d in cands:
        if (d / "config.json").exists() and (d / "model.safetensors").exists():
            return str(d)
    return None


RETRY_SEC = int(os.environ.get("OMNI_RETRY_SEC", "30"))


def load_forever():
    """Nạp mô hình; hỏng (mất mạng...) thì thử lại sau RETRY_SEC giây, không bỏ cuộc."""
    n = 0
    while M is None:
        n += 1
        load()
        if M is not None:
            return
        STATE["status"] = "retrying"
        log.warning("load attempt %d failed - retry in %ds", n, RETRY_SEC)
        time.sleep(RETRY_SEC)


def load():
    global M
    t0 = time.time()
    try:
        import torch
        from omnivoice import OmniVoice
        torch.set_num_threads(CPU_THREADS)
        dev = "cuda:0" if torch.cuda.is_available() else "cpu"
        STATE["device"] = dev
        log.info("loading %s on %s ...", MODEL, dev)
        src = local_snapshot(MODEL)
        if src:
            os.environ["HF_HUB_OFFLINE"] = "1"
            log.info("using local snapshot %s (offline)", src)
        else:
            os.environ.pop("HF_HUB_OFFLINE", None)
            src = MODEL
            log.info("no local snapshot - downloading %s from the Hub", MODEL)
        M = OmniVoice.from_pretrained(src, device_map=dev, dtype=torch.float16 if dev.startswith("cuda") else torch.float32)
        load_voices(M)
        if PROMPTS:
            warm_pr = PROMPTS.get("Hải Đăng") or next(iter(PROMPTS.values()))
            M.generate(text="Xin chào.", voice_clone_prompt=warm_pr, num_step=8)
        STATE.update(status="ready", error=None, load_seconds=round(time.time() - t0, 1))
        log.info("ready in %.1fs, voices=%s", time.time() - t0, list(PROMPTS))
    except Exception as e:
        STATE.update(status="failed", error=str(e))
        log.error("load failed: %s\n%s", e, traceback.format_exc())


def chunks(text, limit=220):
    """Split into sentences; long sentences are split again at commas."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if s.strip()]
    out = []
    for s in sents:
        while len(s) > limit:
            cut = s.rfind(",", 0, limit)
            cut = cut if cut > limit * 0.4 else s.rfind(" ", 0, limit)
            if cut < 0:
                cut = limit - 1
            out.append(s[:cut + 1].strip())
            s = s[cut + 1:].strip()
        if s:
            out.append(s)
    return out


def _is_oom(e):
    return "out of memory" in str(e).lower() or type(e).__name__ == "OutOfMemoryError"


def _gen_batched(sents, pr, speed, num_step):
    """Sinh nhiều câu theo lô trên GPU. Câu xếp theo độ dài để lô ít đệm; tràn VRAM thì giảm lô và chạy lại."""
    import numpy as np
    order = sorted(range(len(sents)), key=lambda i: len(sents[i]))
    outs = [None] * len(sents)
    bs, i = BATCH, 0
    while i < len(order):
        idx, chars = [], 0
        while i + len(idx) < len(order) and len(idx) < bs:
            j = order[i + len(idx)]
            if idx and chars + len(sents[j]) > BATCH_CHARS:
                break
            idx.append(j)
            chars += len(sents[j])
        try:
            res = M.generate(text=[sents[j] for j in idx], voice_clone_prompt=pr, num_step=num_step, speed=speed)
        except Exception as e:  # noqa: BLE001
            if not _is_oom(e) or len(idx) == 1:
                raise
            import torch
            torch.cuda.empty_cache()
            bs = max(1, len(idx) // 2)
            log.warning("VRAM full at batch %d - retry with batch %d", len(idx), bs)
            continue
        for j, a in zip(idx, res):
            outs[j] = np.asarray(a, dtype=np.float32)
        i += len(idx)
    return outs


def synth_many(texts, voice, speed=1.0, num_step=STEPS):
    """Nhiều đoạn văn -> nhiều file âm thanh; mọi câu của mọi đoạn được gộp lô chung trên GPU."""
    import numpy as np
    pr = PROMPTS[voice]
    sents, owner = [], []
    for ti, t in enumerate(texts):
        for c in chunks(t):
            sents.append(c)
            owner.append(ti)
    audio = _gen_batched(sents, pr, speed, num_step) if sents else []
    per = [[] for _ in texts]
    for a, ti in zip(audio, owner):
        if per[ti]:
            per[ti].append(np.zeros(int(SR * 0.28), dtype=np.float32))
        per[ti].append(a)
    return [np.concatenate(p) if p else np.zeros(1, dtype=np.float32) for p in per]


def synth(text, voice, speed=1.0, num_step=STEPS):
    return synth_many([text], voice, speed, num_step)[0]


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, STATE)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/tts_batch":
            return self._tts_batch()
        if self.path != "/tts":
            return self._send(404, {"error": "not found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
            text, voice = req["text"], req.get("voice") or "Hải Đăng"
            out = Path(req["path"]).resolve()
            if out.parent != OUTPUT_DIR or out.suffix.lower() != ".wav":
                return self._send(400, {"error": "path must be a .wav directly inside omnivoice/outputs/"})
            if M is None:
                return self._send(503, {"error": f"model not ready ({STATE['status']}) {STATE.get('error') or ''}"})
            if voice not in PROMPTS:
                return self._send(400, {"error": f"unknown voice '{voice}'. Available: {list(PROMPTS)}"})
            import soundfile as sf
            with LOCK:
                t0 = time.time()
                audio = synth(text, voice, float(req.get("speed", 1.0)), int(req.get("num_step", STEPS)))
                sf.write(str(out), audio, SR, subtype="PCM_16")
                secs = time.time() - t0
            log.info("generated %s (%.1fs audio) in %.1fs", out.name, len(audio) / SR, secs)
            self._send(200, {"seconds": secs, "duration": len(audio) / SR, "sample_rate": SR})
        except Exception as e:
            log.error("tts failed: %s\n%s", e, traceback.format_exc())
            self._send(500, {"error": str(e)})

    def _tts_batch(self):
        try:
            import base64
            import io
            import soundfile as sf
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
            texts = [str(t) for t in (req.get("texts") or [])]
            voice = req.get("voice") or "Hải Đăng"
            if not texts:
                return self._send(400, {"error": "texts is empty"})
            if M is None:
                return self._send(503, {"error": f"model not ready ({STATE['status']}) {STATE.get('error') or ''}"})
            if voice not in PROMPTS:
                return self._send(400, {"error": f"unknown voice '{voice}'. Available: {list(PROMPTS)}"})
            with LOCK:
                t0 = time.time()
                audios = synth_many(texts, voice, float(req.get("speed", 1.0)), int(req.get("num_step", STEPS)))
                secs = time.time() - t0
            items = []
            for a in audios:
                buf = io.BytesIO()
                sf.write(buf, a, SR, format="WAV", subtype="PCM_16")
                items.append({"wav_b64": base64.b64encode(buf.getvalue()).decode("ascii"), "duration": len(a) / SR})
            tot = sum(it["duration"] for it in items)
            log.info("batch %d texts (%.1fs audio) in %.1fs", len(texts), tot, secs)
            self._send(200, {"items": items, "seconds": secs, "sample_rate": SR})
        except Exception as e:
            log.error("tts_batch failed: %s\n%s", e, traceback.format_exc())
            self._send(500, {"error": str(e)})


def _uncaught(t, v, tb):
    log.error("UNCAUGHT %s", "".join(traceback.format_exception(t, v, tb)))


sys.excepthook = _uncaught
threading.excepthook = lambda a: log.error(
    "UNCAUGHT in thread %s: %s",
    a.thread,
    "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)),
)


if __name__ == "__main__":
    atexit.register(lambda: log.info("process exiting normally"))
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("listening on 127.0.0.1:%s, python=%s", PORT, sys.executable)
    print(f"OmniVoice service on 127.0.0.1:{PORT} - loading {MODEL}...", flush=True)
    load_forever()
    print(f"OmniVoice: {STATE['status']} on {STATE['device']} in {STATE['load_seconds']}s, voices={list(PROMPTS)}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
