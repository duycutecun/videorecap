# TTS GIẢ để kiểm thử: tạo tiếng bíp dài theo số chữ. Cấu hình local_command: ["{python}", "tests/fake_tts.py", "--text-file", "{text_file}", "--out", "{out_file}"]
import sys, subprocess
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
text = open(args["--text-file"], encoding="utf-8").read()
dur = max(1.0, len(text.split()) / 165 * 60)
subprocess.run(["ffmpeg","-y","-loglevel","error","-f","lavfi","-i",f"sine=f=220:d={dur:.2f}",args["--out"]], check=True)
