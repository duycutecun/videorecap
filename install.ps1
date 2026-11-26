# Cai dat Video Recap Tool FULL tren Windows - chay mot lan (qua install.bat).
# Moi thu (Python, ffmpeg, model Whisper/Piper/OmniVoice) nam gon trong thu muc tool.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "    [!] $msg" -ForegroundColor Yellow }
function Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Die($msg)  { throw $msg }

# Tai file bang curl.exe (thu lai va thu --ssl-no-revoke neu may moi loi kiem tra chung chi)
function Download($urls, $out) {
    foreach ($u in @($urls)) {
        for ($i = 1; $i -le 3; $i++) {
            Write-Host "    Tai: $u (lan $i)"
            if (Test-Path $out) { Remove-Item $out -Force -ErrorAction SilentlyContinue }
            $extra = if ($i -ge 2) { @("--ssl-no-revoke") } else { @() }
            & curl.exe -L --fail --retry 3 --connect-timeout 30 --speed-limit 10000 --speed-time 60 @extra -# -o $out $u
            if ($LASTEXITCODE -eq 0 -and (Test-Path $out) -and (Get-Item $out).Length -gt 1000) { return }
            Warn "Tai chua duoc (ma $LASTEXITCODE), thu lai..."
        }
    }
    Die "Khong tai duoc: $out. Kiem tra ket noi mang roi chay lai."
}

New-Item -ItemType Directory -Force -Path tools, models, "models\hf", jobs, config, "cache\tmp" | Out-Null
$tmpCache = Join-Path $Root "cache\tmp"
$env:UV_CACHE_DIR = Join-Path $Root "tools\.cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Root "tools\python"
$env:HF_HOME = Join-Path $Root "models\hf"
$env:TEMP = $tmpCache
$env:TMP = $tmpCache
$env:UV_LINK_MODE = "copy"

# Phat hien GPU NVIDIA
$gpuName = ""
try {
    $gpuObj = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'NVIDIA' } | Select-Object -First 1
    if ($gpuObj) { $gpuName = [string]$gpuObj.Name }
} catch {}
$hasNvidia = [bool]($gpuName -or (Get-Command nvidia-smi -ErrorAction SilentlyContinue))
if ($hasNvidia -and -not $gpuName) { $gpuName = "NVIDIA GPU" }

Write-Host ""
if ($hasNvidia) {
    Ok "Phat hien GPU: $gpuName - cai dat FULL kem giong OmniVoice GPU & Whisper CUDA"
} else {
    Warn "Khong thay GPU NVIDIA - cai dat che do CPU gon nhe (Whisper small + Piper/Edge)"
}

# ---------------------------------------------------------------- 1. uv
Step "1/8  Cai uv (trinh quan ly Python portable)"
$uvDir = Join-Path $Root "tools\uv"
New-Item -ItemType Directory -Force -Path $uvDir | Out-Null
$uv = Get-ChildItem -Path $uvDir -Recurse -Filter uv.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $uv) {
    $env:UV_INSTALL_DIR = $uvDir
    $env:UV_NO_MODIFY_PATH = "1"
    $env:INSTALLER_NO_MODIFY_PATH = "1"
    try {
        Invoke-RestMethod "https://astral.sh/uv/install.ps1" -ErrorAction Stop | Invoke-Expression
    } catch {
        Warn "Cai uv qua install.ps1 that bai ($($_.Exception.Message)) - thu tai goi zip du phong..."
    }
    $uv = Get-ChildItem -Path $uvDir -Recurse -Filter uv.exe -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $uv) {
    $uvZip = Join-Path $tmpCache "uv.zip"
    Download @("https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip") $uvZip
    & tar.exe -xf $uvZip -C $uvDir
    if ($LASTEXITCODE -ne 0) {
        Expand-Archive -Path $uvZip -DestinationPath $uvDir -Force
    }
    Remove-Item $uvZip -Force -ErrorAction SilentlyContinue
    $uv = Get-ChildItem -Path $uvDir -Recurse -Filter uv.exe -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $uv) { Die "Khong cai duoc uv. Kiem tra ket noi mang roi chay lai." }
$uv = $uv.FullName
if ((Split-Path $uv -Parent) -ne $uvDir) { Copy-Item $uv (Join-Path $uvDir "uv.exe") -Force }
Ok $uv

# ---------------------------------------------------------------- 2. Python 3.11 + .venv
Step "2/8  Cai Python 3.11 portable + moi truong ao (.venv)"
& $uv python install 3.11
if ($LASTEXITCODE -ne 0) { Die "Cai Python 3.11 portable loi (ma $LASTEXITCODE)." }

$venvDir = Join-Path $Root ".venv"
$py = Join-Path $venvDir "Scripts\python.exe"
$venvOk = $false
if (Test-Path $py) {
    & $py -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $venvOk = $true }
}
if (-not $venvOk) {
    if (Test-Path $venvDir) {
        Warn ".venv cu bi hong - dang tao lai..."
        Remove-Item $venvDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    & $uv venv $venvDir --python 3.11 --seed
    if ($LASTEXITCODE -ne 0) { Die "Tao .venv loi (ma $LASTEXITCODE)." }
}
if (-not (Test-Path $py)) { Die "Khong thay $py sau khi tao .venv." }
& $py -c "import sys; print('Python', sys.version.split()[0])"
if ($LASTEXITCODE -ne 0) { Die "Python trong .venv khong chay duoc." }
Ok $py

# ---------------------------------------------------------------- 3. Thu vien Python
Step "3/8  Cai thu vien Python (lan dau co the mat 5-10 phut)"
& $uv pip install --python $py -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { Die "Cai thu vien Python loi (ma $LASTEXITCODE)." }
Ok "Da cai xong thu vien Python"

# ---------------------------------------------------------------- 4. ffmpeg + 7zr
Step "4/8  Cai ffmpeg portable & 7zr"
if (-not (Test-Path (Join-Path $Root "tools\ffmpeg\bin\ffmpeg.exe"))) {
    $zip = Join-Path $Root "tools\ffmpeg.zip"
    Download @(
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    ) $zip
    Write-Host "    Giai nen ffmpeg..."
    $tmp = Join-Path $Root "tools\ffmpeg_tmp"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    & tar.exe -xf $zip -C $tmp
    if ($LASTEXITCODE -ne 0) {
        Expand-Archive $zip -DestinationPath $tmp -Force
    }
    $exe = Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $exe) { Die "File ffmpeg tai ve khong dung." }
    if (Test-Path (Join-Path $Root "tools\ffmpeg")) { Remove-Item (Join-Path $Root "tools\ffmpeg") -Recurse -Force -ErrorAction SilentlyContinue }
    Move-Item (Split-Path (Split-Path $exe.FullName -Parent) -Parent) (Join-Path $Root "tools\ffmpeg")
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
}
Ok (& (Join-Path $Root "tools\ffmpeg\bin\ffmpeg.exe") -hide_banner -version | Select-Object -First 1)

if (-not (Test-Path (Join-Path $Root "tools\7zr.exe"))) {
    try {
        Download @("https://www.7-zip.org/a/7zr.exe") (Join-Path $Root "tools\7zr.exe")
        Ok "tools\7zr.exe"
    } catch {
        Warn "Khong tai duoc 7zr.exe: $($_.Exception.Message). Co the cai 7-Zip sau neu dung LosslessCut."
    }
} else {
    Ok "tools\7zr.exe (da co)"
}

# ---------------------------------------------------------------- 5. Font phu de + Piper (CPU / du phong)
Step "5/8  Cai Font phu de & giong doc du phong (neu can)"
function Install-PiperVoice {
    $pdir = Join-Path $Root "models\piper"
    New-Item -ItemType Directory -Force -Path $pdir | Out-Null
    $base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium"
    try {
        foreach ($ext in @(".onnx", ".onnx.json")) {
            $out = Join-Path $pdir ("vi_VN-vais1000-medium" + $ext)
            if (-not (Test-Path $out)) { Download @($base + $ext + "?download=true") $out }
        }
        Ok "models\piper\vi_VN-vais1000-medium.onnx"
    } catch {
        Warn "Khong tai duoc giong Piper: $($_.Exception.Message). Co the chon giong khac trong Cai dat."
    }
}
if (-not $hasNvidia) {
    Install-PiperVoice
} else {
    Ok "Bo qua tai giong Piper (may co GPU NVIDIA - chi tai giong Hai Dang OmniVoice cho nhe may)"
}

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
        if ($installedFonts -gt 0) { Ok "Da dang ky $installedFonts font phu de vao Windows" }
    } catch {
        Warn "Chua dang ky duoc font vao Windows (tool van tu doc font tu assets\fonts): $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------- 6. Cau hinh ban dau + Whisper
Step "6/8  Thiet lap cau hinh theo phan cung & tai model Whisper"
$cfgFile = Join-Path $Root "config\settings.json"
if (-not (Test-Path $cfgFile)) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    if ($hasNvidia) {
        [System.IO.File]::WriteAllText($cfgFile, "{}", $utf8NoBom)
        Ok "Da tao config\settings.json (mac dinh GPU: OmniVoice + Whisper large-v3-turbo)"
    } else {
        $cpuCfg = '{"tts":{"provider":"piper"},"transcribe":{"model":"small","cpu_model":"small"}}'
        [System.IO.File]::WriteAllText($cfgFile, $cpuCfg, $utf8NoBom)
        Ok "Da tao config\settings.json (mac dinh CPU: Piper + Whisper small)"
    }
} else {
    Ok "Giu nguyen config\settings.json hien co"
}

& $py -m recap fetch-models
if ($LASTEXITCODE -ne 0) { Warn "Chua tai duoc model Whisper - tool se thu tai lai khi chay video dau tien." }

# ---------------------------------------------------------------- 7. OmniVoice GPU
Step "7/8  Cai dat dich vu giong doc OmniVoice Clone (GPU)"
if ($hasNvidia) {
    $omniDir = Join-Path $Root "tools\omni_venv"
    $omniPy = Join-Path $omniDir "Scripts\python.exe"
    try {
        & $uv python install 3.12
        if ($LASTEXITCODE -ne 0) { throw "Khong cai duoc Python 3.12 cho OmniVoice." }
        $omniOk = $false
        if (Test-Path $omniPy) {
            & $omniPy -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) { $omniOk = $true }
        }
        if (-not $omniOk) {
            if (Test-Path $omniDir) { Remove-Item $omniDir -Recurse -Force -ErrorAction SilentlyContinue }
            & $uv venv $omniDir --python 3.12 --seed
            if ($LASTEXITCODE -ne 0) { throw "Khong tao duoc tools\omni_venv." }
        }

        $torchIdx = "https://download.pytorch.org/whl/cu128"
        Write-Host "    Cai PyTorch CUDA cho OmniVoice (cu128 / du phong cu124)..."
        & $uv pip install --python $omniPy torch torchaudio --index-url $torchIdx
        if ($LASTEXITCODE -ne 0) {
            $torchIdx = "https://download.pytorch.org/whl/cu124"
            Warn "Thu lai PyTorch CUDA 12.4..."
            & $uv pip install --python $omniPy torch torchaudio --index-url $torchIdx
            if ($LASTEXITCODE -ne 0) { throw "Khong cai duoc PyTorch CUDA cho OmniVoice." }
        }

        Write-Host "    Cai goi OmniVoice & thu vien am thanh..."
        & $uv pip install --python $omniPy omnivoice soundfile librosa pydub numpy scipy requests huggingface_hub accelerate transformers safetensors psutil --extra-index-url $torchIdx
        if ($LASTEXITCODE -ne 0) { throw "Cai goi OmniVoice bi loi." }

        # Dam bao torch/torchaudio trong omni_venv van la ban CUDA
        & $omniPy -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() or '+cu' in torch.__version__ else 1)" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Warn "Phat hien torch bi doi sang ban CPU - dang cai lai ban CUDA..."
            & $uv pip install --python $omniPy --reinstall-package torch --reinstall-package torchaudio torch torchaudio --index-url $torchIdx
            if ($LASTEXITCODE -ne 0) { throw "Khong khoi phuc duoc PyTorch CUDA cho OmniVoice." }
        }
        Ok "Moi truong tools\omni_venv san sang"

        $omniModel = "kjanh/KhanhTTS-OmniVoice"
        $modelTxt = Join-Path $Root "omnivoice\model.txt"
        if (Test-Path $modelTxt) {
            $mTxt = (Get-Content $modelTxt -Raw -Encoding UTF8).Trim()
            if ($mTxt) { $omniModel = $mTxt }
        }
        Write-Host "    Tai san model OmniVoice ($omniModel) vao models\hf..."
        & $omniPy -c "from huggingface_hub import snapshot_download; snapshot_download('$omniModel')"
        if ($LASTEXITCODE -eq 0) {
            Ok "Da tai san model $omniModel"
        } else {
            Warn "Chua tai het model $omniModel - dich vu se tu tai khi chay lan dau."
        }

        if (Test-Path (Join-Path $Root "START-OMNIVOICE.bat")) {
            Write-Host "    Khoi dong ngam OmniVoice va cho san sang (toi da 45s)..."
            & cmd.exe /c "`"$Root\START-OMNIVOICE.bat`""
            $ready = $false
            for ($w = 0; $w -lt 23; $w++) {
                try {
                    $h = Invoke-RestMethod -Uri "http://127.0.0.1:8766/health" -TimeoutSec 2 -ErrorAction Stop
                    if ($h.status -eq "ready") { $ready = $true; break }
                    if ($h.status -eq "failed") { break }
                } catch {}
                Start-Sleep -Seconds 2
            }
            if ($ready) { Ok "Dich vu OmniVoice GPU da san sang (127.0.0.1:8766)" }
            else { Warn "OmniVoice dang nap model ngam - co the mo tool ngay, lan doc dau se tu cho nap xong." }
        }
    } catch {
        Install-PiperVoice
        Warn "Chua cai xong OmniVoice GPU: $($_.Exception.Message). Tool van dung duoc Piper / Edge TTS."
    }
} else {
    Write-Host "    Bo qua OmniVoice GPU (may khong co GPU NVIDIA - tiet kiem ~5 GB dung luong)."
    Write-Host "    Da dat giong mac dinh la Piper (co the doi sang Edge TTS hoac VieNeu trong Cai dat)."
}

# ---------------------------------------------------------------- 8. Don cache & Kiem tra
Step "8/8  Don file tam sau cai dat & Kiem tra may"
Remove-Item (Join-Path $Root "cache\tmp") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $Root "tools\.cache") -Recurse -Force -ErrorAction SilentlyContinue
Ok "Da don cache cai dat tam"

& $py -m recap health

Write-Host ""
if (-not (Get-Command codex -ErrorAction SilentlyContinue) -and -not (Get-Command agy -ErrorAction SilentlyContinue) -and -not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Warn "May chua co agent. Cai mot trong ba (xem README):"
    Write-Host "      Claude Code: irm https://claude.ai/install.ps1 | iex     roi go:  claude   (dang nhap Claude)"
    Write-Host "      Codex:       npm install -g @openai/codex     roi go:  codex   (dang nhap ChatGPT)"
    Write-Host "      Antigravity: irm https://antigravity.google/cli/install.ps1 | iex     roi go:  agy   (dang nhap Google)"
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Warn "May chua co Git - nut 'Cap nhat tool' se khong dung duoc. Cai:  winget install Git.Git"
}
Write-Host ""
Write-Host "Cai dat xong. Mo tool bang run.bat" -ForegroundColor Green
