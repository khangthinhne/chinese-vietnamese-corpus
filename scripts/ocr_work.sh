#!/usr/bin/env bash
# OCR thô hai luồng (han/vie) cho MỘT khoảng trang của MỘT work, chạy một mạch, resume được.
# Bản bash của scripts/ocr_work.ps1 (Linux, Colab, Git Bash).
#
#   bash scripts/ocr_work.sh CPO_19 5-623
#   DRY_RUN=1 bash scripts/ocr_work.sh CPO_19 5-623            # không trích ảnh/nạp model: trang nào sẽ OCR
#   PACK=1 MEMBER=tv2 bash scripts/ocr_work.sh CPO_19 5-623
#   STEPS="han build check" bash scripts/ocr_work.sh CPO_20 5-806
#
# Biến môi trường:
#   PADDLE_PY  python có transformers>=5 (mặc định .venv-paddle/{bin,Scripts}/python[.exe])
#   QWEN_PY    python của my_nlp (mặc định: $CONDA_PREFIX nếu đang ở my_nlp, rồi `conda env list`)
#   POPPLER    thư mục pdftoppm (mặc định: PATH, hoặc <my_nlp>/Library/bin trên Windows)
#   DATA_ROOT  mặc định data
#   STEPS      mặc định "extract han vie build check"
#   RETRIES    số lần thử lại một bước OCR còn trang lỗi (mặc định 2; lần cuối giảm pixel/ảnh)
set -uo pipefail
usage() {
  echo "Cách dùng: bash scripts/ocr_work.sh <WORK> <PAGES>   vd: bash scripts/ocr_work.sh CPO_19 5-623" >&2
  echo "  (bắt buộc cả WORK và PAGES; không mặc định OCR cả work. Lệnh từng người: docs/ocr_assignment.md)" >&2
  exit 2
}
WORK="${1:-}"
PAGES="${2:-}"
[ -n "$WORK" ] && [ -n "$PAGES" ] || usage
[[ "$PAGES" =~ ^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$ ]] || { echo "PAGES không hợp lệ: $PAGES" >&2; usage; }
cd "$(dirname "$0")/.."
CONFIG="configs/${WORK}.json"
[ -f "$CONFIG" ] || { echo "Không thấy $CONFIG" >&2; exit 2; }
STEPS="${STEPS:-extract han vie build check}"
DATA_ROOT="${DATA_ROOT:-data}"
RETRIES="${RETRIES:-2}"
DRY_RUN="${DRY_RUN:-}"
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
COMMON=(--config "$CONFIG" --data-root "$DATA_ROOT")

ready() {  # engine python -> 0 khi GPU + môi trường + model sẵn sàng
  command -v nvidia-smi >/dev/null || { echo "Không thấy nvidia-smi: OCR cần GPU NVIDIA" >&2; return 3; }
  nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader
  "$2" -m src.ocr.preflight --engine "$1"
}

ocr_layer() {  # layer engine python pixel_var
  local layer=$1 engine=$2 py=$3 pixvar=$4 attempt=0 code
  local args=(-m src.ocr.ocr "${COMMON[@]}" --layer "$layer" --engines "$engine" --pages "$PAGES" --skip-done)
  if [ -n "$DRY_RUN" ]; then "$py" "${args[@]}" --dry-run; return $?; fi
  ready "$engine" "$py" || { echo "[$layer] DỪNG: môi trường/GPU/model chưa sẵn sàng" >&2; return 3; }
  local log="logs/ocr_${WORK}_${layer}_$(stamp).log"
  args+=(--log-file "$log")
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
  [ $code -eq 0 ] || echo "[$layer] còn trang lỗi: xem $log và $DATA_ROOT/$WORK/03_ocr/manifests/ocr_$layer.json" >&2
  return $code
}

echo "=== $WORK, trang $PAGES${DRY_RUN:+ (DRY-RUN)} ==="
if ! "$QWEN_PY" -m src.ocr.work_config --config "$CONFIG" --verify-source; then
  if [ -z "$DRY_RUN" ] && [[ " $STEPS " == *" extract "* ]]; then
    echo "DỪNG: PDF nguồn thiếu hoặc sai bản (SHA-256). Xem docs/ocr_assignment.md." >&2; exit 2
  fi
fi
failed=()
start=$(date +%s)
for step in $STEPS; do
  t0=$(date +%s)
  echo "=== $WORK: $step ==="
  case "$step" in
    extract)
      if [ -n "$DRY_RUN" ]; then echo "(dry-run) bỏ qua trích ảnh"; else
        "$QWEN_PY" -m src.extraction.pdf_extraction "${COMMON[@]}" --pages "$PAGES" \
          ${POPPLER:+--poppler-path "$POPPLER"} || failed+=(extract)
      fi ;;
    han) ocr_layer han paddlevl "$PADDLE_PY" PADDLEVL_MAX_PIXELS_TOKENS || failed+=(han) ;;
    vie) ocr_layer vie qwen "$QWEN_PY" QWEN_MAX_PIXELS_TOKENS || failed+=(vie) ;;
    build)   # mọi trang có raw (luôn chứa PAGES): không làm mất trang của khoảng khác trên cùng máy
      if [ -n "$DRY_RUN" ]; then echo "(dry-run) bỏ qua build"; else
        "$QWEN_PY" -m src.ocr.build_layers "${COMMON[@]}" || failed+=(build)
      fi ;;
    check)
      if [ -n "$DRY_RUN" ]; then echo "(dry-run) bỏ qua check"; else
        "$QWEN_PY" scripts/check_ocr_complete.py --work-id "$WORK" --data-root "$DATA_ROOT" --config "$CONFIG" \
          --pages "$PAGES" --sample 5 --json "$DATA_ROOT/$WORK/03_ocr/ocr_check_${PAGES//,/_}.json" || failed+=(check)
      fi ;;
    *) echo "Bước không hợp lệ: $step" >&2; exit 2 ;;
  esac
  echo "--- $step: $(( ($(date +%s) - t0) / 60 )) phút"
done
if [ -n "${PACK:-}" ] && [ -z "$DRY_RUN" ]; then
  "$QWEN_PY" scripts/pack_ocr.py --work-id "$WORK" --pages "$PAGES" --data-root "$DATA_ROOT" --config "$CONFIG" \
    ${MEMBER:+--member "$MEMBER"} --force || failed+=(pack)
fi
echo "=== $WORK trang $PAGES: xong sau $(( ($(date +%s) - start) / 60 )) phút; bước lỗi: ${failed[*]:-không}"
[ ${#failed[@]} -eq 0 ]
