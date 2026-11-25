# Cài đặt Video Recap Tool FULL trên Windows — chạy một lần (qua install.bat).
# Mọi thứ (Python, ffmpeg, model Whisper/Piper/OmniVoice) nằm gọn trong thư mục tool, không làm đầy ổ C:.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "    [!] $msg" -ForegroundColor Yellow }
function Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }

# Tải file có thanh tiến trình, tự thử lại và thử nguồn dự phòng (dùng curl.exe có sẵn trên Windows 10/11)
function Download($urls, $out) {
    foreach ($u in @($urls)) {
        for ($i = 1; $i -le 3; $i++) {
            Write-Host "    Tải: $u (lần $i)"
            if (Test-Path $out) { Remove-Item $out -Force -ErrorAction SilentlyContinue }
            & curl.exe -L --fail --retry 3 --connect-timeout 30 --speed-limit 10000 --speed-time 60 -# -o $out $u
            if ($LASTEXITCODE -eq 0 -and (Test-Path $out) -and (Get-Item $out).Length -gt 1000) { return }
            Warn "Tải chưa được (mã $LASTEXITCODE), thử lại…"
        }
    }
    throw "Không tải được: $out. Kiểm tra mạng hoặc tải tay (xem README)."
}

New-Item -ItemType Directory -Force -Path tools, models, "models\hf", jobs, config, "cache\tmp" | Out-Null
$tmpCache = Join-Path $Root "cache\tmp"
$env:UV_CACHE_DIR = Join-Path $Root "tools\.cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Root "tools\python"
$env:HF_HOME = Join-Path $Root "models\hf"
$env:TEMP = $tmpCache
$env:TMP = $tmpCache
$env:UV_LINK_MODE = "copy"

# Phát hiện GPU NVIDIA
$gpuName = ""
try {
    $gpuObj = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'NVIDIA' } | Select-Object -First 1
    if ($gpuObj) { $gpuName = [string]$gpuObj.Name }
} catch {}
$hasNvidia = [bool]($gpuName -or (Get-Command nvidia-smi -ErrorAction SilentlyContinue))
if ($hasNvidia -and -not $gpuName) { $gpuName = "NVIDIA GPU" }

Write-Host ""
if ($hasNvidia) {
    Ok "Phát hiện GPU: $gpuName — cài đặt FULL kèm giọng OmniVoice GPU & Whisper CUDA"
} else {
    Warn "Không thấy GPU NVIDIA — cài đặt chế độ CPU gọn nhẹ (Whisper small + giọng Piper/Edge, bỏ qua OmniVoice GPU)"
}

# ---------------------------------------------------------------- 1. uv
Step "1/8  Cài uv (trình quản lý Python portable)"
$uvDir = Join-Path $Root "tools\uv"
$uv = Get-ChildItem -Path $uvDir -Recurse -Filter uv.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $uv) {
    $env:UV_INSTALL_DIR = $uvDir
    $env:UV_NO_MODIFY_PATH = "1"
    $env:INSTALLER_NO_MODIFY_PATH = "1"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $uv = Get-ChildItem -Path $uvDir -Recurse -Filter uv.exe -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $uv) { throw "Không cài được uv. Kiểm tra kết nối mạng rồi chạy lại." }
$uv = $uv.FullName
if ((Split-Path $uv -Parent) -ne $uvDir) { Copy-Item $uv (Join-Path $uvDir "uv.exe") -Force }
Ok $uv

# ---------------------------------------------------------------- 2. Python
Step "2/8  Cài Python 3.11 portable + môi trường ảo (.venv)"
& $uv python install 3.11
if ($LASTEXITCODE -ne 0) { throw "Cài Python 3.11 portable lỗi." }
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $uv venv .venv --python 3.11
    if ($LASTEXITCODE -ne 0) { throw "Tạo .venv lỗi." }
}
$py = Join-Path $Root ".venv\Scripts\python.exe"
Ok $py

# ---------------------------------------------------------------- 3. Thư viện
Step "3/8  Cài thư viện Python (lần đầu có thể mất 5-10 phút)"
& $uv pip install --python $py -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Cài thư viện lỗi." }
Ok "Đã cài thư viện"

# ---------------------------------------------------------------- 4. ffmpeg + 7zr
Step "4/8  Cài ffmpeg portable & 7zr"
if (-not (Test-Path "tools\ffmpeg\bin\ffmpeg.exe")) {
    $zip = Join-Path $Root "tools\ffmpeg.zip"
    Download @(
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    ) $zip
    Write-Host "    Giải nén ffmpeg…"
    $tmp = Join-Path $Root "tools\ffmpeg_tmp"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip -DestinationPath $tmp -Force
    $exe = Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1
    if (-not $exe) { throw "File ffmpeg tải về không đúng." }
    if (Test-Path "tools\ffmpeg") { Remove-Item "tools\ffmpeg" -Recurse -Force }
    Move-Item (Split-Path (Split-Path $exe.FullName -Parent) -Parent) (Join-Path $Root "tools\ffmpeg")
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
}
Ok (& "tools\ffmpeg\bin\ffmpeg.exe" -hide_banner -version | Select-Object -First 1)

if (-not (Test-Path "tools\7zr.exe")) {
    try {
        Download @("https://www.7-zip.org/a/7zr.exe") (Join-Path $Root "tools\7zr.exe")
        Ok "tools\7zr.exe"
    } catch {
        Warn "Không tải được 7zr.exe: $($_.Exception.Message). Có thể cài 7-Zip sau nếu dùng LosslessCut."
    }
} else {
    Ok "tools\7zr.exe (đã có)"
}

# ---------------------------------------------------------------- 5. Piper + Font phụ đề
Step "5/8  Tải giọng đọc Piper (tiếng Việt) & cài Font phụ đề"
$pdir = Join-Path $Root "models\piper"
New-Item -ItemType Directory -Force -Path $pdir | Out-Null
$base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium"
try {
    foreach ($ext in @(".onnx", ".onnx.json")) {
        $out = Join-Path $pdir ("vi_VN-vais1000-medium" + $ext)
        if (-not (Test-Path $out)) { Download @($base + $ext + "?download=true") $out }
    }
    Ok "models\piper\vi_VN-vais1000-medium.onnx"
} catch { Warn "Không tải được giọng Piper: $($_.Exception.Message). Có thể chọn giọng khác trong Cài đặt." }

$fontSrc = Join-Path $Root "assets\fonts"
if (Test-Path $fontSrc) {
    try {
        $fontDst = Join-Path $env:LOCALAPPDATA "Microsoft\Windows\Fonts"
        New-Item -ItemType Directory -Path $fontDst -Force | Out-Null
        $regPath = "HKCU:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
        if (-not (Test-Path $regPath)) { New-Item -Path $regPath -Force | Out-Null }
        $installedFonts = 0
        Get-ChildItem -Path $fontSrc -File | Where-Object { $_.Extension -in ".ttf", ".otf" } | ForEach-Object {
            $dstFile = Join-Path $fontDst $_.Name
            Copy-Item $_.FullName $dstFile -Force -ErrorAction SilentlyContinue
            $suffix = if ($_.Extension -eq ".otf") { " (OpenType)" } else { " (TrueType)" }
            Set-ItemProperty -Path $regPath -Name ($_.BaseName + $suffix) -Value $dstFile -ErrorAction SilentlyContinue
            $installedFonts++
        }
        if ($installedFonts -gt 0) { Ok "Đã đăng ký $installedFonts font phụ đề vào Windows" }
    } catch {
        Warn "Chưa đăng ký được font vào Windows (tool vẫn tự đọc font từ assets\fonts): $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------- 6. Cấu hình ban đầu + Whisper
Step "6/8  Thiết lập cấu hình theo phần cứng & tải model Whisper"
$cfgFile = Join-Path $Root "config\settings.json"
if (-not (Test-Path $cfgFile)) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    if ($hasNvidia) {
        [System.IO.File]::WriteAllText($cfgFile, "{}", $utf8NoBom)
        Ok "Đã tạo config\settings.json (mặc định GPU: OmniVoice + Whisper large-v3-turbo)"
    } else {
        $cpuCfg = '{"tts":{"provider":"piper"},"transcribe":{"model":"small","cpu_model":"small"}}'
        [System.IO.File]::WriteAllText($cfgFile, $cpuCfg, $utf8NoBom)
        Ok "Đã tạo config\settings.json (mặc định CPU: Piper + Whisper small)"
    }
} else {
    Ok "Giữ nguyên config\settings.json hiện có"
}

& $py -m recap fetch-models
if ($LASTEXITCODE -ne 0) { Warn "Chưa tải được model Whisper — tool sẽ thử tải lại khi chạy video đầu tiên." }

# ---------------------------------------------------------------- 7. OmniVoice GPU
Step "7/8  Cài đặt dịch vụ giọng đọc OmniVoice Clone (GPU)"
if ($hasNvidia) {
    $omniDir = Join-Path $Root "tools\omni_venv"
    $omniPy = Join-Path $omniDir "Scripts\python.exe"
    try {
        & $uv python install 3.12
        if ($LASTEXITCODE -ne 0) { throw "Không cài được Python 3.12 cho OmniVoice." }
        if (-not (Test-Path $omniPy)) {
            & $uv venv $omniDir --python 3.12 --seed
            if ($LASTEXITCODE -ne 0) { throw "Không tạo được tools\omni_venv." }
        }
        Write-Host "    Cài PyTorch CUDA cho OmniVoice (cu128 / dự phòng cu124)…"
        & $uv pip install --python $omniPy torch torchaudio --index-url https://download.pytorch.org/whl/cu128
        if ($LASTEXITCODE -ne 0) {
            Warn "Thử lại PyTorch CUDA 12.4…"
            & $uv pip install --python $omniPy torch torchaudio --index-url https://download.pytorch.org/whl/cu124
            if ($LASTEXITCODE -ne 0) { throw "Không cài được PyTorch CUDA cho OmniVoice." }
        }
        Write-Host "    Cài gói OmniVoice & thư viện âm thanh…"
        & $uv pip install --python $omniPy omnivoice soundfile librosa pydub numpy scipy requests huggingface_hub accelerate transformers safetensors psutil
        if ($LASTEXITCODE -ne 0) { throw "Cài gói OmniVoice bị lỗi." }
        Ok "Môi trường tools\omni_venv sẵn sàng"

        $omniModel = "kjanh/KhanhTTS-OmniVoice"
        $modelTxt = Join-Path $Root "omnivoice\model.txt"
        if (Test-Path $modelTxt) {
            $mTxt = (Get-Content $modelTxt -Raw -Encoding UTF8).Trim()
            if ($mTxt) { $omniModel = $mTxt }
        }
        Write-Host "    Tải sẵn model OmniVoice ($omniModel) vào models\hf…"
        & $omniPy -c "from huggingface_hub import snapshot_download; snapshot_download('$omniModel')"
        if ($LASTEXITCODE -eq 0) {
            Ok "Đã tải sẵn model $omniModel"
        } else {
            Warn "Chưa tải hết model $omniModel — dịch vụ sẽ tự tải khi chạy lần đầu."
        }

        if (Test-Path (Join-Path $Root "START-OMNIVOICE.bat")) {
            Write-Host "    Khởi động ngầm OmniVoice và chờ sẵn sàng (tối đa 45s)…"
            & cmd.exe /c "`"$Root\START-OMNIVOICE.bat`""
            $ready = $false
            for ($w = 0; $w -lt 23; $w++) {
                try {
                    $h = Invoke-RestMethod -Uri "http://127.0.0.1:8766/health" -TimeoutSec 2
                    if ($h.status -eq "ready") { $ready = $true; break }
                    if ($h.status -eq "failed") { break }
                } catch {}
                Start-Sleep -Seconds 2
            }
            if ($ready) { Ok "Dịch vụ OmniVoice GPU đã sẵn sàng (127.0.0.1:8766)" }
            else { Warn "OmniVoice đang nạp model ngầm — có thể mở tool ngay, lần đọc đầu sẽ tự chờ nạp xong." }
        }
    } catch {
        Warn "Chưa cài xong OmniVoice GPU: $($_.Exception.Message). Tool vẫn dùng được Piper / Edge TTS."
    }
} else {
    Write-Host "    Bỏ qua OmniVoice GPU (máy không có GPU NVIDIA — tiết kiệm ~5 GB dung lượng)."
    Write-Host "    Đã đặt giọng mặc định là Piper (có thể đổi sang Edge TTS hoặc VieNeu trong Cài đặt)."
}

# ---------------------------------------------------------------- 8. Dọn cache & Kiểm tra
Step "8/8  Dọn file tạm sau cài đặt & Kiểm tra máy"
Remove-Item (Join-Path $Root "cache\tmp") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $Root "tools\.cache") -Recurse -Force -ErrorAction SilentlyContinue
Ok "Đã dọn cache cài đặt tạm"

& $py -m recap health

Write-Host ""
if (-not (Get-Command codex -ErrorAction SilentlyContinue) -and -not (Get-Command agy -ErrorAction SilentlyContinue) -and -not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Warn "Máy chưa có agent. Cài một trong ba (xem README):"
    Write-Host "      Claude Code: irm https://claude.ai/install.ps1 | iex     rồi gõ:  claude   (đăng nhập Claude)"
    Write-Host "      Codex:       npm install -g @openai/codex     rồi gõ:  codex   (đăng nhập ChatGPT)"
    Write-Host "      Antigravity: irm https://antigravity.google/cli/install.ps1 | iex     rồi gõ:  agy   (đăng nhập Google)"
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Warn "Máy chưa có Git — nút 'Cập nhật tool' sẽ không dùng được. Cài:  winget install Git.Git"
}
Write-Host "`nCài đặt xong. Mở tool bằng run.bat" -ForegroundColor Green
