#!/usr/bin/env bash
# OCR thô hai luồng (han/vie) cho MỘT work, chạy một mạch, resume được. Bản bash của scripts/ocr_work.ps1
# (Linux, Colab, Git Bash).
#
#   bash scripts/ocr_work.sh CPO_19
#   PADDLE_PY=.venv-paddle/bin/python QWEN_PY=~/miniconda3/envs/my_nlp/bin/python bash scripts/ocr_work.sh CPO_20
#   STEPS="han build check" bash scripts/ocr_work.sh CPO_20
#   PACK=1 bash scripts/ocr_work.sh CPO_20
#
# Biến môi trường:
#   PADDLE_PY  python có transformers>=5 (mặc định .venv-paddle/{bin,Scripts}/python[.exe])
#   QWEN_PY    python của my_nlp (mặc định: $CONDA_PREFIX nếu đang ở my_nlp, rồi `conda env list`)
#   POPPLER    thư mục pdftoppm (mặc định: PATH, hoặc <my_nlp>/Library/bin trên Windows)
#   STEPS      mặc định "extract han vie build check"
#   PAGES      mặc định "auto" (= ocr.streams.pages trong configs/<WORK>.json)
#   RETRIES    số lần thử lại một bước OCR còn trang lỗi (mặc định 2; lần cuối giảm pixel/ảnh)
set -uo pipefail
WORK="${1:?Cách dùng: bash scripts/ocr_work.sh CPO_xx}"
cd "$(dirname "$0")/.."
CONFIG="configs/${WORK}.json"
[ -f "$CONFIG" ] || { echo "Không thấy $CONFIG" >&2; exit 2; }
STEPS="${STEPS:-extract han vie build check}"
PAGES="${PAGES:-auto}"
RETRIES="${RETRIES:-2}"
mkdir -p logs
export PYTHONUTF8=1 HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"

first_existing() { for p in "$@"; do [ -x "$p" ] && { echo "$p"; return 0; }; done; return 1; }
PADDLE_PY="${PADDLE_PY:-$(first_existing .venv-paddle/bin/python .venv-paddle/Scripts/python.exe)}" \
  || { echo "Không thấy .venv-paddle; đặt PADDLE_PY" >&2; exit 2; }
if [ -z "${QWEN_PY:-}" ]; then
  if [ -n "${CONDA_PREFIX:-}" ] && [ "$(basename "$CONDA_PREFIX")" = "my_nlp" ]; then
    QWEN_PY="$(first_existing "$CONDA_PREFIX/bin/python" "$CONDA_PREFIX/python.exe")"
  else
    ENV_DIR="$(conda env list 2>/dev/null | sed -nE 's/^[[:space:]]*my_nlp[[:space:]]+([*+][[:space:]]+)?(.*[^[:space:]])[[:space:]]*$/\2/p' | head -1)"
    [ -n "$ENV_DIR" ] && QWEN_PY="$(first_existing "$ENV_DIR/bin/python" "$ENV_DIR/python.exe")"
  fi
fi
[ -n "${QWEN_PY:-}" ] || { echo "Không tìm thấy môi trường my_nlp; đặt QWEN_PY" >&2; exit 2; }
if [ -z "${POPPLER:-}" ] && ! command -v pdftoppm >/dev/null; then
  POPPLER="$(dirname "$QWEN_PY")/Library/bin"
fi
[ -n "${POPPLER:-}" ] && export PATH="$POPPLER:$PATH"
stamp() { date +%Y%m%d_%H%M%S; }

ocr_layer() {  # layer engine python pixel_var
  local layer=$1 engine=$2 py=$3 pixvar=$4
  command -v nvidia-smi >/dev/null || { echo "Không thấy nvidia-smi: OCR cần GPU NVIDIA" >&2; return 3; }
  nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader
  local log="logs/ocr_${WORK}_${layer}_$(stamp).log" attempt=0 code
  local args=(-m src.ocr.ocr --config "$CONFIG" --layer "$layer" --engines "$engine" --pages "$PAGES"
              --skip-done --log-file "$log")
  "$py" "${args[@]}"; code=$?
  while [ $code -ne 0 ] && [ $attempt -lt "$RETRIES" ]; do
    attempt=$((attempt + 1))
    echo "[$layer] thử lại $attempt/$RETRIES"
    if [ $attempt -eq "$RETRIES" ]; then
      env "$pixvar=768" "$py" "${args[@]}"; code=$?       # lần cuối: ít pixel hơn (OOM)
    else
      "$py" "${args[@]}"; code=$?
    fi
  done
  [ $code -eq 0 ] || echo "[$layer] còn trang lỗi: xem $log và data/$WORK/03_ocr/manifests/ocr_$layer.json" >&2
  return $code
}

failed=()
start=$(date +%s)
for step in $STEPS; do
  t0=$(date +%s)
  echo "=== $WORK: $step ==="
  case "$step" in
    extract)
      range="$PAGES"
      [ "$range" = "auto" ] && range="$("$QWEN_PY" -m src.ocr.work_config --config "$CONFIG" --get pages | tr -d '\r')"
      "$QWEN_PY" -m src.extraction.pdf_extraction --config "$CONFIG" --pages "$range" \
        ${POPPLER:+--poppler-path "$POPPLER"} || failed+=(extract) ;;
    han) ocr_layer han paddlevl "$PADDLE_PY" PADDLEVL_MAX_PIXELS_TOKENS || failed+=(han) ;;
    vie) ocr_layer vie qwen "$QWEN_PY" QWEN_MAX_PIXELS_TOKENS || failed+=(vie) ;;
    build) "$QWEN_PY" -m src.ocr.build_layers --config "$CONFIG" || failed+=(build) ;;
    check) "$QWEN_PY" scripts/check_ocr_complete.py --work-id "$WORK" --sample 5 \
             --json "data/$WORK/03_ocr/ocr_check.json" || failed+=(check) ;;
    *) echo "Bước không hợp lệ: $step" >&2; exit 2 ;;
  esac
  echo "--- $step: $(( ($(date +%s) - t0) / 60 )) phút"
done
if [ -n "${PACK:-}" ]; then "$QWEN_PY" scripts/pack_ocr.py --work-id "$WORK" --force || failed+=(pack); fi
echo "=== $WORK: xong sau $(( ($(date +%s) - start) / 60 )) phút; bước lỗi: ${failed[*]:-không}"
[ ${#failed[@]} -eq 0 ]
