"""Bước 5: giọng đọc. Một lớp 'nhà cung cấp giọng' — thêm/bỏ nhà cung cấp chỉ sửa ở file này.

Nhà cung cấp có sẵn:
  omnivoice      OmniVoice clone giọng (dịch vụ GPU chạy ngầm 127.0.0.1:8766, bật bằng START-OMNIVOICE.bat).
                 Mọi đoạn được gửi theo lô — GPU sinh nhiều câu cùng lúc nên nhanh hơn đọc từng câu nhiều lần.
  vieneu         VieNeu-TTS — giọng tiếng Việt tự nhiên, chạy tại máy, miễn phí (cài thêm bằng nút trong tool)
  piper          chạy tại máy, miễn phí, offline
  local_command  gọi một lệnh TTS bất kỳ trên máy (VieNeu, XTTS, …) qua mẫu lệnh trong Cài đặt
  edge           giọng Microsoft Edge online (miễn phí, cần mạng)
  openai         OpenAI TTS (API key của khách)
  elevenlabs     ElevenLabs (API key của khách)
"""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures as cf
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import media, pronounce, settings
from .paths import IS_WINDOWS, ROOT
from .project import Cancelled, ProcessRunner, Project

_NOWIN = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}

PROVIDERS = ("omnivoice", "vieneu", "piper", "local_command", "edge", "openai", "elevenlabs")


def availability() -> dict[str, tuple[bool, str]]:
    """Nhà cung cấp nào đã sẵn sàng dùng: {tên: (dùng_được, ghi_chú)}."""
    cfg = settings.get("tts", {}) or {}
    pv = cfg.get("providers") or {}
    out: dict[str, tuple[bool, str]] = {}
    m = Path((pv.get("piper") or {}).get("model") or "")
    m = m if m.is_absolute() else ROOT / m
    out["piper"] = (m.exists(), "" if m.exists() else "chưa có model giọng")
    h = omnivoice_health()
    if h and h.get("status") == "ready":
        out["omnivoice"] = (True, f"GPU: {h.get('device')}")
    elif h:
        out["omnivoice"] = (False, f"dịch vụ đang nạp model ({h.get('status')}) — chờ ~30 giây")
    else:
        out["omnivoice"] = (False, "dịch vụ OmniVoice chưa chạy — bật START-OMNIVOICE.bat trong thư mục tool")
    import importlib.util
    has_vn = importlib.util.find_spec("vieneu") is not None
    out["vieneu"] = (has_vn, "" if has_vn else "chưa cài — Kiểm tra máy → Cài VieNeu")
    cmd = (pv.get("local_command") or {}).get("command") or []
    bad = (not cmd) or any("path/to" in str(a) or "path\\to" in str(a) for a in cmd)
    out["local_command"] = (not bad, "chưa cấu hình lệnh" if bad else "")
    out["edge"] = (True, "cần mạng")
    for k in ("openai", "elevenlabs"):
        ok = bool((pv.get(k) or {}).get("api_key"))
        out[k] = (ok, "" if ok else "chưa có API key")
    return out


def _to_wav(src: Path, dst: Path, rate: float = 1.0) -> None:
    """Chuẩn hoá mọi đầu ra về WAV 48 kHz stereo; áp tốc độ đọc nếu nhà cung cấp không hỗ trợ."""
    af = []
    if abs(rate - 1.0) > 0.01:
        r = max(0.5, min(2.0, rate))
        af = ["-af", f"atempo={r}"]
    subprocess.run([media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), *af,
                    "-ar", "48000", "-ac", "2", str(dst)], check=True, **_NOWIN)


def trim_silence(path: Path) -> None:
    """Cắt khoảng lặng thừa ở đầu/cuối câu (giọng TTS thường dư ~0.1s đầu + ~0.15s cuối).
    Không cắt thì giữa hai câu bị hẫng ~0.6s đúng lúc chuyển cảnh → nghe/nhìn như khựng."""
    tmp = path.with_name(path.stem + ".trim.wav")
    af = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.03,areverse,"
          "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse,afade=t=in:d=0.01")
    r = subprocess.run([media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(path), "-af", af,
                        "-ar", "48000", "-ac", "2", str(tmp)], **_NOWIN)
    if r.returncode == 0 and tmp.exists() and media.duration(tmp) > 0.3:
        tmp.replace(path)
    else:
        tmp.unlink(missing_ok=True)


def _http_post(url: str, headers: dict, body: dict, out: Path) -> None:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            out.write_bytes(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"Lỗi API TTS ({e.code}): {detail}") from None


def _clean_text(text: str) -> str:
    """Bỏ ký tự hay làm dịch vụ TTS lỗi: emoji, ký tự điều khiển, ngoặc kép lạ, khoảng trắng thừa."""
    import re
    import unicodedata
    t = unicodedata.normalize("NFC", text)
    t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'").replace("…", "...")
    t = "".join(ch for ch in t if unicodedata.category(ch)[0] != "C" and not (0x1F000 <= ord(ch) <= 0x1FAFF)
                and not (0x2600 <= ord(ch) <= 0x27BF))
    t = re.sub(r"[<>&*#_~`|\\]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _split_sentences(text: str, max_chars: int = 280) -> list[str]:
    import re
    sents = [x.strip() for x in re.split(r"(?<=[.!?;:])\s+", text) if x.strip()]
    out, cur = [], ""
    for x in sents:
        if cur and len(cur) + 1 + len(x) > max_chars:
            out.append(cur)
            cur = x
        else:
            cur = f"{cur} {x}".strip()
    if cur:
        out.append(cur)
    return [p for p in out if re.search(r"\w", p)] or [text]


class TTS:
    def __init__(self, provider: Optional[str] = None, voice: Optional[str] = None, rate: Optional[float] = None):
        cfg = settings.get("tts", {})
        self.provider = provider or cfg.get("provider") or "piper"
        if self.provider not in PROVIDERS:
            raise RuntimeError(f"Nhà cung cấp giọng không hợp lệ: {self.provider}")
        self.pcfg = (cfg.get("providers") or {}).get(self.provider, {})
        self.voice = voice or cfg.get("voice") or self.pcfg.get("voice") or ""
        self.rate = float(rate if rate is not None else cfg.get("rate", 1.0) or 1.0)

    def cache_key(self, text: str) -> str:
        h = hashlib.sha1(f"{self.provider}|{self.voice}|{self.rate}|{text}".encode("utf-8")).hexdigest()
        return h[:16]

    def synth(self, text: str, out_wav: Path, runner: Optional[ProcessRunner] = None) -> None:
        if self.provider == "omnivoice":
            h = ensure_omnivoice(15.0)
            if (not h or h.get("status") != "ready") and not _has_nvidia_or_omni_env():
                fb_prov, fb_voice = _fallback_tts_choice()
                if log_fn := getattr(self, "log", None):
                    log_fn(f"Máy không có GPU/OmniVoice — tự chuyển giọng đọc sang {fb_prov}.")
                TTS(fb_prov, fb_voice, self.rate).synth(text, out_wav, runner)
                return
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            raw = getattr(self, f"_{self.provider}")(_clean_text(text) if self.provider != "edge" else text, tmp, runner)
            # piper / local_command không có tham số tốc độ chuẩn → chỉnh bằng atempo
            rate = self.rate if self.provider in ("piper", "local_command", "elevenlabs", "vieneu") else 1.0
            # (omnivoice nhận tốc độ trực tiếp qua tham số speed → rate = 1.0 ở đây)
            _to_wav(raw, out_wav, rate)

    # ----- từng nhà cung cấp -----
    def _piper(self, text: str, tmp: Path, runner) -> Path:
        model = Path(self.pcfg.get("model") or "")
        if not model.is_absolute():
            model = ROOT / model
        if not model.exists():
            raise RuntimeError(f"Chưa có model giọng Piper: {model}. Chạy lại install.bat hoặc đổi nhà cung cấp giọng.")
        out = tmp / "out.wav"
        cmd = [sys.executable, "-m", "piper", "-m", str(model), "-f", str(out)]
        if self.voice and self.voice.isdigit():
            cmd += ["-s", self.voice]
        r = subprocess.run(cmd, input=text, text=True, encoding="utf-8", capture_output=True, **_NOWIN)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(f"Piper lỗi: {r.stderr[-400:]}")
        return out

    def _omnivoice(self, text: str, tmp: Path, runner) -> Path:
        out = tmp / "out.wav"
        out.write_bytes(omnivoice_batch([text], self.voice, self.rate)[0])
        return out

    def _vieneu(self, text: str, tmp: Path, runner) -> Path:
        out = tmp / "out.wav"
        vieneu_batch([{"text": text, "out": str(out)}], self.voice, tmp, runner, None)
        return out

    def _local_command(self, text: str, tmp: Path, runner) -> Path:
        tmpl = self.pcfg.get("command") or []
        if not tmpl or any("path/to" in str(a) or "path\\to" in str(a) for a in tmpl):
            raise RuntimeError("Chưa cấu hình lệnh TTS tại máy (Cài đặt → Giọng đọc → local_command).")
        tf = tmp / "text.txt"
        tf.write_text(text, encoding="utf-8")
        out = tmp / "out.wav"
        cmd = [a.replace("{text_file}", str(tf)).replace("{out_file}", str(out)).replace("{voice}", self.voice)
               .replace("{python}", sys.executable) for a in tmpl]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **_NOWIN)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(f"Lệnh TTS tại máy lỗi (mã {r.returncode}): {(r.stderr or r.stdout)[-400:]}")
        return out

    def _edge(self, text: str, tmp: Path, runner) -> Path:
        """Edge hay trả 'No audio was received' khi bị giới hạn tần suất → chia câu ngắn,
        nghỉ giữa các lần gọi, thử lại với thời gian chờ tăng dần."""
        import time
        import edge_tts
        pct = int(round((self.rate - 1.0) * 100))
        rate = f"{'+' if pct >= 0 else ''}{pct}%"
        voice = self.voice or "vi-VN-HoaiMyNeural"
        parts = _split_sentences(_clean_text(text), max_chars=280)
        files = []
        for i, part in enumerate(parts):
            out = tmp / f"p{i:03d}.mp3"
            waits = [0, 4, 10, 20, 40, 60]
            for k, w in enumerate(waits):
                if w:
                    if runner and runner.cancelled:
                        raise Cancelled()
                    time.sleep(w)
                try:
                    async def go():
                        await edge_tts.Communicate(part, voice, rate=rate).save(str(out))
                    asyncio.run(go())
                    if out.exists() and out.stat().st_size > 0:
                        break
                except Exception as e:  # noqa: BLE001
                    if k == len(waits) - 1:
                        raise RuntimeError(f"Edge TTS vẫn lỗi sau nhiều lần thử ({e}). Máy chủ Microsoft có thể đang "
                                           f"giới hạn — đợi vài phút rồi bấm Dựng lại (các đoạn đã xong được giữ lại), "
                                           f"hoặc chọn giọng khác.") from None
                    if self_log := getattr(self, "log", None):
                        self_log(f"  Edge chưa trả âm thanh, chờ {waits[k + 1]}s rồi thử lại…")
            if not out.exists() or out.stat().st_size == 0:
                raise RuntimeError("Edge TTS không trả âm thanh sau nhiều lần thử. Đợi vài phút rồi bấm Dựng lại, "
                                   "hoặc chọn giọng khác.")
            files.append(out)
            time.sleep(0.6)
        if len(files) == 1:
            return files[0]
        lst = tmp / "parts.txt"
        lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8")
        merged = tmp / "merged.mp3"
        subprocess.run([media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c", "copy", str(merged)], check=True, **_NOWIN)
        return merged

    def _openai(self, text: str, tmp: Path, runner) -> Path:
        key = self.pcfg.get("api_key")
        if not key:
            raise RuntimeError("Chưa nhập API key OpenAI (Cài đặt → Giọng đọc).")
        out = tmp / "out.mp3"
        _http_post("https://api.openai.com/v1/audio/speech", {"Authorization": f"Bearer {key}"},
                   {"model": self.pcfg.get("model") or "gpt-4o-mini-tts", "voice": self.voice or "alloy",
                    "input": text, "speed": max(0.25, min(4.0, self.rate)), "response_format": "mp3"}, out)
        return out

    def _elevenlabs(self, text: str, tmp: Path, runner) -> Path:
        key = self.pcfg.get("api_key")
        if not key or not self.voice:
            raise RuntimeError("ElevenLabs cần API key và giọng (tên hoặc voice ID) — Cài đặt → Giọng đọc.")
        out = tmp / "out.mp3"
        try:
            _http_post(f"https://api.elevenlabs.io/v1/text-to-speech/{_eleven_id(self.voice)}?output_format=mp3_44100_192",
                       {"xi-api-key": key},
                       {"text": text, "model_id": self.pcfg.get("model") or "eleven_multilingual_v2"}, out)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            if "payment" in body:
                raise RuntimeError("ElevenLabs từ chối: tài khoản chưa thanh toán hóa đơn. Thanh toán trên elevenlabs.io rồi chạy lại.") from None
            raise RuntimeError(f"ElevenLabs lỗi ({e.code}): {body[:300]}") from None
        return out


_EL_CACHE: dict = {}


def elevenlabs_voices(force: bool = False) -> list[dict]:
    """Danh sách giọng trong tài khoản ElevenLabs: [{id, name, lang, category, gender}] (nhớ tạm 10 phút)."""
    import time
    key = ((settings.get("tts.providers", {}) or {}).get("elevenlabs") or {}).get("api_key")
    if not key:
        return []
    if not force and _EL_CACHE.get("t", 0) > time.time() - 600 and _EL_CACHE.get("key") == key:
        return _EL_CACHE["v"]
    req = urllib.request.Request("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return _EL_CACHE.get("v", [])
    out = []
    for v in data.get("voices") or []:
        lb = v.get("labels") or {}
        out.append({"id": v["voice_id"], "name": (v.get("name") or "").strip(), "lang": lb.get("language", ""),
                    "category": v.get("category", ""), "gender": lb.get("gender", "")})
    out.sort(key=lambda x: (x["lang"] != "vi", x["category"] not in ("cloned", "generated"), x["name"].lower()))
    _EL_CACHE.update(t=time.time(), key=key, v=out)
    return out


def _eleven_id(voice: str) -> str:
    """Nhận voice ID hoặc tên giọng (vd 'Bao Ngoc') → voice ID."""
    v = (voice or "").strip()
    if re.fullmatch(r"[A-Za-z0-9]{20}", v):
        return v
    vs = elevenlabs_voices()
    low = v.lower()
    for test in (lambda n: n.lower() == low, lambda n: n.lower().startswith(low), lambda n: low in n.lower()):
        for x in vs:
            if test(x["name"]):
                return x["id"]
    raise RuntimeError(f"Không tìm thấy giọng ElevenLabs '{voice}'. Mở danh sách giọng để chọn lại.")


def _omni_cfg() -> dict:
    return ((settings.get("tts.providers", {}) or {}).get("omnivoice") or {})


def omnivoice_url() -> str:
    return (_omni_cfg().get("url") or "http://127.0.0.1:8766").rstrip("/")


def omnivoice_health(timeout: float = 2.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(omnivoice_url() + "/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def omnivoice_voices() -> list[str]:
    h = omnivoice_health() or {}
    return [v[-1] if isinstance(v, (list, tuple)) else str(v) for v in (h.get("voices") or [])]


def _has_nvidia_or_omni_env() -> bool:
    return (
        (ROOT / "tools" / "omni_venv").exists()
        or (ROOT / "omnivoice" / ".venv").exists()
        or shutil.which("nvidia-smi") is not None
    )


def _fallback_tts_choice() -> tuple[str, str]:
    pv = (settings.get("tts", {}) or {}).get("providers") or {}
    m = Path((pv.get("piper") or {}).get("model") or "models/piper/vi_VN-vais1000-medium.onnx")
    m = m if m.is_absolute() else ROOT / m
    if m.exists():
        return "piper", ""
    edge_voice = (pv.get("edge") or {}).get("voice") or "vi-VN-HoaiMyNeural"
    return "edge", edge_voice


def ensure_omnivoice(wait_seconds: float = 90.0) -> Optional[dict]:
    """Đảm bảo dịch vụ OmniVoice đang chạy và đã nạp xong model (tự bật START-OMNIVOICE.bat nếu đã cài)."""
    h = omnivoice_health()
    if h and h.get("status") == "ready":
        return h
    has_env = (
        (ROOT / "tools" / "omni_venv" / "Scripts" / "pythonw.exe").exists()
        or (ROOT / "tools" / "omni_venv" / "Scripts" / "python.exe").exists()
        or (ROOT / "omnivoice" / ".venv" / "Scripts" / "pythonw.exe").exists()
        or (ROOT / "omnivoice" / ".venv" / "Scripts" / "python.exe").exists()
    )
    if h is None and has_env and (ROOT / "START-OMNIVOICE.bat").exists():
        try:
            subprocess.run(["cmd", "/c", str(ROOT / "START-OMNIVOICE.bat")], cwd=str(ROOT), **_NOWIN)
        except Exception:  # noqa: BLE001
            pass
        h = omnivoice_health()
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while time.monotonic() < deadline:
        if h and h.get("status") == "ready":
            return h
        if h and h.get("status") == "failed":
            return h
        if h is None and not has_env:
            return None
        time.sleep(2.0)
        h = omnivoice_health()
    return h


def omnivoice_batch(texts: list[str], voice: str, speed: float = 1.0) -> list[bytes]:
    """Gửi nhiều đoạn một lần cho dịch vụ OmniVoice; nhận về WAV (24 kHz) của từng đoạn."""
    h = omnivoice_health()
    if not (h and h.get("status") == "ready"):
        h = ensure_omnivoice(90.0)
    cfg = _omni_cfg()
    body = {"texts": [_clean_text(t) for t in texts], "voice": voice or cfg.get("voice") or "Hải Đăng",
            "speed": max(0.5, min(2.0, float(speed or 1.0))), "num_step": int(cfg.get("num_step") or 32)}
    req = urllib.request.Request(omnivoice_url() + "/tts_batch", data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=max(300, 60 * len(texts))) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        if e.code == 404:
            raise RuntimeError("Dịch vụ OmniVoice đang chạy bản cũ, chưa có lệnh đọc nhiều đoạn (/tts_batch). "
                               "Đóng cửa sổ OmniVoice đang chạy, mở lại START-OMNIVOICE.bat trong thư mục tool, "
                               "chờ khoảng 30 giây rồi bấm Dựng lại.") from None
        raise RuntimeError(f"OmniVoice lỗi ({e.code}): {detail}") from None
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise RuntimeError("Không kết nối được dịch vụ OmniVoice (127.0.0.1:8766). Bật "
                           "START-OMNIVOICE.bat trong thư mục tool rồi bấm Dựng lại. "
                           f"({e})") from None
    return [base64.b64decode(it["wav_b64"]) for it in data["items"]]


def post_workers() -> int:
    """Số tiến trình ffmpeg chạy song song cho việc chuyển/cắt lặng từng đoạn giọng."""
    import os
    return max(1, min(12, (os.cpu_count() or 4) // 4))


def vieneu_workers() -> int:
    v = ((settings.get("tts.providers", {}) or {}).get("vieneu") or {}).get("workers") or 1
    try:
        return max(1, min(8, int(v)))
    except (TypeError, ValueError):
        return 1


def vieneu_mode() -> str:
    return ((settings.get("tts.providers", {}) or {}).get("vieneu") or {}).get("mode") or "v3turbo"


def vieneu_batch(items: list[dict], voice: str, workdir: Path, runner: Optional[ProcessRunner],
                 project: Optional[Project]) -> None:
    """Gọi worker VieNeu một lần cho nhiều đoạn (model chỉ nạp một lần)."""
    if not items:
        return
    bf = workdir / "vieneu_batch.json"
    bf.write_text(json.dumps({"voice": voice, "items": items}, ensure_ascii=False), encoding="utf-8")
    cmd = [sys.executable, "-m", "recap.vieneu_worker", "--batch", str(bf), "--mode", vieneu_mode()]
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(ROOT) + (__import__("os").pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    done = {"n": 0}

    warns: list[str] = []

    def on_line(line: str) -> None:
        if line.startswith("WARN "):
            warns.append(line[5:].strip())
            if project:
                project.log("  VieNeu: " + line[5:].strip())
        if line.startswith("OK ") and project:
            done["n"] += 1
            project.log(f"  VieNeu: {done['n']}/{len(items)}")

    r = runner or ProcessRunner(project)
    rc = r.run(cmd, cwd=workdir, env=env, timeout=6 * 3600, on_line=on_line)
    if warns and project is None and voice:
        # nghe thử: báo rõ là giọng không tồn tại thay vì lặng lẽ đọc bằng giọng mặc định
        raise RuntimeError(warns[0])
    missing = [it for it in items if not Path(it["out"]).exists()]
    if rc != 0 or missing:
        raise RuntimeError("VieNeu lỗi khi tạo giọng (xem log). Nếu vừa cài, lần đầu VieNeu cần tải model "
                           "qua mạng; kiểm tra tên giọng trong Cài đặt → Giọng đọc.")


def run(project: Project, runner: Optional[ProcessRunner] = None) -> list[dict]:
    """Tạo giọng đọc cho từng segment của script.json. Đoạn nào không đổi lời thì dùng lại file cũ."""
    from .script_schema import load
    sc = load(project.f("script.json"))
    opts = project.options
    tts = TTS(opts.get("tts_provider") or None, opts.get("tts_voice") or None,
              opts.get("tts_rate") if opts.get("tts_rate") not in (None, "") else None)
    if tts.provider == "omnivoice":
        h = ensure_omnivoice(90.0 if _has_nvidia_or_omni_env() else 15.0)
        if (not h or h.get("status") != "ready") and not _has_nvidia_or_omni_env():
            fb_prov, fb_voice = _fallback_tts_choice()
            project.log(f"Máy không có GPU/OmniVoice — tự chuyển giọng đọc sang {fb_prov}.")
            tts = TTS(fb_prov, fb_voice, tts.rate)
    ok, note = availability().get(tts.provider, (True, ""))
    if not ok:
        raise RuntimeError(f"Nhà cung cấp giọng '{tts.provider}' chưa dùng được ({note}). "
                           f"Chọn giọng khác ở khung 'Giọng đọc' trong trang kịch bản rồi bấm Dựng lại, "
                           f"hoặc cấu hình trong Cài đặt → Giọng đọc.")
    d = project.f("tts")
    d.mkdir(exist_ok=True)
    tts.log = project.log
    project.log(f"Giọng đọc: {tts.provider} / {tts.voice or '(mặc định)'} / tốc độ {tts.rate}")
    if tts.provider == "omnivoice":
        # Gửi mọi đoạn còn thiếu theo lô cho dịch vụ GPU; tốc độ đọc đi thẳng vào model (không atempo)
        raw_dir = d / "_omni"
        raw_dir.mkdir(exist_ok=True)
        todo = []
        for s in sc.segments:
            say = pronounce.spoken_text(s, sc.language)
            final = d / f"seg_{s.id:03d}_{tts.cache_key(say)}.wav"
            if not final.exists():
                todo.append((say, final))
        if todo:
            group = max(1, int(_omni_cfg().get("group") or 24))
            project.log(f"OmniVoice (GPU): tạo {len(todo)} đoạn, mỗi lượt {group} đoạn…")
            done = 0
            with cf.ThreadPoolExecutor(post_workers()) as ex:
                futs = []
                for gi in range(0, len(todo), group):
                    if runner and runner.cancelled:
                        raise Cancelled()
                    part = todo[gi:gi + group]
                    wavs = omnivoice_batch([t for t, _ in part], tts.voice, tts.rate)
                    for (_, final), wav in zip(part, wavs):
                        raw = raw_dir / f"{final.stem}.raw.wav"
                        raw.write_bytes(wav)
                        futs.append(ex.submit(lambda r=raw, f=final: (_to_wav(r, f, 1.0), r.unlink(missing_ok=True))))
                    done += len(part)
                    project.log(f"  OmniVoice: {done}/{len(todo)}")
                for f in futs:
                    f.result()
    if tts.provider == "vieneu":
        # Tạo trước mọi đoạn còn thiếu trong một lần chạy, rồi vòng lặp dưới chỉ việc đo độ dài
        raw_dir = d / "_vieneu"
        raw_dir.mkdir(exist_ok=True)
        todo, pairs = [], []
        for s in sc.segments:
            say = pronounce.spoken_text(s, sc.language)
            final = d / f"seg_{s.id:03d}_{tts.cache_key(say)}.wav"
            if not final.exists():
                raw = raw_dir / f"{final.stem}.raw.wav"
                todo.append({"text": _clean_text(say), "out": str(raw)})
                pairs.append((raw, final))
        if todo:
            n = min(vieneu_workers(), len(todo))
            project.log(f"VieNeu: tạo {len(todo)} đoạn bằng {n} tiến trình song song "
                        f"(lần đầu cần nạp model, có thể mất 1–2 phút)…")
            if n <= 1:
                vieneu_batch(todo, tts.voice, raw_dir, runner, project)
            else:
                parts = [todo[i::n] for i in range(n)]
                base = runner or ProcessRunner(project)
                with cf.ThreadPoolExecutor(n) as ex:
                    futs = []
                    for i, part in enumerate(parts):
                        wd = raw_dir / f"w{i}"
                        wd.mkdir(exist_ok=True)
                        futs.append(ex.submit(vieneu_batch, part, tts.voice, wd, base.child(), project))
                    for f in futs:
                        f.result()
            for raw, final in pairs:
                _to_wav(raw, final, tts.rate)
                raw.unlink(missing_ok=True)
    manifest = []
    trim = bool(settings.get("tts.trim_silence", True))
    trimmed_file = d / "trimmed.json"
    try:
        trimmed = set(json.loads(trimmed_file.read_text(encoding="utf-8"))) if trimmed_file.exists() else set()
    except Exception:  # noqa: BLE001
        trimmed = set()
    outs: list[tuple] = []
    for i, s in enumerate(sc.segments, 1):
        if runner and runner.cancelled:
            raise Cancelled()
        say = pronounce.spoken_text(s, sc.language)
        key = tts.cache_key(say)
        out = d / f"seg_{s.id:03d}_{key}.wav"
        if not out.exists():
            import time
            for attempt in range(3):
                try:
                    tts.synth(say, out, runner)
                    break
                except (RuntimeError, Cancelled):
                    raise
                except Exception as e:  # lỗi mạng tạm thời
                    if attempt == 2:
                        raise RuntimeError(f"TTS lỗi ở đoạn {s.id}: {e}. Các đoạn đã tạo được giữ lại — "
                                           f"bấm Dựng lại để làm tiếp.") from e
                    project.log(f"  TTS lỗi tạm thời ở đoạn {s.id}, chờ {10 * (attempt + 1)}s rồi thử lại… ({e})")
                    time.sleep(10 * (attempt + 1))
        outs.append((s, out))

    # Cắt lặng + đo độ dài: mỗi đoạn là vài lệnh ffmpeg nhỏ → chạy song song nhiều đoạn một lúc
    def _post(item):
        seg, f = item
        did = False
        if trim and f.name not in trimmed:
            trim_silence(f)
            did = True
        return seg, f, media.duration(f), did

    with cf.ThreadPoolExecutor(post_workers()) as ex:
        results = list(ex.map(_post, outs))
    n_all = len(results)
    for i, (s, out, dur, did) in enumerate(results, 1):
        if did:
            trimmed.add(out.name)
        manifest.append({"id": s.id, "file": f"tts/{out.name}", "duration": round(dur, 3)})
        project.log(f"  đoạn {i}/{n_all}: {dur:.1f}s")
    if trim:
        trimmed_file.write_text(json.dumps(sorted(trimmed)), encoding="utf-8")
    project.f("tts", "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    total = sum(m["duration"] for m in manifest)
    project.log(f"Xong giọng đọc: tổng {media.fmt_ts(total)}")
    return manifest
