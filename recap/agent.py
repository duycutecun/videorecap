"""Lớp gọi agent (Codex CLI hoặc Antigravity CLI) — chỉ có đúng một chỗ này biết cách gọi agent.

Agent được giao TASK.md trong thư mục dự án và chỉ điều khiển quy trình qua bộ lệnh `recap`
(recap.cmd trên Windows / recap.sh trên Linux-macOS) — không tự viết lệnh ffmpeg.
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Optional

from . import media, settings
from .paths import IS_WINDOWS, PRESETS_DIR, ROOT, python_exe
from .project import ProcessRunner, Project

SHORT_PROMPT = ("Read the file TASK.md in the current directory and carry out that task exactly. "
                "Work only inside the current directory and use only the recap commands described there.")

LENGTH_PRESETS = {
    "shorts": 55,
    "short": 6 * 60,
    "medium": 12 * 60,
    "long": 30 * 60,
}


NARRATION_STYLES = {
    "story": "Natural storyteller: warm, lively, like a friend telling the story to the viewer.",
    "funny": "Funny and witty: playful comments and light jokes about what happens (never mean or vulgar).",
    "dramatic": "Dramatic: build suspense and tension, short punchy lines at the key moments.",
    "chill": "Calm and relaxing: gentle, soothing, unhurried, like a cozy narration.",
    "documentary": "Documentary: clear and serious, explain what is done and why, still in spoken language.",
}

STYLE_SECTION = """### Narration style - it must sound like a real person telling a story (client requirement)
Tone: {style_line}
Matching the picture does NOT mean describing the picture like a camera log. Tell a STORY over the clips:
- Write spoken, everyday language, as you would say it out loud to a viewer - not written/report style.
- Connect the sentences so the whole narration flows as one story: use linking words between segments
  (Vietnamese e.g. "Thế là", "Rồi", "Nhưng", "Hóa ra", "Chẳng mấy chốc", "Xong xuôi", "Cuối cùng"...).
  A sentence may continue the thought of the previous one.
- Explain the purpose and the result of actions (why he does it, what it becomes), react to it, show feeling
  (surprise, satisfaction, tension), and talk to the viewer now and then (e.g. "các bạn thấy không", "nghe thì dễ nhưng...").
  Comments and reactions are allowed as long as they do not claim something that is not true or not shown.
- Vary the sentence openings and lengths. Do NOT start many sentences with the same word or the character's
  pronoun (e.g. "Anh ..." again and again) - often drop the subject or start with the action, the time, or a reaction.
- Never talk about the camera or the shot ("nhìn từ bên trong", "từ góc này", "khung hình", "cảnh quay", "camera").
- Avoid repeating the same verbs and phrases; avoid stiff words like "tiến hành", "thực hiện".
Read the whole narration top to bottom before you finish: if it sounds like a list of actions, rewrite it.
{pron_rules}
`validate` prints a warning when many sentences start the same way or talk about the camera - fix those.
"""


PRON_RULES = """
#### Pronunciation - the narration is read by a VIETNAMESE text-to-speech voice
It reads English/foreign words and abbreviations badly. So:
- Prefer Vietnamese words: "máy quay" not "camera", "trò chơi" not "game", "đội" not "team", "trực tuyến" not "online".
- Write numbers, units and symbols the way they are spoken: "hai mét", "năm mươi phần trăm", "ba giờ chiều".
- When a foreign name/brand/term must stay (a person, a place, a product), keep it in `narration` (it is used for
  the subtitles) and add a `speak` field to that segment with the SAME sentence written the way a Vietnamese
  speaker says it, e.g. narration "Anh lái chiếc Ferrari mới tới." -> speak "Anh lái chiếc phe ra ri mới tới."
- `validate` warns ("Phát âm: ...") about segments with foreign words and no `speak` - fix every one.
"""


def write_wrapper(project: Project) -> str:
    """Tạo lệnh `recap` ngay trong thư mục dự án để agent gọi."""
    py = python_exe()
    if IS_WINDOWS:
        p = project.f("recap.cmd")
        p.write_text(f'@echo off\r\nset "PYTHONPATH={ROOT}"\r\nset "PYTHONIOENCODING=utf-8"\r\n'
                     f'"{py}" -m recap %*\r\n', encoding="utf-8")
        # Bản cho Git Bash (Claude Code dùng bash nếu máy có Git for Windows)
        sh = project.f("recap")
        sh.write_text(f'#!/bin/sh\nexport PYTHONPATH="{ROOT.as_posix()}"\nexport PYTHONIOENCODING=utf-8\n'
                      f'exec "{Path(py).as_posix()}" -m recap "$@"\n', encoding="utf-8", newline="\n")
        return "./recap.cmd"
    p = project.f("recap.sh")
    p.write_text(f'#!/bin/sh\nexport PYTHONPATH="{ROOT}"\nexec "{py}" -m recap "$@"\n', encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return "./recap.sh"


def is_anime(opts: dict) -> bool:
    """Ngách anime/donghua: bắt buộc bảng tên riêng theo nguyên tác (glossary.json) và xuất 2 file SRT."""
    return (opts.get("content_type") or "") == "anime"


ANIME_SECTION = """
## Anime: source fidelity (REQUIRED for this job) - two subtitle tracks
This recap produces TWO subtitle files from your script:
- DISPLAY SRT (`output/final.srt`, also burned on the picture) = the `narration` text. Names and terms are spelled
  exactly as in the original work.
- VOICE SRT (`output/final.voice.srt`) = what the text-to-speech voice actually reads. The tool builds it from
  `narration` by replacing every glossary name with its `read` form (or from `speak` if you wrote one).
So: write names CORRECTLY in `narration`; never write phonetic spellings there.

### Step 2b - Build `glossary.json` BEFORE writing script.json
List every proper noun and special term the narration will use: characters, clans/organisations, places, techniques,
weapons, titles, races, power-system words. UTF-8 JSON:
```json
{
  "source": "<original title> - <season/episode or arc>",
  "terms": [
    {"name": "Monkey D. Luffy", "read": "mân ki đi lu phi", "type": "character", "note": "thuyền trưởng băng Mũ Rơm", "avoid": ["Rufi", "Luffi"]},
    {"name": "Luffy", "read": "lu phi", "type": "character"},
    {"name": "Gomu Gomu no Mi", "read": "gô mư gô mư nô mi", "type": "item", "note": "trái Cao Su"},
    {"name": "Tiêu Viêm", "read": "", "type": "character", "avoid": ["Xiao Yan"]}
  ]
}
```
- `name`: the canonical spelling. Take it from the source itself first (on-screen text, credits, the subtitles/dialogue
  in transcript.txt). If the transcript only gives a phonetic guess of a name (speech recognition often does), work
  out the real name from the title/context; if you are able to search the web, confirm the official spelling there.
  * Japanese anime: official romanised name (Hepburn / official English release), e.g. "Naruto", "Sasuke", "Tanjiro".
    Do NOT turn Japanese names into Sino-Vietnamese.
  * Chinese donghua: the Sino-Vietnamese (Hán Việt) name Vietnamese viewers use, e.g. "Tiêu Viêm", "Đường Tam" -
    not pinyin.
  * Techniques / terms: keep the original name; the first time it appears, the narration may add its meaning in
    Vietnamese in a few words.
  * Add the short form used in narration as its own entry too (full name AND given name).
- `read`: how a Vietnamese speaker says it, in lowercase Vietnamese syllables ("sa su kê", "tan gi rô"). Required for
  every name that is not already plain Vietnamese; leave "" for Vietnamese / Hán Việt names.
- `avoid`: wrong or fan-made spellings you saw in the transcript - `validate` fails if one appears in the narration.
- Use ONE spelling per name in the whole script. If you are not sure who a character is, do not guess a name:
  describe them ("gã tóc bạc", "cô gái cầm kiếm") instead.
`validate` reports an ERROR for a missing glossary, a name without `read`, an `avoid` spelling, or a phonetic spelling
inside `narration`. You only need the `speak` field for the rare sentence where the automatic replacement is not
enough (numbers, symbols, an odd abbreviation): then `speak` is the SAME sentence written fully as spoken.

### Fidelity rules
- Every fact in the narration (who did what, who won, who died, what a power does, how characters are related) must
  be shown or said in THIS source. When unsure, leave it out.
- Keep ranks, titles and relationships exactly as the source gives them; keep the outcome and order of events.
- No details from other episodes, the manga/novel or the internet, and no spoilers beyond this video.
"""


def is_auto_length(opts: dict) -> bool:
    return ((opts.get("length") or "auto") == "auto" and not opts.get("target_minutes")
            and not opts.get("target_percent"))


def auto_bounds(source_seconds: float = 0) -> tuple[float, float]:
    """(tối thiểu, tối đa) phút khi AI tự quyết. auto_max_minutes = 0 → không giới hạn (tới hết video gốc)."""
    lo = float(settings.get("script.auto_min_minutes", 1) or 1)
    hi = float(settings.get("script.auto_max_minutes", 0) or 0)
    if hi <= 0:
        hi = source_seconds / 60 if source_seconds else 10_000
    if source_seconds:
        hi = min(hi, source_seconds / 60)
        lo = min(lo, round(source_seconds / 60 * 0.5, 2))  # video gốc rất ngắn: hạ mức tối thiểu
    return lo, max(lo, hi)


def target_seconds(opts: dict, source_seconds: float = 0) -> Optional[int]:
    """Độ dài mục tiêu (giây). None = AI tự quyết.
    target_percent: % độ dài video nguồn (thanh trượt 10–100%)."""
    if is_auto_length(opts):
        return None
    if opts.get("target_percent") and source_seconds:
        try:
            pct = max(10.0, min(100.0, float(opts["target_percent"])))
            return max(30, int(source_seconds * pct / 100))
        except (TypeError, ValueError):
            pass
    if opts.get("target_minutes"):
        try:
            return int(float(opts["target_minutes"]) * 60)
        except (TypeError, ValueError):
            pass
    return LENGTH_PRESETS.get(opts.get("length") or "medium", 12 * 60)


def build_task(project: Project, cmd: str) -> str:
    opts = project.options
    lang = opts.get("narration_language") or "vi"
    lang_name = {"vi": "Vietnamese", "en": "English"}.get(lang, lang)
    src_total = float(project.state().get("source_duration") or 0)
    tsec = target_seconds(opts, src_total)
    if tsec is not None and src_total and tsec > src_total:
        tsec = int(src_total)
    wpm = (settings.get("script.words_per_minute", {}) or {}).get(lang, 160)
    if tsec is None:
        lo, hi = auto_bounds(src_total)
        length_line = (f"- Target recap length: **YOU DECIDE** (between {lo:g} and {hi:.1f} minutes). "
                       f"See 'Choosing the length' below.")
        length_section = f"""## Choosing the length (you decide)
Pick the length that best fits THIS source - long enough to tell the story/ideas clearly, short enough to stay
engaging. Rough guide (adjust to how much really happens):
- Story content (film/anime/drama): about 25-40% of the source length for short sources (< 30 min),
  10-20% for long ones (> 1 h). Dense plot -> longer; slow or repetitive parts -> shorter.
- Show / game / vlog: only the highlights - usually 10-20% of the source.
- Lecture / podcast: enough to cover every key idea once - usually 10-15% of the source.
- Always stay between {lo:g} and {hi:.1f} minutes (the upper limit is the full source length). Narration speed is
  about {wpm} words per minute.
Write your decision into script.json as `"target_minutes": <number>` and `"length_reason": "<one short sentence in
{lang_name} explaining why>"`, then write narration that matches that length (+/-15%).
"""
    else:
        words = int(tsec / 60 * wpm)
        length_line = f"- Target recap length: **about {tsec // 60} min {tsec % 60} s** ≈ **{words} words** of narration in total"
        length_section = ""
    max_clip = float(settings.get("render.max_clip_seconds", 8.0))
    mode = opts.get("mode") or "review"
    preset_name = opts.get("content_type") or "film"
    preset_file = PRESETS_DIR / f"{preset_name}.md"
    preset = preset_file.read_text(encoding="utf-8") if preset_file.exists() else ""
    extra = (opts.get("instructions") or "").strip()
    style_key = opts.get("narration_style") or settings.get("script.narration_style", "story") or "story"
    style_line = NARRATION_STYLES.get(style_key, NARRATION_STYLES["story"])
    style_section = STYLE_SECTION.replace("{style_line}", style_line).replace(
        "{pron_rules}", PRON_RULES if (opts.get("narration_language") or "vi") == "vi" else "")
    src_dur = float(project.state().get("source_duration") or 0)
    anime_section = ANIME_SECTION if is_anime(opts) else ""

    finish = (f"""## Step 4 — Produce the video (AUTO mode)
Run `{cmd} render` (the voice already exists from sync). Confirm that `output/final.mp4` exists. If a command fails, read the
error, fix script.json if the error is about the script, and retry. Do not edit any other file.
""" if mode == "auto" else """## Step 4 — Stop (REVIEW mode)
Do NOT run render. A human will review and edit script.json in the UI before rendering.
""")

    if project.f("context.md").exists():
        step1 = ("Preparation is ALREADY DONE by the tool - do not run prepare again. You have:\n"
                 "- `context.md`: every scene with its time range in seconds, its frame images, and the dialogue spoken in it\n"
                 "- `transcript.txt`: the full timed dialogue\n"
                 "- `frames/*.jpg`: frame images (open them to understand what is on screen)")
    else:
        step1 = (f"Run `{cmd} prepare` (it can take a while for long videos). Afterwards you will have:\n"
                 "- `context.md`: every scene with its time range in seconds, its frame images, and the dialogue spoken in it\n"
                 "- `transcript.txt`: the full timed dialogue\n"
                 "- `frames/*.jpg`: frame images (open them to understand what is on screen)")
    one = bool(settings.get("script.one_sentence_per_segment", True))
    seg_rule = ("Each segment = EXACTLY ONE sentence (short, one idea) - split longer ideas into several segments,"
                " each with its own clips." if one else "Each segment = ONE idea, 1-2 sentences.")
    seg_size = ("ONE sentence of narration (~10-25 words)" if one else "1-2 sentences of narration (~15-40 words)")
    check_step = ("""
### Step 3c - Look at what will be on screen (visual check)
Run `""" + cmd + """ check`. It writes `check.md` and one image per segment in `check/` (frames from the start,
middle and end of every clip, labelled with the source second). It prints which segments are NEW or CHANGED since
your previous check: open ONLY those images (the first time that is every segment) and compare each with the
narration of that segment. Segments marked UNCHANGED have the same sentence and the same frames - keep your note. If the picture does not show what the sentence says (wrong person, wrong action, wrong place,
action not started yet / already over), move the clip to the right seconds - use the overview (or that moment's detail sheet) to find them - or, if no
footage shows it, rewrite the sentence to describe what IS shown. Then run `""" + cmd + """ sync` and
`""" + cmd + """ check` again.

After the LAST check, write `check_notes.json` (UTF-8) with one entry for EVERY segment, describing what you actually
see in its check image (in """ + lang_name + """), e.g.:
```json
[ {"id": 1, "on_screen": "Người đàn ông đeo ba lô đi vào rừng, dừng trước một thân cây đổ lớn", "matches": true} ]
```
`matches` must be true for every segment before you finish (fix the clip or the sentence until it is). The tool checks
that this file exists, covers every segment and is newer than script.json - a human will read your `on_screen` notes.
""" if settings.get("script.visual_check", True) else "")
    polish = ""
    if opts.get("rewrite") == "polish" and project.f("script.json").exists():
        note = (opts.get("polish_note") or "").strip()
        polish = ("""## THIS RUN: rewrite the narration only (script.json already exists)
`script.json` already has good segments and clips that match the picture. Your job this time is to make the
NARRATION sound natural - follow "Narration style" below. Keep every segment and its clips; rewrite only the
`narration` text of each segment so the whole thing flows like one story told by a person, while each sentence
still matches its own clips (use your notes and the check/ images; open sheets only if unsure). Only change a clip when the new
sentence really needs it. Do NOT run prepare. Then run validate, sync and check (Steps 3 to 3c) as usual.
""" + (f"Extra request for this rewrite: {note}\n" if note else "") + "\n")
    return f"""# TASK: write a recap video script

{polish}
You are the director of a recap video (the style of popular YouTube "anime/movie recap" channels:
an engaging narrator retells and comments on the story while short clips from the source play).

- Source video: `{project.source().name if project.source() else 'source.mp4'}` (duration {src_dur:.1f}s = {media.fmt_ts(src_dur)})
- Narration language: **{lang_name}**
{length_line}
- Aspect ratio of the final video: {opts.get('aspect') or '16:9'}
- Mode: **{mode.upper()}**

## Rules (important)
1. Work ONLY inside the current directory. Do not read or write files outside it.
2. Control the pipeline ONLY through the commands below. Never call ffmpeg, yt-dlp, whisper or python directly,
   never install packages, never delete the source video.
3. The narration must be your own words: retell, explain and comment. Do not copy the original dialogue
   line by line. Do not invent events that are not in the source.
4. Every clip must be short: at most {max_clip:g} seconds each. Use several clips per segment instead of one long clip.
5. Keep character names consistent. Open with a strong hook in segment 1 and finish with a clear ending.
6. All files are UTF-8 (Vietnamese text). On Windows PowerShell read them with
   `Get-Content -Raw -Encoding UTF8 <file>` and write script.json as UTF-8
   (e.g. `Set-Content -Encoding UTF8` or your file-editing tool). Never write UTF-16.
7. Narration and picture must match exactly - see Step 3 and Step 3b.
8. Run every command in the FOREGROUND and wait for it to finish (prepare can take 10-30 minutes on a slow machine -
   that is normal, keep waiting). Never start a command in the background. Do NOT end your turn until script.json
   exists, `validate` prints OK, `sync` prints SYNC OK and the visual check passes - there is nobody to answer you
   in between.

## Commands available (run them in this directory)
(On Windows: in PowerShell/cmd use `./recap.cmd <command>`; in a bash shell use `./recap <command>`.)
- `{cmd} prepare`   - does transcribe + scenes + frames + context in one go (skips parts already done)
- `{cmd} transcribe` / `{cmd} scenes` / `{cmd} frames` / `{cmd} context` - the same steps one by one
- `{cmd} validate`  - checks script.json and prints errors / warnings
- `{cmd} check`     - makes one image per segment showing the frames that will be on screen, for visual checking
- `{cmd} sync`      - generates the narration voice and checks that every segment's clips match the real
                      narration duration; prints SYNC OK or what to fix
- `{cmd} tts`       - generates the narration audio
- `{cmd} render`    - renders output/final.mp4
- `{cmd} info`      - shows the project status

## Step 1 - Prepare
{step1}
## Step 2 - Understand (look at the pictures)
Read `context.md` fully. Then look at the pictures of the whole video, in order: the OVERVIEW sheets in `overview/`
if they exist (otherwise the sheets in `sheets/`, or the frame images if there are no sheets). Note at which SECONDS
each important action visibly happens (who, what, where). Dialogue alone is not enough: the narration will be judged
against the picture.

### Save your budget (images are expensive)
- Every image you open stays in your context for the rest of the task, so open each image ONCE and write down what
  you learned (e.g. in notes.md) instead of opening it again.
- Never open all the detail sheets in `sheets/`. Open a detail sheet only for a clip whose exact start/end second
  you cannot tell from the overview - usually you do not need it, because the visual check (Step 3c) shows you the
  exact frames of every clip anyway.
- In Step 3c open only the check images that `check` reports as new or changed.

## Step 3 - Write `script.json` and validate it
Write `script.json` with exactly this structure (UTF-8 JSON):

```json
{{
  "title": "short catchy title in {lang_name}",
  "language": "{lang}",{chr(10) + '  "target_minutes": 8,' + chr(10) + '  "length_reason": "...",' if tsec is None else ''}
  "segments": [
    {{
      "id": 1,
      "narration": "Narration text for this segment ({lang_name}).",
      "clips": [ {{"start": 12.5, "end": 17.0}}, {{"start": 40.0, "end": 43.5}} ],
      "original_audio_volume": 0.0
    }}
  ]
}}
```
- `start`/`end` are seconds in the source video (use the scene ranges from context.md).
- Each segment: {seg_size} and clips whose total length roughly matches
  how long the narration takes to read (~{wpm} words per minute). If clips are a bit short, the tool stretches them
  inside the same scene; if longer, they are trimmed - but aim for a close match yourself.
- `speak` (optional): how the voice should read the sentence when it has foreign words (see Pronunciation).
  `narration` becomes the DISPLAY subtitles; `speak` (or narration + pronunciation tables) becomes the VOICE subtitles.
- `original_audio_volume`: 0 normally; 0.1-0.3 only when the original sound adds impact (a scream, a key line).
- Segments are in story order; keep the clips in each segment in chronological order.

### Narration and picture MUST match exactly (the client's top requirement) - work PICTURE FIRST
1. Outline the story beats you want to tell.
2. For each beat, FIRST choose the clip: the seconds where that thing is visibly happening on screen (read the
   yellow second labels on the overview sheets; open the detail sheet of that moment only if you need the exact second). Start the clip right where that shot/action begins (on a scene cut if there is one) - NOT earlier: a
   clip that begins with the tail of the previous shot makes the narration arrive before the action. The tool snaps
   clip edges to nearby scene cuts and leaves a short pause before each sentence.
3. THEN write the sentence for that clip, describing what is actually visible in it (who / what / where). You may add
   a short comment or joke, but never state something that is not visible in that clip or not true in the source.
4. {seg_rule} Never use unrelated "filler" footage.
5. The clip length must equal how long the sentence takes to read (~{wpm} words per minute): a long sentence needs a
   long clip of the same action, a short clip needs a short sentence. The whole described action must fit INSIDE the
   clip - if it takes longer than the sentence, describe only the part that is shown.

Then run `{cmd} validate`. Fix every ERROR and re-run until it prints `OK`. Look at the warnings and fix them if reasonable
(especially total length).

### Step 3b - Check timing with the real voice
Run `{cmd} sync`. It creates the narration audio and compares, for every segment, the real narration duration with
the total clip length. For every segment marked TOO SHORT / TOO LONG, change its clips (TOO LONG: move the END
earlier, keep the START just before the action; TOO SHORT: extend over more seconds of the SAME action - never filler)
and run `{cmd} sync` again until it prints `SYNC OK`.
Re-running sync is cheap: unchanged narration is not regenerated. Do not change narration text just to fix timing
unless there is no matching footage.
{style_section}
{anime_section}
{check_step}
{finish}
{length_section}
## Content type guidance
{preset}
{('## Extra instructions from the user' + chr(10) + extra) if extra else ''}
When you are completely finished, print the single line: TASK_COMPLETE
"""


def _resolve_cmd(tmpl: list[str], prompt: str, extra: Optional[list[str]] = None) -> list[str]:
    if not tmpl:
        raise RuntimeError("Chưa cấu hình lệnh gọi agent (Cài đặt → Agent).")
    tmpl = list(tmpl)
    if extra:
        # chèn tham số model ngay sau tên chương trình (và sau lệnh con như `exec` nếu có),
        # để không tách một cờ khỏi giá trị của nó (vd `-p {prompt}`)
        idx = 1
        if len(tmpl) > 1 and not tmpl[1].startswith("-") and "{prompt}" not in tmpl[1]:
            idx = 2
        tmpl[idx:idx] = extra
    cmd = [a.replace("{prompt}", prompt) for a in tmpl]
    exe = shutil.which(cmd[0])
    if not exe:
        raise RuntimeError(f"Không tìm thấy '{cmd[0]}' trên máy. Hãy cài và đăng nhập {cmd[0]} trước "
                           f"(xem README, mục Cài agent).")
    cmd[0] = exe
    return cmd


def run_agent(project: Project, runner: ProcessRunner, prompt: Optional[str] = None,
              provider: Optional[str] = None) -> int:
    cfg = settings.get("agent", {})
    # Phiên agent mới chưa nhìn thấy ảnh đối chiếu nào → lần check đầu của phiên phải cho xem lại tất cả
    try:
        project.f("check_state.json").unlink(missing_ok=True)
    except OSError:
        pass
    provider = provider or project.options.get("agent") or cfg.get("provider") or "codex"
    tmpl = (cfg.get("commands") or {}).get(provider)
    opts = project.options
    model = (opts.get("agent_model") or (cfg.get("models") or {}).get(provider) or "").strip()
    extra: list[str] = []
    if model:
        flags = (cfg.get("model_flags") or {}).get(provider) or []
        extra += [f.replace("{model}", model) for f in flags]
    effort = ((opts.get("agent_reasoning") or cfg.get("codex_reasoning") or "").strip()
              if provider == "codex" else "")
    if provider == "codex" and effort:
        extra += ["-c", f"model_reasoning_effort={effort}"]
    if provider == "claude":
        ceff = (opts.get("agent_reasoning") or cfg.get("claude_effort") or "").strip()
        if ceff:
            extra += ["--effort", ceff]
    tier = (opts.get("agent_service_tier") or cfg.get("codex_service_tier") or "").strip()
    if provider == "codex" and tier:
        extra += ["-c", f"service_tier={tier}"]
    cmd = _resolve_cmd(tmpl or [], prompt or SHORT_PROMPT, extra)
    timeout = float(cfg.get("timeout_minutes") or 0) * 60  # 0 = không giới hạn
    env = os.environ.copy()
    env["PATH"] = str(project.path) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(ROOT)
    env["RECAP_PROJECT"] = str(project.path)
    project.log(f"Gọi agent: {provider}{' / model ' + model if model else ' / model mặc định'}"
                f"{' / suy luận ' + effort if provider == 'codex' and effort else ''}"
                f"{' / tốc độ ' + tier if provider == 'codex' and tier else ''} ({'giới hạn ' + format(timeout/60, '.0f') + ' phút' if timeout > 0 else 'không giới hạn thời gian'})")
    project.log("---------- AGENT OUTPUT BẮT ĐẦU ----------")
    with open(project.f("log.txt"), "a", encoding="utf-8") as fh:
        fh.write("$ " + " ".join(cmd) + "\n")
        log_pos = fh.tell()
    use_pty = (cfg.get("use_pty") or {}).get(provider, False)
    if use_pty:
        project.log("(chạy agent trong terminal ảo)")
        rc = runner.run_pty(cmd, cwd=project.path, timeout=timeout, env=env)
    else:
        rc = runner.run(cmd, cwd=project.path, timeout=timeout, env=env)
    project.log(f"---------- AGENT OUTPUT KẾT THÚC (mã {rc}) ----------")
    if rc != 0:
        limit = _limit_message(project, log_pos)
        if limit:
            # Hết hạn mức thì gọi lại cũng bị từ chối ngay → dừng hẳn, báo rõ cho người dùng
            raise AgentLimitError(f"Agent {provider} đã hết hạn mức sử dụng: \"{limit}\". "
                                  "Đợi tới giờ được mở lại (hoặc chọn agent khác) rồi bấm 'Chạy lại'.")
    return rc


class AgentLimitError(RuntimeError):
    """Agent (Claude/Codex) báo hết hạn mức hoặc hết lượt dùng."""


_LIMIT_PATTERNS = ("hit your", "usage limit", "rate limit", "rate_limit", "limit reached", "limit · resets",
                   "resets at", "quota", "insufficient_quota", "credit balance", "out of credits",
                   "too many requests")


def _limit_message(project: Project, pos: int) -> str:
    """Tìm dòng báo hết hạn mức trong phần log agent vừa in ra. Trả về dòng đó (rỗng nếu không có)."""
    try:
        with open(project.f("log.txt"), "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(pos)
            text = fh.read()
    except OSError:
        return ""
    for line in text.splitlines():
        low = line.lower()
        if "----------" in line:
            continue
        if any(k in low for k in _LIMIT_PATTERNS):
            return line.strip()[:200]
    return ""


def list_models(provider: str) -> list[str]:
    """Hỏi agent danh sách model (nếu agent có lệnh liệt kê). Trả về [] nếu không lấy được."""
    import re
    import subprocess
    tmpl = (settings.get("agent.list_models", {}) or {}).get(provider)
    if not tmpl:
        return []
    exe = shutil.which(tmpl[0])
    if not exe:
        return []
    try:
        r = subprocess.run([exe, *tmpl[1:]], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=60, stdin=subprocess.DEVNULL,
                           **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {}))
    except Exception:
        return []
    out = []
    for line in (r.stdout or "").splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", line).strip(" *-•│|\t")
        tok = line.split()[0] if line.split() else ""
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,}", tok) and tok.lower() not in ("model", "models", "name", "available"):
            out.append(tok)
    return list(dict.fromkeys(out))
