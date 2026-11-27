"""Điều phối một job: lấy nguồn → giao agent → kiểm tra kịch bản → (duyệt) → giọng đọc → dựng."""
from __future__ import annotations

import traceback

import json

from . import agent, ingest, render, scenes, settings, transcribe, tts
from .project import Cancelled, ProcessRunner, Project
from .script_schema import validate_file


def prepare(project: Project, runner: ProcessRunner | None = None) -> None:
    runner = runner or ProcessRunner(project)
    if not project.state().get("source_duration"):
        ingest.check_source(project)
    transcribe.run(project)
    sc = scenes.detect(project, runner)
    if settings.get("frames.use_sheets", True):
        from . import sheets
        sheets.make(project, runner)
    elif not project.f("context.md").exists() or not any(s.get("frames") for s in sc):
        sc = scenes.extract_frames(project, sc, runner)
    scenes.build_context(project, sc)


def check_notes_problems(project: Project) -> list[str]:
    """Bắt agent ghi lại thứ nó THẤY trên ảnh đối chiếu của từng đoạn (chống 'soát cho có')."""
    if not settings.get("script.visual_check", True):
        return []
    from .media import read_text_any
    from .script_schema import load
    f = project.f("check_notes.json")
    if not f.exists():
        return ["thiếu check_notes.json (bước 3c: chạy check, xem từng ảnh, ghi lại thứ thấy trên hình)"]
    try:
        notes = json.loads(read_text_any(f))
        ids = {int(n["id"]): n for n in notes}
    except Exception as e:  # noqa: BLE001
        return [f"check_notes.json không đọc được: {e}"]
    sc = load(project.f("script.json"))
    probs = []
    missing = [s.id for s in sc.segments if s.id not in ids or not str(ids[s.id].get("on_screen", "")).strip()]
    if missing:
        probs.append(f"check_notes.json thiếu mô tả hình cho đoạn {missing[:10]}")
    bad = [i for i, n in ids.items() if n.get("matches") is not True]
    if bad:
        probs.append(f"các đoạn {bad[:10]} vẫn ghi matches=false — sửa clip hoặc câu cho khớp")
    if f.stat().st_mtime + 1 < project.f("script.json").stat().st_mtime:
        probs.append("script.json đã sửa sau lần soát cuối — chạy lại sync, check và cập nhật check_notes.json")
    return probs


def validate(project: Project) -> tuple[list[str], list[str]]:
    st = project.state()
    opts = st.get("options", {})
    lang = opts.get("narration_language") or "vi"
    wpm = (settings.get("script.words_per_minute", {}) or {}).get(lang, 160)
    src = float(st.get("source_duration") or 0)
    tsec = agent.target_seconds(opts, src)
    if tsec is not None and src and tsec > src:
        tsec = int(src)
    errs, warns, _ = validate_file(project.f("script.json"), src,
                                   float(settings.get("render.max_clip_seconds", 8.0)),
                                   tsec, wpm,
                                   agent.auto_bounds(src) if agent.is_auto_length(opts) else None,
                                   bool(settings.get("script.one_sentence_per_segment", True)),
                                   anime=agent.is_anime(opts))
    return errs, warns


def run_job(project: Project, runner: ProcessRunner, file_path=None) -> None:
    """Chạy job từ đầu đến khi xong (auto) hoặc đến bước chờ duyệt (review)."""
    try:
        opts = project.options
        project.update(status="running", stage="Lấy video nguồn", error=None)
        if not project.source():
            if opts.get("url"):
                ingest.download(project, opts["url"], runner)
            else:
                raise RuntimeError("Job không có link và không có file nguồn.")
        ingest.check_source(project)

        if settings.get("agent.tool_prepares", True) and not project.f("context.md").exists():
            # Bước nặng (nhận dạng lời thoại, cắt cảnh, lấy ảnh) do tool tự làm trước:
            # agent hay tự dừng khi một lệnh chạy quá lâu (đã gặp với Codex trên máy không có GPU).
            project.update(stage="Tool đang chuẩn bị dữ liệu (lời thoại, cảnh, khung hình)")
            prepare(project, runner)

        cmd = agent.write_wrapper(project)
        from .media import write_text_bom
        write_text_bom(project.f("TASK.md"), agent.build_task(project, cmd))

        retries = int(settings.get("agent.max_retries", 1))
        attempt = 0
        prompt = None
        while True:
            project.update(stage="Agent đang phân tích và viết kịch bản" + (f" (lần {attempt + 1})" if attempt else ""))
            agent.run_agent(project, runner, prompt=prompt)
            errs, warns = validate(project)
            note_errs = check_notes_problems(project) if not errs else []
            style = [w for w in warns if w.startswith(("Giọng văn", "Phát âm", "Nguyên tác"))]
            if not errs and not note_errs and style and attempt < retries and settings.get("script.enforce_style", True):
                note_errs = style  # lời bình còn khô → cho agent sửa thêm một lượt
            if not errs and not note_errs:
                break
            if not errs and attempt >= retries:
                # Kịch bản hợp lệ nhưng agent chưa soát hình đầy đủ → vẫn cho duyệt, kèm cảnh báo rõ ràng
                warns = warns + ["Agent chưa hoàn tất bước soát hình: " + "; ".join(note_errs[:3])]
                break
            if attempt >= retries:
                raise RuntimeError("Agent chưa tạo được script.json hợp lệ: " + "; ".join(errs[:5]))
            errs = errs or note_errs
            attempt += 1
            # Dự phòng: nếu agent không tự chạy được bước chuẩn bị thì tool chạy giùm
            if not project.f("context.md").exists():
                project.log("Agent chưa chạy bước chuẩn bị — tool tự chạy `prepare` rồi giao lại cho agent.")
                project.update(stage="Tool đang chuẩn bị dữ liệu (lời thoại, cảnh, khung hình)")
                prepare(project, runner)
            write_text_bom(project.f("FIX.md"), 
                "# Fix needed\nThe previous attempt is not finished. Problems:\n"
                + "\n".join(f"- {e}" for e in errs)
                + "\n\nPreparation output (context.md, frames/) already exists — do not redo it unless missing. "
                  "Fix them, run validate / sync / check until everything passes (TASK.md Step 3 to 3c), "
                  "and write check_notes.json.\n")
            prompt = agent.SHORT_PROMPT + " Also read FIX.md first: it lists what went wrong last time."

        for w in warns:
            project.log(f"Cảnh báo kịch bản: {w}")
        project.update(warnings=warns)

        if (opts.get("mode") or "review") == "review":
            project.update(status="review", stage="Chờ duyệt kịch bản")
            project.log("Kịch bản đã sẵn sàng — mở giao diện để duyệt rồi bấm 'Dựng video'.")
            return

        final = project.f("output", "final.mp4")
        if not final.exists() or final.stat().st_mtime < project.f("script.json").stat().st_mtime:
            project.log("Agent chưa dựng video (hoặc kịch bản mới hơn video) — tool tự dựng.")
            render_job(project, runner, set_running=False)
        else:
            project.update(status="done", stage="Hoàn tất", output="output/final.mp4")
    except Cancelled:
        project.log("Đã huỷ job.")
        project.update(status="cancelled", stage="Đã huỷ")
    except Exception as e:  # noqa: BLE001
        project.log(f"LỖI: {e}")
        project.log(traceback.format_exc())
        project.update(status="error", stage="Lỗi", error=str(e))


def render_job(project: Project, runner: ProcessRunner, set_running: bool = True) -> None:
    try:
        errs, warns = validate(project)
        if errs:
            raise RuntimeError("Kịch bản còn lỗi: " + "; ".join(errs[:5]))
        project.update(status="rendering", stage="Tạo giọng đọc", error=None)
        tts.run(project, runner)
        project.update(stage="Dựng video")
        render.run(project, runner)
        project.update(status="done", stage="Hoàn tất", output="output/final.mp4")
    except Cancelled:
        project.log("Đã huỷ job.")
        project.update(status="cancelled", stage="Đã huỷ")
    except Exception as e:  # noqa: BLE001
        project.log(f"LỖI: {e}")
        project.log(traceback.format_exc())
        project.update(status="error", stage="Lỗi", error=str(e))


def info(project: Project) -> str:
    st = project.state()
    files = {n: project.f(n).exists() for n in ("transcript.json", "scenes.json", "context.md", "script.json",
                                                 "tts/manifest.json", "output/final.mp4")}
    return json.dumps({"id": st.get("id"), "status": st.get("status"), "stage": st.get("stage"),
                       "source_duration": st.get("source_duration"), "files": files}, ensure_ascii=False, indent=2)
