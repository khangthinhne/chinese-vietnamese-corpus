# Hán–Việt Alignment: Extraction Pipeline

Turns raw sources (PDF, scanned pages, web pages) into raw XML per text layer, ready for cleaning and alignment.

## Layout

```
src/
  common/corpus.py             shared schema, paths, XML writer, metadata, CLI helpers
  extraction/pdf_extraction.py PDF -> page images (+ optional Google Drive upload)
  extraction/web_extraction.py web page -> raw HTML -> XML
  ocr/ocr.py                   scanned PDF / images -> raw OCR JSON -> XML
configs/CPO_18.json            example shared config
data/<WORK_ID>/
  01_sources/                  original files (never edited)
  02_extraction/               <layer>.xml, metadata.json, 01_page_image/, 02_ocr_raw/
```

Layers: `han`, `am`, `nghia`, `tho`. Run all commands from the repo root.


## Usage

**PDF to page images** (optionally upload to Drive for bulk OCR)

```bash
python -m src.extraction.pdf_extraction --layer han --work-id CPO_18 \
  --source data/CPO_18/01_sources/book.pdf --start 205 --end 400 \
  --upload drive --drive-folder-id <ID> --drive-credentials credentials.json
```

Output: `02_extraction/01_page_image/<layer>/page_205.png`, plus an image manifest (with Drive IDs) in `metadata.json` under `<layer>_images`.

To let Google Drive for desktop sync the images without configuring the Drive API, pass a local folder inside the mounted `My Drive`:

```powershell
python -m src.extraction.pdf_extraction `
  --layer han --work-id OPO18 `
  --source "data/book.pdf" --pages "1-3" `
  --upload sync --drive-sync-dir "G:\My Drive\Corpus"
```

This copies images into `<drive-sync-dir>/<WORK_ID>_<layer>/`; Drive for desktop handles cloud sync.

**Web page to XML**

```bash
python -m src.extraction.web_extraction --layer han --work-id CPO_17 --url <URL>
python -m src.extraction.web_extraction --layer tho --work-id CPO_17 --html data/CPO_17/01_sources/viet.html
```

Output: `01_sources/<layer>.html` (original HTML) and `02_extraction/<layer>.xml`.

**OCR to XML**

```bash
python -m src.ocr.ocr --layer han --work-id CPO_18 \
  --source data/CPO_18/01_sources/book.pdf --pages 205-210 --region 0,0,0.5,1
```

Output: page images, per-page raw OCR in `02_ocr_raw/page_205.json`, `02_extraction/<layer>.xml`. Pages with low confidence are listed in `metadata.json` for review. OCR is a draft and must be checked by a human.

**Shared config**: `--config configs/CPO_18.json --layer han`. Priority: defaults < `common` < `layers.<layer>` < CLI flags.

## Notes

- Existing outputs are not overwritten without `--force`.
- Re-runs resume: existing images, uploads and OCR results (same parameters) are skipped.
- Extraction only: no cleaning, footnote removal or sentence splitting. That belongs to `03_cleaned`.
- To add an OCR engine, subclass `OcrEngine` and use `@register_engine`. To add a website, add a `SiteProfile` in `web_extraction.py`. To add a layer, add a line to `LAYERS` in `corpus.py`.
