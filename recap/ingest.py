"""Bước 1: lấy video nguồn — tải từ link (yt-dlp) hoặc nhận file khách kéo vào."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Optional

from . import media, settings
from .project import ProcessRunner, Project

SUPPORTED_HINT = "YouTube, TikTok, Facebook, Bilibili"


def _common_args(cfg: dict) -> list[str]:
    import shutil
    args: list[str] = []
    if cfg.get("cookies_from_browser"):
        args += ["--cookies-from-browser", cfg["cookies_from_browser"]]
    if cfg.get("cookies_file"):
        args += ["--cookies", cfg["cookies_file"]]
    # YouTube cần một JS runtime; Node có sẵn vì Codex cần Node
    if not shutil.which("deno") and shutil.which("node"):
        args += ["--js-runtimes", "node"]
    return args


def download(project: Project, url: str, runner: Optional[ProcessRunner] = None) -> Path:
    runner = runner or ProcessRunner(project)
    cfg = settings.get("download", {}) or {}
    project.log(f"Tải video từ link: {url}")
    h = int(cfg.get("max_height") or 720)
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist", "--newline", "--no-part", "--progress-delta", "15",
        "-f", f"bv*[ext=mp4][height<={h}]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]/b",
        "--merge-output-format", "mp4",
        "--ffmpeg-location", str(Path(media.ffmpeg()).parent),
        "-o", str(project.f("source.%(ext)s")),
        "--write-info-json",
    ]
    cmd += _common_args(cfg)
    cmd.append(url)
    rc = runner.run(cmd, cwd=project.path, timeout=3 * 3600)
    src = project.source()
    if rc != 0 or not src:
        raise RuntimeError(
            "Không tải được video từ link này. Cách xử lý: (1) vào tab Kiểm tra máy → bấm 'Cập nhật bộ tải video' rồi thử lại; "
            "(2) với Bilibili/Facebook có thể cần cookie trình duyệt (Cài đặt → Tải video từ link); "
            "(3) hoặc tự tải video về máy rồi kéo file vào tool."
        )
    info = project.f("source.info.json")
    if info.exists():
        import json
        try:
            title = json.loads(info.read_text(encoding="utf-8")).get("title")
            if title and not project.state().get("title"):
                project.update(title=title)
        except Exception:
            pass
    project.log(f"Đã tải xong: {src.name}")
    if settings.get("transcribe.use_source_subtitles", True):
        download_subtitles(project, url, runner)
    return src


def download_subtitles(project: Project, url: str, runner: Optional[ProcessRunner] = None) -> None:
    """Tải phụ đề có sẵn (thủ công hoặc tự động) của video — nếu có thì bỏ qua được bước nhận dạng lời thoại."""
    runner = runner or ProcessRunner(project)
    cfg = settings.get("download", {}) or {}
    project.log("Thử lấy phụ đề có sẵn của video…")
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--skip-download", "--ignore-errors",
           "--write-subs", "--write-auto-subs",
           "--sub-langs", "vi,vi-orig,en,en-orig,.*-orig,ja,ko,zh-Hans,zh-Hant,zh,zh-CN,-live_chat",
           "--convert-subs", "srt", "--ffmpeg-location", str(Path(media.ffmpeg()).parent),
           "-o", str(project.f("subs.%(ext)s"))]
    cmd += _common_args(cfg)
    cmd.append(url)
    try:
        runner.run(cmd, cwd=project.path, timeout=300)
    except Exception as e:  # noqa: BLE001 — phụ đề chỉ là phần thêm, lỗi thì bỏ qua
        project.log(f"  không lấy được phụ đề: {e}")


def import_file(project: Project, file_path: Path) -> Path:
    ext = file_path.suffix.lower() or ".mp4"
    dst = project.f("source" + ext)
    if file_path.resolve() != dst.resolve():
        shutil.move(str(file_path), dst)
    if not project.state().get("title"):
        project.update(title=file_path.stem)
    project.log(f"Đã nhận file: {dst.name}")
    return dst


def check_source(project: Project) -> dict:
    src = project.source()
    if not src:
        raise RuntimeError("Chưa có video nguồn trong dự án.")
    info = media.probe(src)
    dur = float(info.get("format", {}).get("duration") or 0)
    has_v = any(s.get("codec_type") == "video" for s in info.get("streams", []))
    if not has_v or dur <= 0:
        raise RuntimeError("File nguồn không phải video hợp lệ (không có hình hoặc thời lượng = 0).")
    max_h = float(settings.get("limits.max_source_hours", 3) or 3)
    if dur > max_h * 3600:
        raise RuntimeError(f"Video nguồn dài {dur/3600:.1f} giờ, vượt giới hạn {max_h:g} giờ của bản này.")
    project.update(source_duration=dur, source_has_audio=any(s.get("codec_type") == "audio" for s in info.get("streams", [])))
    project.log(f"Nguồn: {src.name}, thời lượng {media.fmt_ts(dur)}")
    return {"path": str(src), "duration": dur}
