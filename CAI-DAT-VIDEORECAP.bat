@echo off
chcp 65001 >nul
setlocal
title VideoRecap - Cai dat FULL tu dong
set "VR_SELF=%~f0"
set "VR_TARGET=%~1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:VR_SELF,[Text.Encoding]::UTF8); $m='<#'+'VR_SETUP'+'#>'; $n='</'+'VR_SETUP'+'>'; $a=$t.IndexOf($m); $e=$t.IndexOf($n); if($a -lt 0 -or $e -le $a){Write-Host '[LOI] File cai dat bi hong.'; Read-Host; exit 1}; iex $t.Substring($a+$m.Length,$e-$a-$m.Length)"
exit /b %errorlevel%
<#VR_SETUP#>
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
try { [Console]::InputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoGit = "https://github.com/duycutecun/videorecap.git"
$RepoZip = "https://github.com/duycutecun/videorecap/archive/refs/heads/main.zip"
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

function Step($m) { Write-Host ""; Write-Host "==== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }
function Die($m)  {
    Write-Host ""
    Write-Host "[LOI] $m" -ForegroundColor Red
    Write-Host "Sua xong cu chay lai file nay, cac phan da cai se duoc giu lai."
    Read-Host "Bam Enter de dong"
    exit 1
}
function Has($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Refresh-EnvPath {
    $extra = @(
        "$env:ProgramFiles\Git\cmd",
        "$env:LOCALAPPDATA\Programs\Git\cmd",
        "$env:ProgramFiles\nodejs",
        "$env:APPDATA\npm",
        "$env:USERPROFILE\.local\bin",
        "$env:LOCALAPPDATA\Programs\antigravity"
    )
    $sysPath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $usrPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($extra + ($sysPath -split ';') + ($usrPath -split ';') + ($env:Path -split ';')) |
             Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
    $env:Path = ($parts -join ';')
}

Refresh-EnvPath

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host "  VIDEORECAP - CAI DAT FULL TU DONG (1-CLICK)" -ForegroundColor Cyan
Write-Host "  Tu tai code tu GitHub -> Python -> FFmpeg -> Whisper -> TTS" -ForegroundColor Cyan
Write-Host "  Neu may co GPU NVIDIA: tu cai luon OmniVoice + 14 giong mau" -ForegroundColor Cyan
Write-Host "===============================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------- 1. Kiem tra may & chon thu muc
Step "1/6  Kiem tra Windows, mang & chon thu muc cai dat"
if (-not (Has "curl.exe")) { Die "Windows thieu curl.exe (can Windows 10 ban 1803 tro len)." }
if (-not (Has "tar.exe"))  { Die "Windows thieu tar.exe (can Windows 10 ban 1803 tro len)." }

& curl.exe -sI -L --connect-timeout 15 --fail "https://github.com/duycutecun/videorecap" >$null 2>&1
if ($LASTEXITCODE -ne 0) {
    Die "Khong ket noi duoc toi GitHub ($RepoGit). Kiem tra mang Wi-Fi/day roi chay lai."
}
Ok "Windows & ket noi GitHub san sang"

$DefaultDir = "C:\VideoRecap"
try {
    $dInfo = New-Object IO.DriveInfo "D:\"
    if ($dInfo.IsReady -and $dInfo.DriveType -eq "Fixed" -and $dInfo.AvailableFreeSpace -ge 30GB) {
        $DefaultDir = "D:\VideoRecap"
    }
} catch {}

$Target = $env:VR_TARGET
if (-not $Target) {
    Write-Host ""
    Write-Host "Chon thu muc cai dat (nhan Enter de chon mac dinh: $DefaultDir)" -ForegroundColor White
    Write-Host " * LUU Y: Khong dung duong dan co dau cach hoac tieng Viet co dau." -ForegroundColor Yellow
    $inp = Read-Host "> "
    $Target = if ($inp -and $inp.Trim()) { $inp.Trim() } else { $DefaultDir }
}
$Target = $Target.Trim('"').TrimEnd('\')
if ($Target -match '[^\x21-\x7E]') {
    Die "Duong dan khong hop le (co dau cach hoac dau tieng Viet): $Target"
}
try {
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
} catch {
    Die "Khong tao duoc thu muc $Target : $($_.Exception.Message)"
}
if (-not (Test-Path $Target)) { Die "Khong tao duoc thu muc $Target" }

try {
    $tDrive = New-Object IO.DriveInfo ([IO.Path]::GetPathRoot($Target))
    $freeGB = [math]::Round($tDrive.AvailableFreeSpace / 1GB, 1)
    Write-Host "[OK] Cai vao: $Target (o $($tDrive.Name) con trong $freeGB GB)" -ForegroundColor Green
    if ($tDrive.AvailableFreeSpace -lt 15GB) {
        Warn "O dia $($tDrive.Name) con it dung luong ($freeGB GB). Ban full GPU can ~12-18 GB."
    }
} catch {}

# ---------------------------------------------------------------- 2. Git for Windows
Step "2/6  Kiem tra / cai dat Git for Windows"
if (-not (Has "git")) {
    if (Has "winget") {
        Write-Host "     Dang cai Git for Windows qua winget..."
        & winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
        Refresh-EnvPath
    }
}
if (Has "git") {
    Ok ("Git: " + (& git --version))
} else {
    Warn "Chua cai duoc Git — se tai ma nguon dang ZIP (nut 'Cap nhat tool' trong app se can Git sau)."
}

# ---------------------------------------------------------------- 3. Tai ma nguon tu GitHub
Step "3/6  Tai ma nguon VideoRecap tu GitHub"
$gotSource = $false
if (Has "git") {
    if (Test-Path (Join-Path $Target ".git")) {
        Write-Host "     Thu muc da la repo Git — dang cap nhat (git pull)..."
        & git -C $Target pull --ff-only
        if ($LASTEXITCODE -eq 0) { $gotSource = $true; Ok "Da cap nhat ban moi nhat qua Git" }
        else { Warn "git pull khong thanh cong, chuyen sang tai goi ZIP cap nhat de len..." }
    } else {
        $existing = Get-ChildItem -Path $Target -Force -ErrorAction SilentlyContinue
        if (-not $existing) {
            Write-Host "     Dang clone repo tu $RepoGit ..."
            & git clone --depth 1 $RepoGit $Target
            if ($LASTEXITCODE -eq 0 -and (Test-Path (Join-Path $Target "install.ps1"))) {
                $gotSource = $true
                Ok "Da tai ma nguon qua Git"
            } else {
                Warn "git clone gap loi, chuyen sang tai goi ZIP..."
            }
        }
    }
}

if (-not $gotSource) {
    $work = Join-Path ([IO.Path]::GetDirectoryName($Target)) ("_vr_tmp_" + [IO.Path]::GetRandomFileName())
    if (-not $work -or $work -eq "_vr_tmp_") { $work = Join-Path $Target "_vr_tmp_dl" }
    $zipFile = Join-Path $work "videorecap.zip"
    $extractDir = Join-Path $work "x"
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
    try {
        Write-Host "     Dang tai ZIP tu $RepoZip ..."
        & curl.exe -L --fail --retry 3 --connect-timeout 30 -# -o $zipFile $RepoZip
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $zipFile)) {
            Die "Tai goi ma nguon tu GitHub that bai."
        }
        Write-Host "     Dang giai nen..."
        & tar.exe -xf $zipFile -C $extractDir
        if ($LASTEXITCODE -ne 0) {
            Expand-Archive -Path $zipFile -DestinationPath $extractDir -Force
        }
        $inst = Get-ChildItem -Path $extractDir -Recurse -Filter "install.ps1" | Select-Object -First 1
        if (-not $inst) { Die "Trong goi tai ve khong thay install.ps1." }
        $srcDir = $inst.DirectoryName
        & robocopy $srcDir $Target /E /NFL /NDL /NJH /NJS /NP /XD jobs .venv tools models cache __pycache__ /XF settings.json >$null
        if ($LASTEXITCODE -ge 8) { Die "Chep file vao $Target that bai (robocopy ma $LASTEXITCODE)." }
        Ok "Da giai nen & cap nhat ma nguon vao $Target"
    } finally {
        Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path (Join-Path $Target "install.ps1"))) {
    Die "Thieu $Target\install.ps1 sau khi tai ma nguon."
}

# ---------------------------------------------------------------- 4. Chay install.ps1
Step "4/6  Cai moi truong Python, FFmpeg, Whisper, Piper, Font & OmniVoice GPU"
Write-Host "     Buoc nay tai model vai GB, vui long giu cua so nay den khi xong." -ForegroundColor Yellow
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Target "install.ps1")
$venvPy = Join-Path $Target ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Die "install.ps1 chua tao duoc .venv\Scripts\python.exe. Xem thong bao loi o tren."
}
Ok "Moi truong VideoRecap da san sang"

# ---------------------------------------------------------------- 5. Agent CLI
Step "5/6  Cai dat & cau hinh Agent viet kich ban"
Refresh-EnvPath
$haveAgents = @("codex", "claude", "agy") | Where-Object { Has $_ }
if ($haveAgents) {
    Ok ("Phat hien agent co san tren may: " + ($haveAgents -join ", "))
} else {
    Warn "May chua co agent nao (can it nhat 1 agent de viet kich ban)."
}

Write-Host ""
Write-Host "  Chon agent muon cai / dat lam mac dinh:" -ForegroundColor White
Write-Host "    1. Codex       (goi ChatGPT / API key OpenAI)"
Write-Host "    2. Claude Code (goi Claude Pro / Max)"
Write-Host "    3. Antigravity (goi Google AI)"
Write-Host "    4. Bo qua      (giu nguyen / tu cai sau)"
$ag = Read-Host "Chon 1-4 (Enter = 4)"
if (-not $ag) { $ag = "4" }

function Ensure-Node {
    if (Has "node") { return $true }
    Write-Host "     Dang cai Node.js LTS..."
    if (Has "winget") {
        & winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
        Refresh-EnvPath
    }
    if (-not (Has "node")) {
        try {
            $v = (Invoke-RestMethod "https://nodejs.org/dist/index.json" | Where-Object lts | Select-Object -First 1).version
            $msi = Join-Path $env:TEMP "node-lts.msi"
            & curl.exe -L --fail -o $msi "https://nodejs.org/dist/$v/node-$v-x64.msi"
            Start-Process msiexec -ArgumentList "/i", "`"$msi`"", "/passive" -Wait
            Remove-Item $msi -Force -ErrorAction SilentlyContinue
            Refresh-EnvPath
        } catch {}
    }
    return (Has "node")
}

$chosenAgent = $null
switch ($ag.Trim()) {
    "1" {
        $chosenAgent = "codex"
        if (Ensure-Node) {
            Write-Host "     Dang cai @openai/codex..."
            & npm install -g @openai/codex
            Refresh-EnvPath
            if (Has "codex") {
                Ok "Da cai Codex. Cua so moi se mo de DANG NHAP (xong go /quit)."
                Start-Process cmd -ArgumentList "/k", "codex"
            } else { Warn "Cai Codex chua thanh cong. Thu chay tay: npm install -g @openai/codex" }
        } else {
            Warn "Chua cai duoc Node.js. Tai tai https://nodejs.org roi chay: npm install -g @openai/codex"
        }
    }
    "2" {
        $chosenAgent = "claude"
        if (-not (Has "git") -and (Has "winget")) {
            & winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
            Refresh-EnvPath
        }
        Write-Host "     Dang cai Claude Code..."
        try {
            Invoke-RestMethod "https://claude.ai/install.ps1" | Invoke-Expression
            Refresh-EnvPath
        } catch {}
        if (Has "claude") {
            Ok "Da cai Claude Code. Cua so moi se mo de DANG NHAP (xong go /exit)."
            Start-Process cmd -ArgumentList "/k", "claude"
        } else {
            Warn "Cai Claude Code chua hoan tat. Thu chay lai trong PowerShell: irm https://claude.ai/install.ps1 | iex"
        }
    }
    "3" {
        $chosenAgent = "agy"
        Write-Host "     Dang cai Antigravity CLI..."
        try {
            Invoke-RestMethod "https://antigravity.google/cli/install.ps1" | Invoke-Expression
            Refresh-EnvPath
        } catch {}
        if (Has "agy") {
            Ok "Da cai Antigravity CLI. Cua so moi se mo de DANG NHAP."
            Start-Process cmd -ArgumentList "/k", "agy"
        } else {
            Warn "Chua thay lenh agy — mo CMD moi roi go: agy"
        }
    }
    default {
        Write-Host "     Bo qua cai them agent."
        $haveNow = @("codex", "claude", "agy") | Where-Object { Has $_ }
        if ($haveNow.Count -eq 1) {
            $chosenAgent = $haveNow[0]
        }
    }
}

if ($chosenAgent) {
    try {
        $cfgFile = Join-Path $Target "config\settings.json"
        $rawJson = if (Test-Path $cfgFile) { [IO.File]::ReadAllText($cfgFile, $Utf8NoBom) } else { "{}" }
        if (-not $rawJson.Trim()) { $rawJson = "{}" }
        $cfgObj = $rawJson | ConvertFrom-Json
        if (-not ($cfgObj.PSObject.Properties.Name -contains "agent") -or $null -eq $cfgObj.agent) {
            $cfgObj | Add-Member -MemberType NoteProperty -Name "agent" -Value ([pscustomobject]@{}) -Force
        }
        $cfgObj.agent | Add-Member -MemberType NoteProperty -Name "provider" -Value $chosenAgent -Force
        [IO.File]::WriteAllText($cfgFile, ($cfgObj | ConvertTo-Json -Depth 10), $Utf8NoBom)
        Ok "Da dat agent mac dinh trong config\settings.json = $chosenAgent"
    } catch {
        Warn "Khong cap nhat duoc agent.provider trong settings.json: $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------- 6. Shortcut & hoan tat
Step "6/6  Tao loi tat VideoRecap ngoai Desktop"
try {
    $desk = [Environment]::GetFolderPath("Desktop")
    $lnkPath = Join-Path $desk "VideoRecap.lnk"
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($lnkPath)
    $sc.TargetPath = Join-Path $Target "run.bat"
    $sc.WorkingDirectory = $Target
    $sc.Description = "VideoRecap Tool"
    $sc.Save()
    Ok "Da tao loi tat VideoRecap ngoai Desktop"
} catch {
    Warn "Khong tao duoc loi tat Desktop — mo truc tiep $Target\run.bat"
}

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Green
Write-Host "  HOAN TAT CAI DAT VIDEORECAP!" -ForegroundColor Green
Write-Host "  Thu muc tool : $Target" -ForegroundColor Green
Write-Host "  Giao dien web: http://127.0.0.1:7860" -ForegroundColor Green
Write-Host "  Lan sau chi can bam loi tat 'VideoRecap' ngoai Desktop." -ForegroundColor Green
Write-Host "===============================================================" -ForegroundColor Green
Write-Host ""

$openNow = Read-Host "Mo VideoRecap ngay bay gio? (Y/n)"
if ($openNow -notmatch "^[nN]") {
    Start-Process -FilePath (Join-Path $Target "run.bat") -WorkingDirectory $Target
}
exit 0
</VR_SETUP>
