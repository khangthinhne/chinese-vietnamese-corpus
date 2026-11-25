<#
.SYNOPSIS
  OCR thô hai luồng (han/vie) cho MỘT khoảng trang của MỘT work, chạy một mạch, tiếp tục được (resume).

.DESCRIPTION
  BẮT BUỘC -Work và -Pages (vd -Work CPO_19 -Pages 5-623). Không có mặc định "cả work".
  Các bước (mặc định tất cả): extract (trích ảnh của Pages, cần Poppler) -> han (paddlevl, .venv-paddle)
  -> vie (qwen, my_nlp) -> build (han.xml/vie.xml/TXT) -> check (chỉ trong Pages) -> pack (tuỳ chọn, -Pack).
  Hướng chữ Hán lấy từ configs/<Work>.json (ocr.streams). Mỗi bước OCR dùng --skip-done: chạy lại đúng lệnh này
  sau khi bị ngắt chỉ làm trang còn thiếu; trang đã có trong gói hạt giống không bị OCR lại.
  Hai model KHÔNG bao giờ chạy cùng lúc. Log: logs/ocr_<Work>_<bước>_<thời điểm>.log
  -DryRun: không trích ảnh, không nạp model; in trang nào đã xong / sẽ OCR và ước lượng thời gian.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_19 -Pages 5-623
  powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_19 -Pages 5-623 -DryRun
  powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_19 -Pages 5-623 -Pack -Member tv2
  powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_20 -Pages 5-806 -Steps han,build,check
#>
param(
    [string]$Work = "",
    [string]$Pages = "",
    [string]$PaddlePython = "",
    [string]$QwenPython = "",
    [string]$PopplerPath = "",
    [string]$DataRoot = "data",
    [string[]]$Steps = @("extract", "han", "vie", "build", "check"),
    [int]$Retries = 2,
    [switch]$DryRun,
    [switch]$Pack,
    [string]$Member = ""
)

$ErrorActionPreference = "Stop"
$Usage = @"
Cách dùng (từ gốc repo):
  powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_19 -Pages 5-623 [-DryRun] [-Pack -Member tv2]
Tuỳ chọn: -Steps extract,han,vie,build,check  -PaddlePython <python .venv-paddle>  -QwenPython <python my_nlp>
          -PopplerPath <thư mục pdftoppm>  -DataRoot data  -Retries 2
Lệnh của từng thành viên: docs\ocr_assignment.md
"@
if (-not $Work -or -not $Pages) {
    Write-Host "Thiếu -Work hoặc -Pages (bắt buộc, không mặc định OCR cả work)." -ForegroundColor Red
    Write-Host $Usage
    exit 2
}
if ($Pages -notmatch '^\d+(-\d+)?(,\d+(-\d+)?)*$') {
    Write-Host "-Pages không hợp lệ: '$Pages' (ví dụ 5-623 hoặc 213,214)" -ForegroundColor Red
    Write-Host $Usage
    exit 2
}
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Config = Join-Path "configs" "$Work.json"
if (-not (Test-Path $Config)) { Write-Host "Không thấy $Config (chạy từ gốc repo, -Work CPO_xx)" -ForegroundColor Red; exit 2 }
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
    foreach ($base in @($env:CONDA_EXE, "$env:USERPROFILE\miniconda3\Scripts\conda.exe", "$env:USERPROFILE\anaconda3\Scripts\conda.exe")) {
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
    if (-not (Test-Path $py)) { Write-Host "Không thấy python: $py (xem docs\ocr_runbook.md mục 3)" -ForegroundColor Red; exit 2 }
}
if (-not $PopplerPath) { $PopplerPath = Join-Path (Split-Path -Parent $QwenPython) "Library\bin" }

$env:PYTHONUTF8 = "1"
if (-not $env:HF_HUB_OFFLINE) { $env:HF_HUB_OFFLINE = "1" }   # model đã tải sẵn; tránh lỗi SSL tới huggingface.co

function Stamp { Get-Date -Format "yyyyMMdd_HHmmss" }

function Invoke-Py([string]$Py, [string[]]$PyArgs) {
    Write-Host ">> $Py $($PyArgs -join ' ')" -ForegroundColor Cyan
    & $Py @PyArgs | Out-Host
    return $LASTEXITCODE
}

function Test-Ready([string]$Engine, [string]$Py) {
    # GPU + đúng môi trường + model đã tải. Thiếu -> dừng rõ ràng (mã 3), không OCR.
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $smi) { Write-Host "Không thấy nvidia-smi: OCR cần GPU NVIDIA (docs\ocr_runbook.md, mục Phần cứng)" -ForegroundColor Red; return 3 }
    & nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader | Out-Host
    return (Invoke-Py $Py @("-m", "src.ocr.preflight", "--engine", $Engine))
}

$Common = @("--config", $Config, "--data-root", $DataRoot)

function Invoke-OcrLayer([string]$Layer, [string]$Engine, [string]$Py, [string]$PixelVar) {
    $base = @("-m", "src.ocr.ocr") + $Common + @("--layer", $Layer, "--engines", $Engine, "--pages", $Pages, "--skip-done")
    if ($DryRun) { return (Invoke-Py $Py ($base + @("--dry-run"))) }
    $ready = Test-Ready $Engine $Py
    if ($ready -ne 0) { Write-Host "[$Layer] DỪNG: môi trường/GPU/model chưa sẵn sàng (xem thông báo ở trên)" -ForegroundColor Red; return $ready }
    $log = "logs\ocr_${Work}_${Layer}_$(Stamp).log"
    $base += @("--log-file", $log)
    $code = Invoke-Py $Py $base
    $attempt = 0
    while ($code -ne 0 -and $attempt -lt $Retries) {
        $attempt++
        # thử lại các trang lỗi; lần cuối giảm số pixel/ảnh (OOM). Đổi tham số -> khoá cache mới, trang xong vẫn bỏ qua.
        if ($attempt -eq $Retries) { Set-Item "env:$PixelVar" "768"; Write-Host "Thử lại với $PixelVar=768" }
        Write-Host "[$Layer] lần thử lại $attempt/$Retries" -ForegroundColor Yellow
        $code = Invoke-Py $Py $base
    }
    Remove-Item "env:$PixelVar" -ErrorAction SilentlyContinue
    if ($code -ne 0) { Write-Warning "[$Layer] còn trang lỗi sau $Retries lần thử: xem $log và $DataRoot\$Work\03_ocr\manifests\ocr_$Layer.json" }
    return $code
}

Write-Host "=== $Work, trang $Pages$(if ($DryRun) { ' (DRY-RUN)' }) ===" -ForegroundColor Green
$srcCode = Invoke-Py $QwenPython @("-m", "src.ocr.work_config", "--config", $Config, "--verify-source")
if ($srcCode -ne 0 -and ($Steps -contains "extract") -and -not $DryRun) {
    Write-Host "DỪNG: PDF nguồn thiếu hoặc sai bản (SHA-256). Xem docs\ocr_assignment.md." -ForegroundColor Red
    exit 2
}

$failed = @()
$started = Get-Date
foreach ($step in $Steps) {
    $t0 = Get-Date
    Write-Host "=== $Work : $step ===" -ForegroundColor Green
    switch ($step) {
        "extract" {
            if ($DryRun) { Write-Host "(dry-run) bỏ qua trích ảnh; trang thiếu ảnh hiện là 'no_image'"; break }
            $env:PATH = "$PopplerPath;$env:PATH"
            $code = Invoke-Py $QwenPython (@("-m", "src.extraction.pdf_extraction") + $Common + @("--pages", $Pages,
                                           "--poppler-path", $PopplerPath))
            if ($code -ne 0) { $failed += $step }
        }
        "han" { if ((Invoke-OcrLayer "han" "paddlevl" $PaddlePython "PADDLEVL_MAX_PIXELS_TOKENS") -ne 0) { $failed += $step } }
        "vie" {
            $env:PATH = "$PopplerPath;$(Split-Path -Parent $QwenPython);$env:PATH"
            if ((Invoke-OcrLayer "vie" "qwen" $QwenPython "QWEN_MAX_PIXELS_TOKENS") -ne 0) { $failed += $step }
        }
        "build" {
            if ($DryRun) { Write-Host "(dry-run) bỏ qua build"; break }
            # Dựng MỌI trang có raw (luôn chứa Pages): không làm mất trang của lần chạy/khoảng khác trên cùng máy.
            if ((Invoke-Py $QwenPython (@("-m", "src.ocr.build_layers") + $Common)) -ne 0) { $failed += $step }
        }
        "check" {
            if ($DryRun) { Write-Host "(dry-run) bỏ qua check"; break }
            $out = "$DataRoot\$Work\03_ocr\ocr_check_$($Pages -replace ',', '_').json"
            if ((Invoke-Py $QwenPython @("scripts\check_ocr_complete.py", "--work-id", $Work, "--data-root", $DataRoot,
                                         "--config", $Config, "--pages", $Pages, "--sample", "5", "--json", $out)) -ne 0) { $failed += $step }
        }
        default { Write-Host "Bước không hợp lệ: $step (extract, han, vie, build, check)" -ForegroundColor Red; exit 2 }
    }
    Write-Host ("--- {0}: {1:N1} phút" -f $step, ((Get-Date) - $t0).TotalMinutes)
}
if ($Pack -and -not $DryRun) {
    $packArgs = @("scripts\pack_ocr.py", "--work-id", $Work, "--pages", $Pages, "--data-root", $DataRoot, "--config", $Config, "--force")
    if ($Member) { $packArgs += @("--member", $Member) }
    if ((Invoke-Py $QwenPython $packArgs) -ne 0) { $failed += "pack" }
}
Write-Host ("=== {0} trang {1}: xong sau {2:N1} phút; bước lỗi: {3}" -f $Work, $Pages, ((Get-Date) - $started).TotalMinutes,
            ($(if ($failed) { $failed -join ", " } else { "không" })))
if ($failed) { exit 1 }
