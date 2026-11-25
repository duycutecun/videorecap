"""Agent GIẢ để kiểm thử luồng chạy mà không tốn hạn mức agent thật.

Thêm vào Cài đặt → agent.commands:  "fake": ["python", "<đường dẫn>/tests/fake_agent.py", "{prompt}"]
Nó tự tạo transcript giả, chạy recap scenes/frames/context, viết script.json đơn giản, validate,
và nếu TASK.md ở chế độ AUTO thì chạy tts + render.
"""
import json, subprocess, sys, os
print("FAKE AGENT prompt:", sys.argv[1][:80])
task = open("TASK.md", encoding="utf-8").read()
CMD = ["cmd", "/c", "recap.cmd"] if os.name == "nt" else ["./recap.sh"]
run = lambda *a: print(subprocess.run([*CMD, *a], capture_output=True, text=True, encoding="utf-8").stdout)
if not os.path.exists("transcript.json"):
    json.dump({"language":"en","segments":[{"start":2,"end":5,"text":"Hello."}]}, open("transcript.json","w"))
run("scenes"); run("frames"); run("context")
sc = json.load(open("scenes.json", encoding="utf-8"))
segs = []
for s in sc:
    segs.append({"id": s["id"], "narration": f"Đây là cảnh số {s['id']}, một khoảnh khắc đáng nhớ với nhiều màu sắc rực rỡ và bất ngờ.",
                 "clips": [{"start": s["start"]+0.5, "end": min(s["end"], s["start"]+4.5)}]})
if "BROKEN" in task:
    segs[0]["clips"][0]["end"] = 9999
doc = {"title":"Test","language":"vi","segments":segs}
if "YOU DECIDE" in task:
    doc["target_minutes"] = 0.6 if "NOMIN" not in task else None
    doc["length_reason"] = "Video ngắn, ít tình tiết nên tóm tắt ngắn."
json.dump(doc, open("script.json","w",encoding="utf-8"), ensure_ascii=False)
run("validate")
run("sync")
if "visual check" in task:
    run("check")
    json.dump([{"id": x["id"], "on_screen": f"Cảnh {x['id']}: các mảng màu chuyển động", "matches": True} for x in segs],
              open("check_notes.json", "w", encoding="utf-8"), ensure_ascii=False)
if "AUTO mode" in task:
    run("render")
print("TASK_COMPLETE")
