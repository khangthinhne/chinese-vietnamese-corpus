@REM OCR
python -m src.ocr.ocr --source data/CPO_18/02_extraction/01_page_image/han --page 205 --layer han --work-id CPO_18 


@REM PDF
python -m src.extraction.pdf_extraction --source "data/CPO_18/01_sources/CPO18.pdf" --page "205" --layer han --work-id CPO_18 --upload sync --drive-sync-dir "data/CPO_18/02_extraction/01_page_image"