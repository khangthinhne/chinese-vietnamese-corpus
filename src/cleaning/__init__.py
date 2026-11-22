"""Bộ làm sạch module-hoá: 02_extraction/<layer>.xml -> 03_cleaned/<layer>.{jsonl,txt,tsv} + audit.

Thứ tự các bước (xem cleaning/pipeline.py):
    unicode -> page_filter -> section -> duplicates -> footnotes -> artifacts
    -> reconstruction -> normalization -> filtering -> segmentation

Mỗi bước độc lập, nhận list[RawLine] + config + Audit, trả về (dòng giữ, bản ghi)
— dễ test riêng và truy vết được mọi biến đổi về ID/trang nguồn.
"""

from __future__ import annotations

from src.cleaning.model import (CleanConfig, FootnoteConfig, OCRCleaningConfig, RawLine,
                                has_flag)
from src.cleaning.audit import Audit
from src.cleaning.unicode import normalize_unicode
from src.cleaning.page_filter import filter_pages
from src.cleaning.section import SectionInfo, compile_boundary, extract_section
from src.cleaning.duplicates import detect_duplicates
from src.cleaning.footnotes import (Footnote, FootnoteDetectionResult, detect_footnotes)
from src.cleaning.artifacts import (CleaningResult, OCRCleaningConfig as _OCRCfg,  # noqa: F401
                                    clean_lines, clean_ocr_artifacts)
from src.cleaning.reconstruction import reconstruct_page_boundaries
from src.cleaning.normalization import clean_han_line, clean_vi_line, is_parenthesized
from src.cleaning.filtering import drop_reason, filter_invalid_lines
from src.cleaning.segmentation import (build_stream, make_sentences, split_han, split_vi)
from src.cleaning.metadata import (DocumentMetadata, MetadataConfig, MetadataOccurrence,  # noqa: F401
                                   MetadataResult, aggregate_document_metadata,
                                   aggregate_file_metadata, extract_metadata,
                                   write_layer_metadata)
from src.cleaning.pipeline import (CleanOutput, StructuralCleanOutput, clean_layer, create_parser, main,  # noqa: F401
                                   read_raw_lines, run, run_cleaning, run_structural_cleaning)

__all__ = ["CleanConfig", "RawLine", "Audit", "normalize_unicode", "filter_pages",
           "extract_section", "SectionInfo", "detect_duplicates", "detect_footnotes",
           "clean_ocr_artifacts", "clean_lines", "reconstruct_page_boundaries",
           "clean_han_line", "clean_vi_line", "drop_reason", "filter_invalid_lines",
           "build_stream", "split_han", "split_vi", "make_sentences", "clean_layer",
           "run", "main", "create_parser", "read_raw_lines", "is_parenthesized",
           "MetadataConfig", "extract_metadata", "MetadataResult", "DocumentMetadata",
           "MetadataOccurrence", "aggregate_document_metadata", "aggregate_file_metadata",
           "write_layer_metadata", "StructuralCleanOutput", "run_structural_cleaning"]
