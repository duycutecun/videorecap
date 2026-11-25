"""Bộ lệnh `recap`.

Lệnh cho agent (chạy trong thư mục dự án jobs/<id>/):
  prepare | transcribe | scenes | frames | context | validate | tts | render | info
Lệnh quản trị (chạy ở thư mục gốc của tool):
  serve | health | fetch-models | cleanup | update-ytdlp | update-tool
"""
from __future__ import annotations

import argparse
import sys

from . import paths  # noqa: F401  (thiết lập PATH)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="recap", description="Video recap tool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "transcribe", "scenes", "frames", "sheets", "context", "validate", "sync", "check", "tts", "render", "info"):
        sub.add_parser(name)
    sp = sub.add_parser("serve")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.add_argument("--no-browser", action="store_true")
    sub.add_parser("health")
    sub.add_parser("fetch-models")
    sub.add_parser("cleanup")
    sub.add_parser("update-ytdlp")
    sub.add_parser("update-tool")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        from .server import serve
        serve(a.host, a.port, open_browser=not a.no_browser)
        return 0
    if a.cmd == "health":
        from .health import print_report
        return 1 if print_report() else 0
    if a.cmd == "fetch-models":
        from .transcribe import load_model
        print("Đang tải model nhận dạng lời thoại (lần đầu có thể mất vài phút)…")
        _, device, compute = load_model()
        print(f"OK — model sẵn sàng (device={device}, compute={compute})")
        return 0
    if a.cmd == "cleanup":
        from .maintenance import cleanup
        print("Đã xoá:", cleanup() or "không có")
        return 0
    if a.cmd in ("update-ytdlp", "update-tool"):
        from . import maintenance
        rc, out = maintenance.update_ytdlp() if a.cmd == "update-ytdlp" else maintenance.update_tool()
        print(out)
        return rc

    # ---- lệnh trong thư mục dự án ----
    from . import pipeline, render, scenes, transcribe, tts
    from .project import Project
    proj = Project.from_cwd()
    try:
        if a.cmd == "prepare":
            pipeline.prepare(proj)
            print("OK — đã có context.md, transcript.txt, frames/")
        elif a.cmd == "transcribe":
            from . import ingest
            if not proj.state().get("source_duration"):
                ingest.check_source(proj)
            transcribe.run(proj)
            print("OK — transcript.txt")
        elif a.cmd == "scenes":
            from . import ingest
            if not proj.state().get("source_duration"):
                ingest.check_source(proj)
            sc = scenes.detect(proj)
            print(f"OK — {len(sc)} cảnh trong scenes.json")
        elif a.cmd == "frames":
            import json
            sc = json.loads(proj.f("scenes.json").read_text(encoding="utf-8"))
            scenes.extract_frames(proj, sc)
            print("OK — frames/")
        elif a.cmd == "sheets":
            from . import sheets as sh
            if not proj.state().get("source_duration"):
                from . import ingest
                ingest.check_source(proj)
            idx = sh.make(proj)
            print(f"OK - {len(idx['sheets'])} sheets in sheets/")
        elif a.cmd == "context":
            import json
            sc = json.loads(proj.f("scenes.json").read_text(encoding="utf-8"))
            scenes.build_context(proj, sc)
            print("OK — context.md")
        elif a.cmd == "validate":
            errs, warns = pipeline.validate(proj)
            for e in errs:
                print("ERROR:", e)
            for w in warns:
                print("WARNING:", w)
            if errs:
                return 1
            print("OK")
        elif a.cmd == "sync":
            from . import sync as sy
            errs, _ = pipeline.validate(proj)
            if errs:
                for e in errs:
                    print("ERROR:", e)
                return 1
            tts.run(proj)
            rows = sy.report(proj)
            bad = 0
            print(f"{'seg':>4} {'speech':>8} {'clips':>8} {'diff':>8}  status")
            for r in rows:
                note = {"ok": "OK", "auto": "OK (tool will stretch/trim inside the same scene)",
                        "short": f"TOO SHORT: add about {-r['diff']:.1f}s of clips showing what this narration describes",
                        "long": f"TOO LONG: clips must total {r['need']:.1f}s - move the END earlier by about "
                                f"{r['diff']:.1f}s, keeping the START just before the described action"}[r["status"]]
                if r["status"] in ("short", "long"):
                    bad += 1
                print(f"{r['id']:>4} {r['speech']:>7.1f}s {r['clip']:>7.1f}s {r['diff']:>+7.1f}s  {note}")
            if bad:
                print(f"\nSYNC FAILED: {bad} segment(s) do not match. Edit their clips in script.json and run sync again.")
                return 1
            print("\nSYNC OK")
        elif a.cmd == "check":
            from . import check as ck
            errs, _ = pipeline.validate(proj)
            if errs:
                for e in errs:
                    print("ERROR:", e)
                return 1
            tts.run(proj)
            _, todo = ck.run(proj)
            if todo:
                print(f"OK - {len(todo)} segment(s) are new or changed and need a look: {', '.join(map(str, todo))}")
                print("Open ONLY those images in check/ (see check.md). The other segments are unchanged since your last check.")
            else:
                print("OK - nothing changed since your last check; no image needs to be opened again.")
        elif a.cmd == "tts":
            tts.run(proj)
            print("OK — tts/")
        elif a.cmd == "render":
            out = render.run(proj)
            print(f"OK — {out.relative_to(proj.path)}")
        elif a.cmd == "info":
            print(pipeline.info(proj))
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
