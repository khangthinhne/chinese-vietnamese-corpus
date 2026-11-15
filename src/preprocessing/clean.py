"""Bộ làm sạch (di chuyển sang src/cleaning/) — file này giữ TƯƠNG THÍCH CLI/API cũ.

02_extraction/<layer>.xml -> làm sạch -> tách câu -> 03_cleaned/<layer>.{jsonl,txt,dropped.tsv}
                                                        + {cleaning_audit,footnotes,duplicates,
                                                          reconstruction}.jsonl, review_required.tsv

INPUT (giữ nguyên như cũ)
    --work-id CPO_18 --layer han|am|nghia|tho  [--data-root data]
    --split punct|line     punct: tách theo dấu câu (mặc định han/am/nghia); line: mỗi dòng = 1 đơn vị
                           (mặc định cho tho)
    --split-colon          Việt: coi ':' là ranh giới câu (mặc định tắt vì hay cắt nhầm lời dẫn)
    --drop-pattern REGEX   (lặp lại được) bỏ dòng khớp regex, vd tiêu đề chạy "^Phần II"
    --no-heading-heuristic tắt luật tự bỏ tiêu đề/tên tác giả ngắn không dấu câu
    --keep-markers         không xoá chỉ mục chú thích [1], (1), ①
    --abbrev tp,ts,...     thêm chữ viết tắt Việt không được coi là hết câu khi có dấu chấm
    --start-after REGEX    chỉ lấy nội dung SAU dòng khớp regex này (không gồm dòng khớp)
    --end-before REGEX     ngừng lấy khi gặp dòng khớp regex này (không gồm dòng khớp)

    Mới (mặc định an toàn): --exclude-pages, --no-headers/--no-footers, --no-duplicates,
    --no-reconstruct, --no-footnotes, --footnote-policy exclude|preserve|review,
    --ocr-remove REGEX, --ocr-replace 'REGEX=>THAY_THẾ'. Xem src/cleaning/pipeline.py.

OUTPUT (data/<WORK_ID>/03_cleaned/)
    <layer>.jsonl        mỗi dòng 1 Sentence: id, text, pages, src_ids, flags
    <layer>.txt          mỗi câu 1 dòng (bản "văn bản sạch" theo guideline)
    <layer>.dropped.tsv  mọi dòng bị loại: id, trang, lý do, nội dung -> NGƯỜI PHẢI DUYỆT lại
    cleaning_audit.jsonl    mọi xoá/thay/nối: work, layer, trang, ID, gốc, kết quả, lý do, quy tắc
    footnotes.jsonl         chú thích/trích dẫn nguồn phát hiện được (kể cả nhiều dòng/qua trang)
    duplicates.jsonl        dòng OCR trùng bị bỏ kèm ID của bản giữ
    reconstruction.jsonl    phép nối qua ranh giới trang (nối từ, nối câu, không chắc)
    review_required.tsv     ký tự/trường hợp không chắc: giữ nguyên + trình người duyệt
    02_extraction/metadata.json -> layers["<layer>_cleaned"]

Kiến trúc: 10 bước độc lập trong src/cleaning/ (unicode -> page_filter -> section ->
duplicates -> footnotes -> artifacts -> reconstruction -> normalization -> filtering ->
segmentation). Bản OCR gốc không bao giờ bị sửa; mọi biến đổi truy vết được về ID/trang.

Ví dụ:
    python -m src.preprocessing.clean --work-id CPO_18 --layer han
    python -m src.preprocessing.clean --work-id CPO_18 --layer nghia --drop-pattern "^Phần II"
    python -m src.preprocessing.clean --work-id CPO_18 --layer am \
        --start-after "^Phiên âm" --end-before "^Dịch nghĩa"
"""

from __future__ import annotations

import sys

from src.cleaning.model import (CleanConfig, FootnoteConfig, OCRCleaningConfig, RawLine)  # noqa: F401
from src.cleaning.section import SectionInfo, compile_boundary, extract_section           # noqa: F401
from src.cleaning.normalization import (clean_han_line, clean_vi_line, is_parenthesized)  # noqa: F401
from src.cleaning.filtering import drop_reason                                            # noqa: F401
from src.cleaning.footnotes import detect_footnotes, Footnote, FootnoteDetectionResult    # noqa: F401
from src.cleaning.artifacts import CleaningResult, clean_ocr_artifacts                    # noqa: F401
from src.cleaning.unicode import normalize_unicode                                        # noqa: F401
from src.cleaning.segmentation import (build_stream, make_sentences, split_han, split_vi) # noqa: F401
from src.cleaning.pipeline import (CleanOutput, clean_layer, create_parser, main,         # noqa: F401
                                   read_raw_lines, run)

# hằng số dùng lại từ bản cũ (test/công cụ ngoài có thể import)
from src.cleaning.model import (ASCII_TO_HAN, DEFAULT_DROP, HAN_CLOSERS, HAN_JUNK,      # noqa: F401
                                HAN_PUNCT, HAN_TERMINATORS, LEADING_NUM_RE, MARKER_RE,
                                VI_ABBREV, VI_CLOSERS, VI_OPENERS)

if __name__ == "__main__":
    sys.exit(main())
