# Đặc tả bàn giao — Video Recap Tool v1.0.0

Tài liệu này gồm hai phần: phần **A** dùng làm phụ lục phạm vi khi thỏa thuận với khách, phần **B** dành cho dev.

---

## A. Phạm vi và điều khoản (để gửi khách)

### A1. Tool làm được gì
Từ một link (YouTube, TikTok, Facebook, Bilibili) hoặc một file video, tool tạo ra một video tóm tắt có lời bình đọc bằng giọng máy, xen các clip ngắn trích từ video gốc, kèm phụ đề và nhạc nền (tùy chọn). Tool chạy trên máy Windows của khách, mở giao diện bằng trình duyệt ở localhost.

### A2. Bảng phạm vi

| Tham số | Bản bàn giao hỗ trợ | Ngoài phạm vi (báo giá riêng) |
|---|---|---|
| Nguồn | 1 file hoặc 1 link mỗi lần, tối đa 3 giờ (chỉnh được) | ghép nhiều tập hoặc cả mùa thành 1 video |
| Trang tải link | YouTube, TikTok, Facebook, Bilibili qua yt-dlp, **không bảo đảm** (xem A4) | trang khác, trang cần tài khoản trả phí |
| Độ dài đầu ra | AI tự quyết theo nội dung; Shorts (<1 phút), ~6, ~12, ~30 phút; hoặc 10–100% độ dài video gốc | |
| Khung hình | 16:9, 9:16, 1:1; độ phân giải 1080p hoặc 720p | 4K |
| Ngôn ngữ lời thoại gốc | mọi ngôn ngữ Whisper nhận dạng được | |
| Ngôn ngữ lời bình | Tiếng Việt, Tiếng Anh | ngôn ngữ khác |
| Loại nội dung | Phim/Anime, Show, Bài giảng/Podcast, Game, Vlog (mỗi loại một preset) | preset mới |
| Viết kịch bản | Claude Code, Codex CLI hoặc Antigravity CLI, dùng tài khoản của khách | API khác, model chạy offline |
| Giọng đọc | VieNeu, Piper (tại máy), lệnh TTS tùy chỉnh, Edge, OpenAI, ElevenLabs | nhân bản giọng (voice clone) |
| Duyệt | Chọn cho từng job: dừng để duyệt và sửa kịch bản, hoặc chạy thẳng | |
| Nhạc nền | Lấy từ thư mục nhạc khách tự cung cấp, tự giảm âm khi có lời đọc | tự tìm hoặc tự tạo nhạc |
| Đăng video | **Không.** Tool chỉ xuất file | tự đăng lên kênh |
| Lưu trữ | Lưu trên máy khách; tự xóa job cũ là tùy chọn, mặc định **tắt** | lưu trên cloud |
| Hệ điều hành | Windows 10/11; GPU NVIDIA không bắt buộc (có thì nhận dạng lời thoại nhanh hơn) | macOS, Linux |

### A3. Cấu hình máy tối thiểu được cam kết
Windows 10/11 64-bit, GPU NVIDIA từ 8 GB VRAM, RAM 16 GB, ổ trống 50 GB, có mạng. Máy yếu hơn mức này thì tool vẫn chạy, nhưng không cam kết tốc độ.

### A4. Điều khoản đề xuất (gợi ý, không phải tư vấn pháp lý)
1. **Nội dung:** Khách cam kết có quyền sử dụng video nguồn và giọng mẫu (nếu có), đồng thời tự chịu trách nhiệm về nội dung tạo ra và nơi đăng tải. Bên làm tool không đăng tải và không lưu trữ hộ.
2. **Tải từ link:** Tính năng này phụ thuộc bên thứ ba (các trang video, yt-dlp) và có thể ngừng hoạt động bất cứ lúc nào. Đường chính được hỗ trợ là đưa file video vào tool.
3. **Agent và hạn mức:** Chi phí và hạn mức của Codex, Antigravity và các API giọng đọc thuộc tài khoản của khách. Kết quả do AI viết có thể khác nhau giữa các lần chạy, và khách có trách nhiệm duyệt nội dung trước khi dùng.
4. **Bàn giao:** Bàn giao một lần, nguyên trạng, **không bảo hành**. Mọi cập nhật hoặc sửa đổi sau bàn giao được thực hiện **theo yêu cầu của khách và có tính phí**.

---

## B. Kiến trúc (cho dev)

### B1. Thành phần
| Module | Vai trò |
|---|---|
| `recap/server.py` | FastAPI và giao diện ở `127.0.0.1:7860`; hàng đợi chạy **từng job một**; huỷ job bằng cách kill cả cây tiến trình |
| `recap/pipeline.py` | Điều phối: lấy nguồn → agent → validate (agent viết lại nếu lỗi, tool tự `prepare` nếu agent bỏ bước) → dừng chờ duyệt hoặc dựng |
| `recap/agent.py` | **Chỗ duy nhất** gọi agent. Sinh `TASK.md` và wrapper `recap.cmd` trong thư mục job; lệnh gọi agent lấy từ settings |
| `recap/ingest.py` | yt-dlp (có cookie) hoặc nhận file; kiểm tra nguồn |
| `recap/transcribe.py` | faster-whisper (CUDA float16 nếu có, không thì CPU int8), VAD |
| `recap/scenes.py` | Cắt cảnh bằng ffmpeg `select=gt(scene,T)` trên bản thu nhỏ 320 px; gộp cảnh ngắn, chia cảnh dài; trích ảnh (≤ 300); ghi `context.md` |
| `recap/script_schema.py` | Cấu trúc `script.json` (pydantic) và các luật kiểm tra |
| `recap/tts.py` | Lớp nhà cung cấp giọng; cache theo hash (nhà cung cấp, giọng, tốc độ, lời) |
| `recap/render.py` | **Chỗ duy nhất** chứa lệnh ffmpeg dựng video: dựng từng đoạn (khớp clip với độ dài giọng, giữ khung hình cuối nếu clip thiếu), ghép bằng concat copy, trộn nhạc nền với sidechain ducking, xuất `.srt` |
| `recap/health.py` / `maintenance.py` | Kiểm tra máy; cập nhật yt-dlp; cập nhật tool qua git; tự xóa job cũ |

### B2. `script.json`
```json
{ "title": "…", "language": "vi",
  "segments": [ { "id": 1, "narration": "…",
                  "clips": [ {"start": 12.5, "end": 17.0} ],
                  "original_audio_volume": 0.0 } ] }
```
Lỗi (bắt buộc sửa): sai cấu trúc, trùng id, `end ≤ start`, clip vượt quá độ dài nguồn, clip ngắn hơn 0,4 giây.
Cảnh báo: clip dài hơn `max_clip_seconds` (mặc định 8 giây), tổng lời bình lệch quá ±30% so với độ dài mục tiêu.

### B3. Quy tắc với agent
- Mỗi job chỉ cần một prompt ngắn: "đọc TASK.md". Toàn bộ hướng dẫn nằm trong `TASK.md`, nên sửa hành vi agent bằng cách sửa `agent.build_task()` hoặc `presets/*.md`, không phải sửa code ở chỗ gọi agent.
- Agent chỉ được gọi bộ lệnh `recap`, chỉ làm việc trong thư mục job, không cài gói, không tự viết lệnh ffmpeg.
- Mọi output của agent được ghi vào `jobs/<id>/log.txt`. Đây là căn cứ để sửa lỗi khi khách báo.
- Mỗi lần chạy có giới hạn thời gian (`agent.timeout_minutes`). Nếu kịch bản lỗi, agent được chạy lại `max_retries` lần, kèm `FIX.md` liệt kê lỗi.

### B4. Rủi ro đã biết, cần thử trên máy khách trước khi nghiệm thu
1. `agy -p` có báo lỗi treo khi chạy không có terminal (non-TTY). Đã có timeout và kill cây tiến trình; nếu vẫn gặp thì dùng codex hoặc thư viện `agy-headless-bridge`.
2. Codex sandbox `workspace-write` trên Windows: cần kiểm tra agent có gọi được `recap.cmd` (Python và ffmpeg nằm ngoài thư mục job) và có mạng khi dùng giọng online. Nếu bị chặn thì đổi sang `--dangerously-bypass-approvals-and-sandbox` (rủi ro cao hơn) hoặc để tool tự `prepare` trước.
3. Hạn mức gói thuê bao: một nguồn khoảng 2 giờ sinh vài trăm ảnh, và agent đọc ảnh tốn hạn mức. Có thể giảm `frames.max_frames` nếu cần.
4. Chất lượng giọng Piper tiếng Việt chỉ ở mức trung bình. Nên chuẩn bị sẵn `local_command` cho VieNeu hoặc giọng cloud nếu khách cần giọng hay hơn.

### B5. Kiểm thử không tốn hạn mức
`tests/fake_agent.py` và `tests/fake_tts.py`: thêm agent `fake` vào `agent.commands`, rồi đặt giọng `local_command` trỏ tới `fake_tts.py`. Cách này chạy hết luồng (review, auto, kịch bản lỗi phải làm lại, huỷ) mà không cần GPU hay tài khoản agent.
