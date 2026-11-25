"""Bước 6: dựng video từ script.json + giọng đọc. Lệnh ffmpeg chỉ nằm ở đây — agent không tự viết lệnh ffmpeg."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from . import media, settings
from .paths import IS_WINDOWS, ROOT
from .project import Cancelled, ProcessRunner, Project
from .script_schema import load
from .sync import _scenes, fit_clips, gap_seconds

TAIL = 0.2  # (cũ) khoảng nghỉ sau mỗi câu — giờ lấy từ render.gap_seconds
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")


def frame_size(aspect: str, res: int) -> tuple[int, int]:
    res = int(res)
    if aspect == "9:16":
        w, h = res, int(res * 16 / 9)
    elif aspect == "1:1":
        w, h = res, res
    else:
        w, h = int(res * 16 / 9), res
    return w - w % 2, h - h % 2


def fit_filter(inp: str, out: str, w: int, h: int, mode: str) -> str:
    if mode == "crop":
        return f"[{inp}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[{out}]"
    if mode == "blur":
        return (f"[{inp}]split=2[{out}bg][{out}fg];"
                f"[{out}bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=20:2[{out}bb];"
                f"[{out}fg]scale={w}:{h}:force_original_aspect_ratio=decrease[{out}fs];"
                f"[{out}bb][{out}fs]overlay=(W-w)/2:(H-h)/2,setsar=1[{out}]")
    return (f"[{inp}]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[{out}]")


def split_subtitles(text: str, max_words: int = 12) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[.!?…;:])\s+", text) if p.strip()]
    chunks: list[str] = []
    for p in parts:
        words = p.split()
        while len(words) > max_words:
            # cắt ở dấu phẩy gần nhất nếu có
            cut = max_words
            for k in range(max_words, max_words // 2, -1):
                if words[k - 1].endswith(","):
                    cut = k
                    break
            chunks.append(" ".join(words[:cut]))
            words = words[cut:]
        if words:
            chunks.append(" ".join(words))
    return chunks or [text]


def write_srt(chunks: list[str], speech_dur: float, offset: float = 0.0) -> list[tuple[float, float, str]]:
    total_chars = sum(len(c) for c in chunks) or 1
    t = 0.0
    cues = []
    for c in chunks:
        d = speech_dur * len(c) / total_chars
        cues.append((offset + t, offset + t + d, c))
        t += d
    return cues


def cues_to_srt(cues: list[tuple[float, float, str]]) -> str:
    out = []
    for i, (a, b, c) in enumerate(cues, 1):
        out.append(f"{i}\n{media.srt_ts(a)} --> {media.srt_ts(b)}\n{c}\n")
    return "\n".join(out)


# ---- Phụ đề kiểu CapCut: chữ đậm trắng, từ đang đọc tô vàng ----
# "caps"   = CHỮ IN HOA, đậm, từ đang đọc màu vàng (mẫu "THE QUICK")
# "italic" = chữ nghiêng, đậm, viền đen dày, từ đang đọc màu vàng (mẫu "The quick")
SUB_EFFECTS = ("caps", "italic")
SUB_HIGHLIGHT = "&H001AE8FF"  # vàng #FFE81A (ASS ghi màu theo thứ tự BGR)


def subtitle_effect(seed: str) -> str:
    """render.subtitle_effect: caps | italic | auto (mỗi video luân phiên một trong hai kiểu) | plain (kiểu cũ)."""
    v = str(settings.get("render.subtitle_effect", "auto") or "auto").lower()
    if v in SUB_EFFECTS or v == "plain":
        return v
    return SUB_EFFECTS[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(SUB_EFFECTS)]


FONT_EXT = (".ttf", ".otf", ".ttc")
_WEIGHT_WORDS = {"thin", "light", "regular", "medium", "semibold", "bold", "black", "heavy", "italic", "md", "bd", "rg"}


def _font_family(path: Path) -> Optional[str]:
    """Đọc tên họ font (name ID 1) trong file .ttf/.otf — đây là tên libass dùng để tìm font."""
    import struct
    try:
        data = path.read_bytes()
        if data[:4] == b"ttcf":
            return None
        num = struct.unpack(">H", data[4:6])[0]
        for i in range(num):
            tag, _, off, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
            if tag != b"name":
                continue
            _, count, str_off = struct.unpack(">HHH", data[off:off + 6])
            best = None
            for j in range(count):
                pid, eid, lid, nid, ln, so = struct.unpack(">HHHHHH", data[off + 6 + 12 * j:off + 18 + 12 * j])
                if nid != 1:
                    continue
                raw = data[off + str_off + so:off + str_off + so + ln]
                if pid == 3:
                    name = raw.decode("utf-16-be", "ignore")
                    if lid == 0x409:
                        return name
                    best = best or name
                elif pid == 1 and not best:
                    best = raw.decode("latin-1", "ignore")
            return best
    except Exception:  # noqa: BLE001
        return None
    return None


def _font_dirs() -> list[Path]:
    import os
    dirs = [ROOT / "assets" / "fonts"]
    if IS_WINDOWS:
        dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
        la = os.environ.get("LOCALAPPDATA")
        if la:
            dirs.append(Path(la) / "Microsoft" / "Windows" / "Fonts")
    return [d for d in dirs if d.is_dir()]


def _find_font_file(name: str) -> Optional[Path]:
    """Tìm file font theo tên (vd 'Proxima Nova' → ProximaNova-Bold.otf). Ưu tiên bản Bold/Medium."""
    tokens = [t for t in re.split(r"[\s_\-]+", name.lower()) if t and t not in _WEIGHT_WORDS]
    if not tokens:
        return None
    want_medium = "medium" in name.lower()
    pref = ("medium", "md", "bold", "bd") if want_medium else ("bold", "bd", "semibold", "black", "medium")
    hits = []
    for d in _font_dirs():
        for p in d.iterdir():
            if p.suffix.lower() not in FONT_EXT:
                continue
            stem = re.sub(r"[\s_\-]+", "", p.stem.lower())
            if all(t in stem for t in tokens) and "italic" not in stem:
                rank = next((i for i, k in enumerate(pref) if k in stem), len(pref))
                hits.append((rank, len(stem), p))
    return min(hits)[2] if hits else None


def resolve_subtitle_font(rdir: Path) -> tuple[str, bool]:
    """Chọn font phụ đề: thử render.subtitle_font rồi lần lượt render.subtitle_font_fallbacks.
    File font tìm thấy được chép vào render/fonts để libass chắc chắn dùng được. Trả về (tên họ font, in đậm?)."""
    names = [settings.get("render.subtitle_font", "Proxima Nova") or ""]
    fb = settings.get("render.subtitle_font_fallbacks", ["DFP King Gothic Medium", "Montserrat"]) or []
    names += [fb] if isinstance(fb, str) else list(fb)
    fdir = rdir / "fonts"
    for n in [x for x in names if x]:
        f = _find_font_file(n)
        if not f:
            continue
        fdir.mkdir(exist_ok=True)
        try:
            shutil.copy2(f, fdir / f.name)
        except OSError:
            pass
        fam = _font_family(f) or n
        return fam, "medium" not in n.lower()
    return "Segoe UI", True


def _ass_ts(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_escape(t: str) -> str:
    return t.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(cues: list[tuple[float, float, str]], w: int, h: int, font: str, effect: str, bold: bool = True) -> str:
    """Phụ đề ASS: cả câu chữ trắng, từ đang được đọc đổi sang vàng (thời điểm chia theo độ dài từ)."""
    vertical = h > w
    size = int(round((w * 0.075) if vertical else (h * 0.058)))
    margin_v = int(round(h * (0.24 if vertical else 0.075)))
    italic = -1 if effect == "italic" else 0
    outline = max(2, round(size * (0.11 if effect == "italic" else 0.08)))
    shadow = max(1, round(size * 0.05))
    side = int(w * 0.06)
    head = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {w}\nPlayResY: {h}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Sub,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,{-1 if bold else 0},{italic},0,0,100,100,0,0,1,"
        f"{outline},{shadow},2,{side},{side},{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    hl_on, hl_off = "{\\c" + SUB_HIGHLIGHT + "&}", "{\\c&H00FFFFFF&}"
    lines = []
    for a, b, text in cues:
        words = _ass_escape(text).split()
        if effect == "caps":
            words = [x.upper() for x in words]
        if not words:
            continue
        total = sum(len(x) + 1 for x in words)
        t = a
        for i in range(len(words)):
            end = b if i == len(words) - 1 else t + (b - a) * (len(words[i]) + 1) / total
            txt = " ".join(hl_on + x + hl_off if j == i else x for j, x in enumerate(words))
            lines.append(f"Dialogue: 0,{_ass_ts(t)},{_ass_ts(end)},Sub,,0,0,0,,{txt}")
            t = end
    return head + "\n".join(lines) + "\n"


def narration_lead() -> float:
    """Lời đọc bắt đầu sau mép cắt bao nhiêu giây (mặc định = khoảng nghỉ giữa câu, tức đưa khoảng nghỉ lên trước)."""
    v = settings.get("render.narration_lead")
    try:
        lead = gap_seconds() if v in (None, "") else float(v)
    except (TypeError, ValueError):
        lead = gap_seconds()
    return max(0.0, min(lead, gap_seconds()))


def _render_fps(setting, src) -> int:
    """'auto' = theo video gốc (24–60). Video gốc 60 fps mà dựng 30 fps thì chuyển động kém mượt hơn bản gốc."""
    if str(setting).lower() != "auto":
        try:
            return max(10, min(60, int(setting)))
        except (TypeError, ValueError):
            return 30
    try:
        from fractions import Fraction
        v = next(x for x in media.probe(src).get("streams", []) if x.get("codec_type") == "video")
        f = float(Fraction(v.get("avg_frame_rate") or "30/1"))
    except Exception:  # noqa: BLE001
        f = 30.0
    return int(round(min(60.0, max(24.0, f))))


def _source_fps(src) -> float:
    try:
        from fractions import Fraction
        v = next(x for x in media.probe(src).get("streams", []) if x.get("codec_type") == "video")
        return float(Fraction(v.get("avg_frame_rate") or "30/1"))
    except Exception:  # noqa: BLE001
        return 30.0


def interpolate_target(src) -> int:
    """Số khung/giây đích khi BẬT nội suy chuyển động (0 = tắt). Chỉ nội suy khi video gốc thưa khung hơn đích."""
    v = str(settings.get("render.interpolate_fps", 0) or 0).lower()
    if v in ("", "0", "off", "false", "no"):
        return 0
    try:
        target = max(30, min(120, int(float(v))))
    except ValueError:
        return 0
    return target if _source_fps(src) < target - 1 else 0


def interp_filter(fps: int) -> str:
    """Nội suy chuyển động (tạo khung mới ở giữa hai khung gốc). 'high' = bù chuyển động kỹ; 'blend' = trộn khung, nhanh."""
    mode = str(settings.get("render.interpolate_mode", "high") or "high").lower()
    if mode == "blend":
        return f"minterpolate=fps={fps}:mi_mode=blend"
    # scd=fdiff: gặp chuyển cảnh thì không trộn hai cảnh với nhau (tránh hình "ma")
    return f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1:scd=fdiff:scd_threshold=8"


def _tool_path(p: str) -> Path:
    """Đường dẫn tương đối (vd assets/bgm) tính từ thư mục tool."""
    q = Path(str(p).strip().strip('"'))
    return q if q.is_absolute() else ROOT / q


def logo_spec(w: int, h: int) -> Optional[tuple[Path, int, int, float]]:
    """(file logo, chiều rộng logo, lề, độ đậm) — logo đặt góc trái trên. None = không gắn logo."""
    f = settings.get("render.logo_file", "")
    if not f:
        return None
    path = _tool_path(f)
    if not path.is_file():
        return None
    try:
        op = float(settings.get("render.logo_opacity", 0.7))
    except (TypeError, ValueError):
        op = 0.7
    try:
        ratio = float(settings.get("render.logo_width", 0.1875))
    except (TypeError, ValueError):
        ratio = 0.1875
    short = min(w, h)
    # 16:9: rộng = 18,75% chiều ngang (360px ở 1920); khung dọc/vuông: theo cạnh ngắn để không quá to
    lw = int(w * ratio) if w > h else int(short * ratio * 16 / 9 * 0.6)
    lw = max(40, lw - lw % 2)
    margin = max(8, int(round(short * 30 / 1080)))
    return path, lw, margin, max(0.0, min(1.0, op))


def pick_bgm(folder: str, seed: str) -> Optional[Path]:
    if not folder:
        return None
    d = _tool_path(folder)
    if not d.is_dir():
        return None
    files = sorted(p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXT)
    if not files:
        return None
    idx = int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(files)
    return files[idx]


def run(project: Project, runner: Optional[ProcessRunner] = None) -> Path:
    runner = runner or ProcessRunner(project)
    sc = load(project.f("script.json"))
    manifest_path = project.f("tts", "manifest.json")
    if not manifest_path.exists():
        raise RuntimeError("Chưa có giọng đọc (tts/manifest.json). Chạy bước tts trước.")
    manifest = {m["id"]: m for m in json.loads(manifest_path.read_text(encoding="utf-8"))}
    src = project.source()
    st = project.state()
    opts = st.get("options", {})
    rcfg = settings.get("render", {})
    aspect = opts.get("aspect") or "16:9"
    res = int(opts.get("resolution") or rcfg.get("resolution", 1080))
    fps = _render_fps(rcfg.get("fps", 30), src)
    interp = interpolate_target(src)
    if interp:
        fps = interp
    # Khoảng nghỉ giữa hai câu đặt TRƯỚC lời đọc: cắt sang hình mới → ~0,2 s sau mới nghe lời tả nó.
    # Mắt kịp thấy hành động trước khi tai nghe — hết cảm giác "lời đi trước hình". Tổng độ dài không đổi.
    lead = narration_lead()
    w, h = frame_size(aspect, res)
    fit = opts.get("fit") or ("blur" if aspect != "16:9" else "pad")
    burn = opts.get("burn_subtitles")
    burn = rcfg.get("burn_subtitles", True) if burn is None else bool(burn)
    font = rcfg.get("subtitle_font", "Arial") or "Arial"
    font_bold = True
    effect = subtitle_effect(project.id)
    vertical = h > w
    logo = logo_spec(w, h)
    font_size = 11 if vertical else 16
    margin_v = 70 if vertical else 22
    src_audio = bool(st.get("source_has_audio", True))
    enc = media.video_encoder_args(rcfg.get("encoder", "auto"), int(rcfg.get("crf", 18)), rcfg.get("quality", "high"))

    rdir = project.f("render")
    if rdir.exists():
        for p in rdir.glob("seg_*"):
            p.unlink()
    rdir.mkdir(exist_ok=True)
    if burn and effect != "plain":
        font, font_bold = resolve_subtitle_font(rdir)
        project.log(f"Font phụ đề: {font}")
    project.f("output").mkdir(exist_ok=True)
    # nội suy chạy trên CPU, mỗi tiến trình gần như 1 nhân → chạy nhiều đoạn song song hơn
    par = media.parallel_jobs(rcfg.get("parallel"), *((6, 8) if interp else (4, 6)))
    if logo:
        project.log(f"Gắn logo góc trái trên: {logo[0].name} (rộng {logo[1]}px, đậm {logo[3]:.0%})")
    project.log(f"Dựng video {w}x{h} @ {fps}fps"
                + (f" (nội suy chuyển động từ {_source_fps(src):.0f}fps gốc — chậm hơn nhiều)" if interp else "")
                + f", khung: {fit}, phụ đề: {('có, kiểu ' + effect) if burn else 'không'}, encoder: {enc[1]} "
                f"({rcfg.get('quality', 'high')}), dựng {par} đoạn cùng lúc")

    scenes = _scenes(project)
    src_dur = float(st.get("source_duration") or media.duration(src))
    seg_files: list[str] = []
    jobs: list[tuple[int, list[str], str, float]] = []  # (id, lệnh ffmpeg, file ra, độ dài)
    all_cues: list[tuple[float, float, str]] = []
    timeline = 0.0
    for n, seg in enumerate(sc.segments, 1):
        if runner.cancelled:
            raise Cancelled()
        m = manifest.get(seg.id)
        if not m:
            raise RuntimeError(f"Thiếu giọng đọc cho đoạn {seg.id}. Chạy lại bước tts.")
        narr = project.f(m["file"])
        speech = float(m["duration"])
        # độ dài đoạn = lời + nghỉ, làm tròn đúng số khung hình để ghép không lệch/không lặp khung
        D = max(1, round((speech + gap_seconds()) * fps)) / fps
        use_orig = src_audio and seg.original_audio_volume > 0

        # Căn hình cho đúng bằng lời đọc: nới clip trong cảnh / cắt bớt / chỉnh tốc độ nhẹ
        fitted, k, frozen = fit_clips([(c.start, c.end) for c in seg.clips], D, scenes, src_dur)
        if frozen > 0.3:
            project.log(f"  đoạn {seg.id}: clip thiếu {frozen:.1f}s so với lời đọc — phải giữ khung cuối")
        cmd = [media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-nostats"]
        for a, b in fitted:
            cmd += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(src)]
        ni = len(fitted)
        cmd += ["-i", str(narr)]
        if logo:
            cmd += ["-i", str(logo[0])]

        fc = []
        concat_in = ""
        for i in range(ni):
            if interp:
                # thu về khung đích trước rồi mới nội suy (nhẹ hơn nhiều so với nội suy ở độ phân giải gốc)
                fc.append(f"[{i}:v]setpts=PTS-STARTPTS[r{i}]")
                fc.append(fit_filter(f"r{i}", f"q{i}", w, h, fit))
                fc.append(f"[q{i}]{interp_filter(fps)},fps={fps}[v{i}]")
            else:
                fc.append(f"[{i}:v]fps={fps},setpts=PTS-STARTPTS[r{i}]")
                fc.append(fit_filter(f"r{i}", f"v{i}", w, h, fit))
            concat_in += f"[v{i}]"
            if use_orig:
                cl = fitted[i][1] - fitted[i][0]
                fo = max(0.0, cl - 0.03)
                # vào/ra tiếng gốc thật nhẹ ở mỗi chỗ cắt để không bị "bụp"
                fc.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,"
                          f"afade=t=in:d=0.02,afade=t=out:st={fo:.3f}:d=0.03[oa{i}]")
                concat_in += f"[oa{i}]"
        if not (k > 0):  # phòng hờ: hệ số tốc độ không hợp lệ thì giữ nguyên tốc độ
            k = 1.0
        if use_orig:
            fc.append(f"{concat_in}concat=n={ni}:v=1:a=1[vc][ac]")
        else:
            fc.append(f"{concat_in}concat=n={ni}:v=1:a=0[vc]")
        speed = ""
        if abs(k - 1) > 0.01:
            # đổi tốc độ nhẹ: trộn khung (framerate) thay vì lặp/bỏ khung → không giật
            if interp:
                smooth = f"{interp_filter(fps)},fps={fps},"
            else:
                smooth = f"framerate=fps={fps}," if settings.get("render.smooth_speed", True) else f"fps={fps},"
            speed = f"setpts={k:.5f}*PTS,{smooth}"
        vchain = f"[vc]{speed}tpad=stop_mode=clone:stop_duration={D:.3f},trim=duration={D:.3f},setpts=PTS-STARTPTS"
        if burn and effect != "plain":
            ass_name = f"seg_{seg.id:03d}.ass"
            # dòng ngắn (≤6 từ) để hiệu ứng tô từng từ dễ theo dõi
            cues = write_srt(split_subtitles(seg.narration, 6), speech, lead)
            (rdir / ass_name).write_text(build_ass(cues, w, h, font, effect, font_bold), encoding="utf-8")
            vchain += f",subtitles={ass_name}:fontsdir=fonts"
        elif burn:
            srt_name = f"seg_{seg.id:03d}.srt"
            cues = write_srt(split_subtitles(seg.narration), speech, lead)
            (rdir / srt_name).write_text(cues_to_srt(cues), encoding="utf-8")
            style = (f"FontName={font},FontSize={font_size},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                     f"BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV={margin_v}")
            vchain += f",subtitles={srt_name}:charenc=UTF-8:force_style='{style}'"
        if logo:
            _, lw, lm, lop = logo
            fc.append(vchain + "[vs]")
            fc.append(f"[{ni + 1}:v]scale={lw}:-2,format=rgba,colorchannelmixer=aa={lop:.2f}[lg]")
            fc.append(f"[vs][lg]overlay={lm}:{lm}:format=auto,format=yuv420p[vo]")
        else:
            fc.append(vchain + "[vo]")
        delay = f"adelay={int(lead * 1000)}:all=1," if lead > 0 else ""
        fc.append(f"[{ni}:a]aresample=48000,aformat=channel_layouts=stereo,{delay}apad,atrim=duration={D:.3f}[na]")
        if use_orig:
            tempo = f"atempo={1 / k:.5f}," if abs(k - 1) > 0.01 else ""
            fc.append(f"[ac]{tempo}volume={seg.original_audio_volume:.3f},apad,atrim=duration={D:.3f}[ob]")
            fc.append("[na][ob]amix=inputs=2:duration=first:normalize=0[ao]")
        else:
            fc.append("[na]anull[ao]")

        # từng đoạn để tiếng ở dạng PCM (không nén) → ghép không bị hở/bụp tiếng ở chỗ nối, nén AAC một lần ở cuối
        out_name = f"seg_{seg.id:03d}.mkv"
        cmd += ["-filter_complex", ";".join(fc), "-map", "[vo]", "-map", "[ao]", "-t", f"{D:.6f}",
                "-r", str(fps), *enc, "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", out_name]
        jobs.append((seg.id, cmd, out_name, D))
        seg_files.append(out_name)
        for a, b, c in write_srt(split_subtitles(seg.narration), speech, timeline + lead):
            all_cues.append((a, b, c))
        timeline += D

    # Dựng các đoạn SONG SONG (mỗi đoạn độc lập, chỉ ghép ở cuối) — GPU/CPU không phải chờ nhau
    import concurrent.futures as cf
    import threading
    done = {"n": 0}
    lock = threading.Lock()
    failed: list[int] = []

    def _one(job):
        sid, cmd, out_name, D = job
        if runner.cancelled:
            raise Cancelled()
        rc = runner.child().run(cmd, cwd=rdir)
        ok = rc == 0 and (rdir / out_name).exists()
        with lock:
            if ok:
                done["n"] += 1
                project.log(f"  dựng đoạn {done['n']}/{len(jobs)} (đoạn {sid}, {D:.1f}s)")
            else:
                failed.append(sid)
        return ok

    with cf.ThreadPoolExecutor(max(1, min(par, len(jobs) or 1))) as ex:
        list(ex.map(_one, jobs))
    if runner.cancelled:
        raise Cancelled()
    if failed:
        # Lỗi khi chạy song song (vd card hết phiên NVENC) → làm lại từng đoạn một
        project.log(f"  {len(failed)} đoạn lỗi khi chạy song song — dựng lại lần lượt…")
        for job in [j for j in jobs if j[0] in set(failed)]:
            rc = runner.run(job[1], cwd=rdir)
            if rc != 0 or not (rdir / job[2]).exists():
                raise RuntimeError(f"ffmpeg lỗi khi dựng đoạn {job[0]} (xem log).")

    # Ghép các đoạn
    (rdir / "list.txt").write_text("".join(f"file '{f}'\n" for f in seg_files), encoding="utf-8")
    body = rdir / "body.mkv"
    rc = runner.run([media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                     "-i", "list.txt", "-c", "copy", "body.mkv"], cwd=rdir)
    if rc != 0:
        raise RuntimeError("ffmpeg lỗi khi ghép các đoạn.")

    final = project.f("output", "final.mp4")
    # job cũ có thể lưu bgm_folder rỗng → lấy theo Cài đặt hiện tại
    bgm_folder = opts.get("bgm_folder") or rcfg.get("bgm_folder", "")
    bgm = pick_bgm(bgm_folder or "", project.id) if opts.get("use_bgm", True) else None
    if bgm:
        vol = float(rcfg.get("bgm_volume", 0.18))
        fade_st = max(0.0, timeline - 3)
        project.log(f"Thêm nhạc nền: {bgm.name} (tự giảm âm khi có lời đọc)")
        fcb = (f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol:.3f},"
               f"afade=t=out:st={fade_st:.2f}:d=3[bg];"
               f"[0:a]asplit=2[n1][n2];"
               f"[bg][n1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=500[duck];"
               f"[n2][duck]amix=inputs=2:duration=first:normalize=0[a]")
        rc = runner.run([media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(body),
                         "-stream_loop", "-1", "-i", str(bgm), "-filter_complex", fcb,
                         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                         "-t", f"{timeline:.3f}", "-movflags", "+faststart", str(final)])
        if rc != 0:
            raise RuntimeError("ffmpeg lỗi khi trộn nhạc nền.")
    else:
        rc = runner.run([media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(body),
                         "-map", "0:v", "-map", "0:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                         "-movflags", "+faststart", str(final)])
        if rc != 0:
            raise RuntimeError("ffmpeg lỗi khi xuất video cuối.")
    project.f("output", "final.srt").write_text(cues_to_srt(all_cues), encoding="utf-8")
    project.log(f"XONG: output/final.mp4 — dài {media.fmt_ts(timeline)}")
    return final
