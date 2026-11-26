# Video Recap Tool — v1.0.4

Tool tạo video tóm tắt kiểu kênh "recap anime/phim" từ link (YouTube, TikTok, Facebook, Bilibili) hoặc từ file video. Tool chạy hoàn toàn trên máy Windows, giao diện mở bằng trình duyệt ở `http://127.0.0.1:7860`. Phần viết kịch bản do agent **Claude Code**, **Codex** hoặc **Antigravity (agy)** đảm nhận, chạy bằng tài khoản của người dùng.

```
Link / file ─► Lấy video ─► [AGENT] prepare (lời thoại · cảnh · khung hình)
                                   ─► đọc context.md + xem ảnh ─► viết script.json ─► validate
            ─► (Duyệt & sửa kịch bản trên giao diện) ─► Giọng đọc ─► Dựng video ─► output/final.mp4 + .srt
```

---

## 1. Yêu cầu máy

| | Tối thiểu (cam kết) | Ghi chú |
|---|---|---|
| Hệ điều hành | Windows 10/11 64-bit | macOS chưa hỗ trợ ở bản này |
| GPU | Không bắt buộc. Có NVIDIA (từ 6–8 GB VRAM, driver mới) thì nhận dạng lời thoại nhanh hơn nhiều | Máy chỉ có CPU: tool tự dùng phụ đề có sẵn của video nếu có, model Whisper nhỏ (`small`) hoặc nhận dạng qua Groq/OpenAI (Cài đặt) |
| RAM | 16 GB | |
| Ổ trống | 50 GB | model ~3–5 GB, mỗi job thêm khoảng 1–3 lần dung lượng video nguồn |
| Mạng | Cần khi cài đặt, tải link, gọi agent | |
| Agent | Claude Code, Codex CLI **hoặc** Antigravity CLI, đã đăng nhập | xem mục 3 |

## 2. Cài đặt (một lần)

### Cách 1: Cài tự động 1-Click trên máy mới (khuyên dùng)
1. Tải 1 file duy nhất [**`CAI-DAT-VIDEORECAP.bat`**](https://github.com/duycutecun/videorecap/releases/latest/download/CAI-DAT-VIDEORECAP.bat) từ mục Releases (hoặc file đi kèm trong repo).
2. Bấm đúp chạy `CAI-DAT-VIDEORECAP.bat` → chọn thư mục cài đặt (mặc định `D:\VideoRecap` nếu ổ D trống, hoặc `C:\VideoRecap`; **không dùng đường dẫn có dấu tiếng Việt hoặc dấu cách**).
3. Script sẽ tự động làm toàn bộ từ A→Z:
   - cài Git for Windows (nếu máy chưa có) và tải mã nguồn từ GitHub
   - cài Python 3.11 portable (qua `uv`), thư viện, `ffmpeg` portable và `7zr`
   - đăng ký font phụ đề (và tải giọng `Piper` nếu máy không có GPU NVIDIA)
   - tải model nhận dạng lời thoại `Whisper` (`large-v3-turbo` trên GPU hoặc `small` trên CPU)
   - nếu máy có **GPU NVIDIA**: tự cài thêm **OmniVoice GPU** (`tools\omni_venv`), tải sẵn model `kjanh/KhanhTTS-OmniVoice` và giọng mẫu **Hải Đăng**, tự bật ngầm khi chạy
   - hướng dẫn cài & đăng nhập Agent (`Codex` / `Claude Code` / `Antigravity`) và tạo lối tắt **VideoRecap** ngoài Desktop.

### Cách 2: Clone Git hoặc giải nén thủ công
1. Chạy `git clone https://github.com/duycutecun/videorecap.git` vào một nơi cố định, ví dụ `D:\VideoRecap`. **Không để đường dẫn có dấu tiếng Việt.**
2. Bấm đúp **`install.bat`**. Script sẽ tự làm hết các bước cài đặt, tất cả nằm gọn trong thư mục tool.
3. Lần sau chỉ cần bấm **`run.bat`**: nếu máy có OmniVoice GPU thì dịch vụ giọng đọc tự khởi động ngầm và trình duyệt sẽ tự mở giao diện.

> Nếu chuyển thư mục tool sang chỗ khác thì phải chạy lại `install.bat`, vì môi trường `.venv` lưu đường dẫn tuyệt đối.

## 3. Cài agent (bắt buộc, chọn một)

**Claude Code** (dùng gói Claude Pro / Max / Team / Enterprise, hoặc tài khoản Claude Console):
```
irm https://claude.ai/install.ps1 | iex      ← chạy trong PowerShell
claude                                        ← chạy một lần để đăng nhập, rồi gõ /exit
```
Nên cài thêm Git for Windows để Claude Code dùng bash; nếu không có Git, Claude Code dùng PowerShell.

**Codex** (dùng gói ChatGPT, hoặc API key OpenAI):
```
npm install -g @openai/codex
codex            ← chạy một lần để đăng nhập, rồi thoát
```
**Antigravity CLI** (dùng gói Google AI):
```
irm https://antigravity.google/cli/install.ps1 | iex
agy              ← chạy một lần để đăng nhập, rồi thoát
```
Sau đó vào **Cài đặt → Agent mặc định** để chọn agent.

Lệnh gọi agent có thể sửa trong **Cài đặt → Lệnh gọi agent** (JSON). `{prompt}` là chỗ tool chèn yêu cầu vào. Mặc định:
- codex: `codex exec --skip-git-repo-check --sandbox workspace-write -c sandbox_workspace_write.network_access=true "{prompt}"`
- agy: `agy -p "{prompt}" --dangerously-skip-permissions`
- claude: `claude -p "{prompt}" --dangerously-skip-permissions --output-format text` (chọn model bằng `--model`, mức suy luận bằng `--effort`)

Agent chỉ làm việc trong thư mục của từng job (`jobs/<id>/`) và chỉ gọi bộ lệnh `recap` (xem mục 6). Agent không tự viết lệnh ffmpeg.

## 4. Dùng tool

1. Vào tab **Tạo video**, dán link hoặc chọn file, rồi chọn loại nội dung, độ dài (để AI tự quyết, chọn sẵn, hoặc kéo thanh trượt **10–100% độ dài video gốc**), khung hình (16:9, 9:16, 1:1), ngôn ngữ và **giọng văn** lời bình, agent (kèm model, mức suy luận), giọng đọc. Bấm **Bắt đầu**.
2. Các job chạy **lần lượt từng cái một** để không tranh GPU. Tab **Danh sách** hiện trạng thái của mọi job.
3. **Chế độ "Dừng để duyệt"** (mặc định): agent viết xong kịch bản thì job dừng ở trạng thái **Chờ duyệt**. Trên giao diện có thể:
   - sửa lời bình, đổi clip (`12.5-17; 40-43.5`), chỉnh âm lượng tiếng gốc, thêm, xoá hoặc đổi thứ tự các đoạn
   - bấm **▶ Xem clip** để xem đúng đoạn gốc ở khung bên phải
   - bấm **Lưu & Dựng video**
   - bấm **✍️ Viết lại lời cho tự nhiên**: agent giữ nguyên các clip, chỉ viết lại lời cho liền mạch như người kể chuyện (kịch bản cũ lưu ở `script.before_polish.json`)
   - bấm **Cho agent viết lại** (viết lại cả kịch bản, có thể kèm yêu cầu mới)
4. **Chế độ "Chạy thẳng"**: agent tự chạy luôn bước giọng đọc và dựng video. Nếu agent bỏ qua bước này thì tool tự dựng.
5. Kết quả: xem ngay trên giao diện, hoặc bấm **Tải video (.mp4)** và **Tải phụ đề (.srt)**. Tool không tự đăng video lên đâu cả.

### Lời bình khớp với hình
- **Hình trước, lời sau:** tool tạo **bảng ảnh** (`sheets/`) phủ toàn bộ video, vài giây một khung hình, mỗi khung có ghi mốc giây. Agent chọn đúng giây đang diễn ra hành động rồi mới viết câu cho clip đó.
- **Chế độ khớp chặt** (mặc định): mỗi đoạn đúng 1 câu, mỗi câu có clip riêng. Tắt trong Cài đặt nếu muốn đoạn dài hơn.
- Agent chạy `sync` với **giọng đọc thật** và phải sửa mọi đoạn có clip dài hơn lời (để hành động không bị cắt mất).
- **Agent tự soát bằng mắt:** chạy `check`, xem ảnh đối chiếu (đầu, giữa, cuối mỗi clip) và ghi lại thứ nó thấy vào `check_notes.json`. Mô tả này hiện dưới mỗi đoạn trên trang duyệt (👁 AI thấy trên hình…).
- Khi dựng, clip ngắn hơn lời thì tool **nới clip ra** (được lấn sang cảnh kế bên — tắt trong Cài đặt nếu không muốn), thiếu nữa mới đổi tốc độ hình nhẹ (mặc định tối đa ±6%). Clip dài hơn thì tua nhanh nhẹ rồi cắt bớt đuôi.
- Trên trang duyệt, mỗi đoạn hiện `giọng Xs · clip Ys` kèm nhãn ✓ khớp / ⚙ tool tự căn / ⚠ thiếu hoặc thừa hình. Nút **🔊 Xem + nghe** phát đúng clip đã căn kèm giọng đọc, y như video thành phẩm.

### Lời bình tự nhiên
- Chọn **giọng văn** khi tạo job: kể chuyện tự nhiên (mặc định), hài hước, kịch tính, nhẹ nhàng, tài liệu.
- Agent được yêu cầu viết như người kể chuyện: câu nối với nhau, có từ nối, nói mục đích và cảm xúc, không tả góc máy.
- `validate` cảnh báo khi nhiều câu mở đầu giống nhau (vd. "Anh…" liên tục) hoặc tả camera; tool tự bắt agent sửa thêm một lượt.

### Chuyển cảnh mượt
- Giọng đọc được cắt bớt khoảng lặng thừa đầu/cuối câu; khoảng nghỉ giữa hai câu chỉnh trong **Cài đặt** (mặc định 0,2 giây).
- Khi phải đổi tốc độ hình, tool trộn khung thay vì lặp/bỏ khung, nên không bị giật.
- Âm thanh từng đoạn được ghép ở dạng không nén rồi mới nén một lần, tiếng gốc vào/ra nhẹ ở mỗi chỗ cắt → không bị "bụp".

### Tự chỉnh sửa (LosslessCut / ffmpeg)
Trên trang job đã có video, khung **"Tự chỉnh sửa"** có:
- **Mở bằng LosslessCut:** trình cắt ghép video có giao diện, cắt không làm giảm chất lượng. Lần đầu bấm **Cài LosslessCut** để tool tự tải về thư mục `tools/`.
- **Mở thư mục video:** mở thư mục `output` trong File Explorer.
- **Mở cửa sổ lệnh ffmpeg:** mở cửa sổ CMD ngay trong thư mục `output`, đã có sẵn ffmpeg, gõ lệnh trực tiếp được.
- **Ô chạy lệnh:** chọn mẫu lệnh có sẵn (cắt đoạn, tăng âm lượng, tăng tốc, thêm logo, thêm chữ, làm mờ dần, xuất 720p, tách âm thanh) hoặc tự gõ lệnh. Dùng `{in}` cho file đầu vào và `{out}` cho file kết quả. Kết quả luôn ghi ra file mới `edit_01.mp4`, `edit_02.mp4`…, không bao giờ ghi đè `final.mp4`.

### Link không tải được?
- Vào **Kiểm tra máy → Cập nhật bộ tải video**. Các trang video thay đổi liên tục nên yt-dlp cần được cập nhật thường xuyên.
- Với Bilibili hoặc Facebook, có thể cần cookie: **Cài đặt → Lấy cookie từ trình duyệt**, điền `chrome`, `edge` hoặc `firefox`.
- Nếu vẫn không được, tự tải video về máy rồi dùng **Chọn file trên máy**.

## 5. Giọng đọc

| Nhà cung cấp | Chạy ở đâu | Chi phí | Ghi chú |
|---|---|---|---|
| `omnivoice` (mặc định trên GPU) | tại máy (GPU) | miễn phí | clone giọng tự nhiên (kèm sẵn giọng **Hải Đăng** trong `omnivoice/voices/haidang/`), tự bật ngầm qua `START-OMNIVOICE.bat` khi mở `run.bat`. Máy không có GPU tự chuyển sang `piper`/`edge` |
| `vieneu` | tại máy | miễn phí | giọng Việt tự nhiên (VieNeu-TTS v3). Cài bằng nút **Kiểm tra máy → Cài VieNeu**; lần đầu tự tải model (cần mạng). Máy CPU yếu thì chọn model `v3nano` trong Cài đặt |
| `piper` (mặc định) | tại máy | miễn phí | giọng `vi_VN-vais1000-medium`, chất lượng vừa phải |
| `local_command` | tại máy | miễn phí | gọi bất kỳ TTS nào trên máy (VieNeu, XTTS…) qua mẫu lệnh `{text_file}` `{out_file}` `{voice}` `{python}` |
| `edge` | online | miễn phí | giọng Microsoft (`vi-VN-HoaiMyNeural`, `vi-VN-NamMinhNeural`), cần mạng |
| `openai` | online | theo ký tự | cần API key |
| `elevenlabs` | online | theo ký tự | cần API key và voice ID |

**Chọn giọng:** mọi ô Giọng (trang Tạo video, trang duyệt kịch bản, Cài đặt) là danh sách để chọn. Với VieNeu, danh sách lấy từ chính model trên máy và chia nhóm: **Kể chuyện — hợp video recap/review** (Anh Khôi, Thanh Bình, Thái Sơn, Đức Trí, Ngọc Linh, Quỳnh Anh…), Tự nhiên, Đọc tin, kèm giới tính và vùng miền. Muốn dùng giọng không có trong danh sách thì chọn **Khác (tự gõ)…**.

**Nghe thử giọng:** vào **Cài đặt → 🔊 Nghe thử giọng**, chọn giọng trong danh sách là nghe ngay một câu mẫu; chỉnh tốc độ rồi bấm **Đặt làm giọng mặc định**. Ở trang Tạo video có nút ▶ cạnh ô Giọng để nghe thử trước khi bắt đầu.

**Từ tiếng Anh / viết tắt:** giọng Việt hay đọc sai những từ này, nên tool:
- có sẵn **từ điển phát âm** khoảng 50 từ phổ biến (YouTube → diu túp, AI → ây ai, laptop → láp tốp, km → ki lô mét…); khách tự thêm trong **Cài đặt → Từ điển phát âm**, mỗi dòng `từ = cách đọc`;
- mỗi đoạn có ô **"Đọc là"**: ghi cách đọc riêng cho câu đó (phụ đề vẫn giữ chữ gốc);
- agent được yêu cầu ưu tiên từ Việt, viết số/đơn vị như khi nói, và điền sẵn cách đọc cho tên riêng nước ngoài; `validate` cảnh báo đoạn nào còn từ nước ngoài chưa có cách đọc.

Đoạn nào không đổi lời bình thì tool dùng lại file giọng cũ, nên sửa một đoạn rồi dựng lại sẽ không phải tạo lại toàn bộ giọng đọc.

**Nhạc nền:** bỏ file nhạc (mp3, wav, m4a…) vào một thư mục, rồi điền đường dẫn thư mục đó vào **Cài đặt → Thư mục nhạc nền**. Nhạc tự giảm âm lượng khi có lời đọc và tự nhỏ dần ở cuối video. Tool chỉ dùng nhạc trong thư mục này, không tự đi tìm nhạc.

## 6. Bộ lệnh `recap` (agent dùng; dev cũng dùng được)

Chạy bên trong thư mục job, trên Windows là `.\recap.cmd <lệnh>`:

| Lệnh | Việc |
|---|---|
| `prepare` | gộp `transcribe` + `scenes` + `frames` + `context` (phần nào đã có thì bỏ qua) |
| `transcribe` | Whisper → `transcript.json`, `transcript.txt` |
| `scenes` | cắt cảnh bằng ffmpeg → `scenes.json` |
| `frames` | 1–3 ảnh mỗi cảnh, tối đa 300 ảnh → `frames/` |
| `context` | gộp cảnh, lời thoại và ảnh → `context.md` (agent đọc file này) |
| `validate` | kiểm tra `script.json`, in `ERROR`, `WARNING` hoặc `OK` |
| `check` | tạo ảnh đối chiếu cho từng đoạn (khung hình đầu, giữa, cuối mỗi clip, có ghi mốc giây) để agent tự soát lời bình có khớp với hình không |
| `sync` | tạo giọng đọc rồi so độ dài giọng thật với độ dài clip của từng đoạn, in `SYNC OK` hoặc đoạn nào thiếu/thừa hình |
| `tts` | tạo giọng đọc cho từng đoạn → `tts/` |
| `render` | dựng → `output/final.mp4` và `output/final.srt` |
| `info` | trạng thái job |

Lệnh quản trị (chạy ở thư mục gốc): `.venv\Scripts\python.exe -m recap health | fetch-models | cleanup | update-ytdlp | update-tool | serve`.

## 7. Cập nhật tool

- Nếu thư mục tool là bản `git clone` từ repo riêng: **Kiểm tra máy → Cập nhật tool** (tool chạy `git pull` rồi cài lại thư viện). Sau đó tắt và mở lại `run.bat`.
- Nếu không: chép đè file zip mới lên, **giữ nguyên** các thư mục `config/`, `jobs/`, `models/`, `tools/`, `.venv/`.

## 8. Sự cố thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| "Không tìm thấy 'codex'" hoặc "'agy'" | Cài agent (mục 3) rồi mở lại `run.bat` |
| Agent chạy rồi báo lỗi đăng nhập hoặc hết hạn mức | Mở terminal, gõ `codex` hoặc `agy` để đăng nhập lại; chờ hạn mức gói thuê bao hồi lại |
| Job đứng lâu ở bước Agent | Mỗi lần chạy agent có giới hạn thời gian (Cài đặt, mặc định 60 phút). Xem Nhật ký; có thể bấm Huỷ |
| Agent bị treo, không trả về (agy) | Đã có báo lỗi `agy -p` treo khi chạy không có terminal. Thử chuyển sang codex, hoặc cập nhật agy |
| Nhận dạng lời thoại rất chậm | Kiểm tra máy → dòng "CUDA cho Whisper" phải là ✓. Nếu ✕ thì cập nhật driver NVIDIA, rồi chạy lại `install.bat` |
| Phụ đề lỗi font | Cài đặt → Font phụ đề: dùng font có dấu tiếng Việt (Arial, Segoe UI, Tahoma) |
| Muốn biết vì sao một job hỏng | Mỗi job có `jobs/<id>/log.txt` ghi toàn bộ: lệnh agent đã gọi, kết quả, lỗi ffmpeg. Gửi file này cho bên làm tool |

## 9. Thư mục

```
recap/        mã nguồn (server, pipeline, agent, tts, render…)
web/          giao diện
presets/      hướng dẫn cho agent theo loại nội dung (film, show, lecture, game, vlog) — sửa được
config/       settings.example.json (mặc định) · settings.json (cài đặt của máy này)
jobs/<id>/    mỗi video một thư mục: source, transcript, frames, TASK.md, script.json, tts, output, log.txt
models/       Whisper, Piper
tools/        Python, ffmpeg, uv (portable)
tests/        agent giả + TTS giả để kiểm thử mà không tốn hạn mức
docs/SPEC.md  phạm vi bàn giao và kiến trúc
```

## 10. Lịch sử phiên bản

**v1.0.4** — sửa lỗi mã hóa UTF-8 trong `install.ps1` trên Windows PowerShell 5.1 ở bước cài Python, bổ sung cơ chế dự phòng tải `uv`, kiểm tra lại PyTorch CUDA cho OmniVoice và thêm `.gitattributes` giữ chuẩn CRLF cho `.bat`/`.ps1`.

**v1.0.3** — đóng gói bản phát hành cài full cho máy mới:
- Tích hợp sẵn dịch vụ giọng clone **OmniVoice GPU** (`omnivoice/` + 14 giọng mẫu Hải Đăng, v.v.), tự khởi động ngầm cùng `run.bat` và tự chờ nạp model.
- Máy không có GPU NVIDIA tự cài cấu hình nhẹ (Whisper `small` + Piper) và tự fallback từ OmniVoice sang Piper/Edge khi đọc lời bình.
- Thêm bộ cài 1-Click `CAI-DAT-VIDEORECAP.bat` tự tải từ GitHub, cài đặt toàn bộ từ đầu (kèm font phụ đề, `7zr`, chọn/cài Agent, shortcut Desktop).
- Cài đặt mặc định an toàn hơn cho máy khác (`interpolate_fps: 0`, `cpu_model: small`).

**v1.0.2** — chọn giọng từ danh sách (VieNeu chia nhóm kể chuyện / tự nhiên / đọc tin, kèm giới tính, vùng miền); nghe thử giọng ngay trong Cài đặt và trang Tạo video; đặt giọng mặc định bằng một nút; báo rõ khi gõ sai tên giọng VieNeu.

**v1.0.1** — giọng đọc từ tiếng Anh: từ điển phát âm (sửa được trong Cài đặt), ô "Đọc là" cho từng đoạn, agent tự ghi cách đọc tên riêng nước ngoài, cảnh báo khi còn sót.

**v1.0.0** — bản bàn giao (gom các bản thử nghiệm 0.1.x):
- Agent: Claude Code, Codex, Antigravity; chọn model và mức suy luận cho từng job.
- Độ dài: AI tự quyết, chọn sẵn, hoặc thanh trượt 10–100% độ dài video gốc.
- Nhận dạng lời thoại tối ưu cho máy không có GPU: dùng phụ đề có sẵn, model nhỏ, hoặc Groq/OpenAI.
- Lời bình khớp với hình: làm việc "hình trước, lời sau", mỗi đoạn một câu, đo bằng giọng thật, agent tự soát bằng ảnh đối chiếu.
- Lời bình tự nhiên: chọn giọng văn, cảnh báo văn khô, nút "Viết lại lời cho tự nhiên".
- Chuyển cảnh mượt: cắt khoảng lặng giọng đọc, khoảng nghỉ chỉnh được, trộn khung khi đổi tốc độ, ghép tiếng không bị hở.
- Giọng đọc VieNeu (chạy song song nhiều tiến trình), Piper, Edge, OpenAI, ElevenLabs, lệnh tùy chỉnh.
- Tự chỉnh sửa bằng LosslessCut hoặc ffmpeg; không bao giờ ghi đè `final.mp4`.
- Nhật ký lỗi ghi đầy đủ chi tiết (traceback) để dễ tìm nguyên nhân.

