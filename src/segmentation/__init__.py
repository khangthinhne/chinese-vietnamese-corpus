"""Phân đoạn tài liệu/lớp từ OCR thô theo hệ chữ."""

from src.segmentation.documents import load_writing_system, segment_documents
from src.segmentation.layers import assign_document, metadata_config
from src.segmentation.model import Document, DocumentLayers, LayerLine, OcrLine

__all__ = ["Document", "DocumentLayers", "LayerLine", "OcrLine", "assign_document",
           "load_writing_system", "metadata_config", "segment_documents"]
