"""
PDF -> image -> Drive
Tách PDF thành ảnh từng trang (+ upload Google Drive) để OCR hàng loạt.
 
INPUT
    --source  file .pdf (nếu nằm ngoài 01_sources sẽ được copy vào, giữ nguyên bản gốc)
    --layer   han | am | nghia | tho   (mỗi lớp có thư mục ảnh riêng, tránh trùng tên trang)
    --start N --end M   trang bắt đầu / kết thúc (1-based, mặc định 1 .. hết)
    --pages "205-210,212"   thay thế cho --start/--end khi cần chọn trang rời rạc
    --dpi 300  --workers 4  --format png|jpg
    --upload none|drive     (mặc định none: chỉ lưu ảnh local)
    --drive-folder-id ID    thư mục cha trên Google Drive (bắt buộc khi --upload drive)
    --drive-credentials FILE  OAuth client secret JSON hoặc service-account JSON
    --drive-token FILE        nơi lưu token OAuth (mặc định drive_token.json)
 
OUTPUT (data/<WORK_ID>/)
    02_extraction/01_page_image/<layer>/page_0205.png   ảnh từng trang, tên số cố định 4 chữ số
    Google Drive: <drive-folder>/<WORK_ID>_<layer>/page_0205.png   (khi --upload drive)
    02_extraction/metadata.json -> layers["<layer>_images"]:
        nguồn + sha256, tổng số trang PDF, tham số, danh sách ảnh
        [{"page", "file", "drive_id", "drive_url"}]  <- dùng làm manifest cho OCR hàng loạt
 
Chạy lại là tiếp tục: ảnh đã có / đã upload (theo manifest) được bỏ qua; --force để làm lại.
Việc OCR không làm ở đây: lấy ảnh từ Drive (hoặc thư mục local) đưa vào src/ocr/ocr.py
hoặc notebook OCR.
 
Ví dụ:
    python -m src.extraction.pdf_extraction --layer han --work-id CPO_18 \
        --source data/CPO_18/01_sources/book.pdf --start 205 --end 400
    python -m src.extraction.pdf_extraction --layer han --work-id CPO_18 \
        --source data/CPO_18/01_sources/book.pdf --start 205 --end 400 \
        --upload drive --drive-folder-id <ID> --drive-credentials credentials.json
    python -m src.extraction.pdf_extraction --config configs/CPO_18.json --layer nghia
 
Cần: poppler (pdftoppm), pdf2image; upload cần google-api-python-client, google-auth-oauthlib.
 
IMPORTANT!: Dùng các hàm đã defined sẵn trong src/common/corpus.py (parse_pages, DataLayout,
register_source, MetadataStore, base_parser, parse_args). Việc tách ảnh không sinh XML nên
không dùng build_document/finalize/RawPage/parse_region/filter_lines.
"""