"""
Web -> HTML ->  -> RawPage (common/corpus.py) -> XML thô  (không tách câu, không làm sạch chú thích)

Crawl trang web (Wikisource...) -> lưu HTML gốc -> trích text -> XML thô.
 
Quy trình 2 pha (để tái lập được, không phụ thuộc mạng ở lần chạy sau):
    1. FETCH  : tải URL -> data/<WORK_ID>/01_sources/<layer>.html  (bất biến, không sửa)
    2. PARSE  : đọc HTML đã lưu -> RawPage -> XML
 
INPUT (chọn một)
    --url   URL        (lặp lại nhiều lần cho tác phẩm nhiều trang; lưu <layer>_001.html ...)
    --html  file.html  (HTML đã tải sẵn, vd data/CPO_17/01_sources/han.html; không cần mạng)
    --layer han | am | nghia | tho
    --profile NAME     cấu hình selector theo site (mặc định đoán theo domain; xem PROFILES)
    --selector CSS     ghi đè vùng nội dung chính
    --block-selector CSS / --heading-selector CSS / --remove CSS   tinh chỉnh khi site lạ
 
OUTPUT (data/<WORK_ID>/)
    01_sources/<layer>.html       HTML gốc
    02_extraction/<layer>.xml     XML thô; mỗi khối (p/li/dd) = 1 PAGE (#đoạn), mỗi dòng (<br>)
                                  = 1 câu hoặc chưa nhiều câu nha; heading (h2/h3) = SECT mới.
    02_extraction/metadata.json   URL, thời điểm tải, sha256, số đoạn/câu
 
Chỉ trích xuất; KHÔNG tách câu theo dấu câu, KHÔNG làm sạch chú thích (bước 03_cleaned).
 
Thêm site mới: thêm 1 SiteProfile vào PROFILES + 1 dòng vào HOST_PROFILES (không sửa logic).
 
Ví dụ:
    python -m src.extraction.web_extraction --layer han --work-id CPO_17 \
        --url https://zh.wikisource.org/zh-hant/征婦吟
    python -m src.extraction.web_extraction --layer tho --work-id CPO_17 \
        --html data/CPO_17/01_sources/viet.html

IMPORTANT!: Import các hàm đã defined sẵn trong src/common/corpus.py (RawPage, build_document, finalize, register_source)
"""
