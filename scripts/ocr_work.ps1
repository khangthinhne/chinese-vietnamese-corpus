<#
.SYNOPSIS
  OCR thô hai luồng (han/vie) cho MỘT work, chạy một mạch, tiếp tục được (resume).

.DESCRIPTION
  Các bước: extract (trích ảnh nếu thiếu, cần Poppler) -> han (paddlevl, .venv-paddle) -> vie (qwen, my_nlp)
  -> build (han.xml/vie.xml/TXT) -> check (scripts/check_ocr_complete.py) -> pack (tuỳ chọn, -Pack).
  Khoảng trang và hướng chữ Hán lấy từ configs/<Work>.json (khối ocr.streams). Mỗi bước OCR dùng --skip-done:
  chạy lại lệnh này sau khi bị ngắt sẽ chỉ làm các trang còn thiếu. Hai model KHÔNG bao giờ chạy cùng lúc.
  Log: logs/ocr_<Work>_<bước>_<thời điểm>.log

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_19
  powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_20 -Steps han,build,check
  powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_20 -QwenPython "D:\miniconda3\envs\my_nlp\python.exe" -Pack
#>
param(
    [Parameter(Mandatory = $true)][string]$Work,
    [string]$PaddlePython = "",
    [string]$QwenPython = "",
    [string]$PopplerPath = "",
    [string[]]$Steps = @("extract", "han", "vie", "build", "check"),
    [string]$Pages = "auto",
    [int]$Retries = 2,
    [switch]$Pack
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Config = Join-Path "configs" "$Work.json"
if (-not (Test-Path $Config)) { throw "Không thấy $Config (chạy từ gốc repo, -Work CPO_xx)" }
if ($Steps.Count -eq 1 -and $Steps[0] -match ",") { $Steps = $Steps[0].Split(",") }   # -Steps han,build qua -File
New-Item -ItemType Directory -Force logs | Out-Null

function Find-QwenPython {
    if ($QwenPython) { return $QwenPython }
    if ($env:CONDA_PREFIX -and (Split-Path -Leaf $env:CONDA_PREFIX) -eq "my_nlp") {
        return (Join-Path $env:CONDA_PREFIX "python.exe")
    }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"        # conda in cảnh báo ra stderr; PowerShell 5.1 coi đó là lỗi
    try {
        foreach ($line in (& conda env list 2>$null)) {      # "my_nlp   [*]  D:\Program Files\...\envs\my_nlp"
            if ($line -match "^\s*my_nlp\s+(?:[*+]\s+)?(.+?)\s*$") { return (Join-Path $Matches[1] "python.exe") }
        }
    } catch { } finally { $ErrorActionPreference = $prev }
    foreach ($base in @($env:CONDA_EXE, "$env:USERPROFILE\miniconda3\Scripts\conda.exe", "$env:USERPROFILEnaconda3\Scripts\conda.exe")) {
        if ($base) {
            $candidate = Join-Path (Split-Path -Parent (Split-Path -Parent $base)) "envs\my_nlp\python.exe"
            if (Test-Path $candidate) { return $candidate }
        }
    }
    throw "Không tìm thấy môi trường my_nlp; truyền -QwenPython <đường dẫn python.exe>"
}
if (-not $PaddlePython) { $PaddlePython = Join-Path $Root ".venv-paddle\Scripts\python.exe" }
$QwenPython = Find-QwenPython
foreach ($py in @($PaddlePython, $QwenPython)) {
    if (-not (Test-Path $py)) { throw "Không thấy python: $py" }
}
if (-not $PopplerPath) { $PopplerPath = Join-Path (Split-Path -Parent $QwenPython) "Library\bin" }

$env:PYTHONUTF8 = "1"
if (-not $env:HF_HUB_OFFLINE) { $env:HF_HUB_OFFLINE = "1" }   # model đã tải sẵn; tránh lỗi SSL tới huggingface.co

function Stamp { Get-Date -Format "yyyyMMdd_HHmmss" }

function Invoke-Py([string]$Py, [string[]]$PyArgs, [string]$Log) {
    Write-Host ">> $Py $($PyArgs -join ' ')" -ForegroundColor Cyan
    & $Py @PyArgs | Out-Host
    return $LASTEXITCODE
}

function Test-Gpu {
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $smi) { throw "Không thấy nvidia-smi: OCR cần GPU NVIDIA (xem docs/ocr_runbook.md, mục Phần cứng)" }
    & nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader | Out-Host
}

function Invoke-OcrLayer([string]$Layer, [string]$Engine, [string]$Py, [string]$PixelVar) {
    Test-Gpu
    $log = "logs\ocr_${Work}_${Layer}_$(Stamp).log"
    $base = @("-m", "src.ocr.ocr", "--config", $Config, "--layer", $Layer, "--engines", $Engine,
              "--pages", $Pages, "--skip-done", "--log-file", $log)
    $code = Invoke-Py $Py $base $log
    $attempt = 0
    while ($code -ne 0 -and $attempt -lt $Retries) {
        $attempt++
        # thử lại các trang lỗi; lần cuối giảm số pixel/ảnh (OOM). Đổi tham số -> khoá cache mới, trang xong vẫn bỏ qua.
        if ($attempt -eq $Retries) { Set-Item "env:$PixelVar" "768"; Write-Host "Thử lại với $PixelVar=768" }
        Write-Host "[$Layer] lần thử lại $attempt/$Retries" -ForegroundColor Yellow
        $code = Invoke-Py $Py $base $log
    }
    Remove-Item "env:$PixelVar" -ErrorAction SilentlyContinue
    if ($code -ne 0) { Write-Warning "[$Layer] còn trang lỗi sau $Retries lần thử: xem $log và manifests/ocr_$Layer.json" }
    return $code
}

$failed = @()
$started = Get-Date
foreach ($step in $Steps) {
    $t0 = Get-Date
    Write-Host "=== $Work : $step ===" -ForegroundColor Green
    switch ($step) {
        "extract" {
            $range = (& $QwenPython -m src.ocr.work_config --config $Config --get pages).Trim()
            if ($Pages -ne "auto") { $range = $Pages }
            $env:PATH = "$PopplerPath;$env:PATH"
            $code = Invoke-Py $QwenPython @("-m", "src.extraction.pdf_extraction", "--config", $Config, "--pages", $range,
                                           "--poppler-path", $PopplerPath) ""
            if ($code -ne 0) { $failed += $step }
        }
        "han" { if ((Invoke-OcrLayer "han" "paddlevl" $PaddlePython "PADDLEVL_MAX_PIXELS_TOKENS") -ne 0) { $failed += $step } }
        "vie" {
            $env:PATH = "$PopplerPath;$(Split-Path -Parent $QwenPython);$env:PATH"
            if ((Invoke-OcrLayer "vie" "qwen" $QwenPython "QWEN_MAX_PIXELS_TOKENS") -ne 0) { $failed += $step }
        }
        "build" {
            if ((Invoke-Py $QwenPython @("-m", "src.ocr.build_layers", "--config", $Config) "") -ne 0) { $failed += $step }
        }
        "check" {
            $out = "data\$Work\03_ocr\ocr_check.json"
            if ((Invoke-Py $QwenPython @("scripts\check_ocr_complete.py", "--work-id", $Work, "--sample", "5",
                                         "--json", $out) "") -ne 0) { $failed += $step }
        }
        default { throw "Bước không hợp lệ: $step (extract, han, vie, build, check)" }
    }
    Write-Host ("--- {0}: {1:N1} phút" -f $step, ((Get-Date) - $t0).TotalMinutes)
}
if ($Pack) {
    if ((Invoke-Py $QwenPython @("scripts\pack_ocr.py", "--work-id", $Work, "--force") "") -ne 0) { $failed += "pack" }
}
Write-Host ("=== {0}: xong sau {1:N1} phút; bước lỗi: {2}" -f $Work, ((Get-Date) - $started).TotalMinutes,
            ($(if ($failed) { $failed -join ", " } else { "không" })))
if ($failed) { exit 1 }
