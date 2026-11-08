"""
Image -> OCR JSON -> RawPage (common/corpus.py) -> XML thô (không tách câu, không làm sạch chú thích)

OCR PDF scan / thư mục ảnh -> ảnh trang + OCR thô (JSON) -> XML thô.
 
INPUT
    --source  thư mục ảnh (tên chứa số trang:  page_205.jpg...)
    --layer   han | am | nghia | tho   (quyết định ngôn ngữ OCR mặc định: han=chi_tra, còn lại=vie)
    --pages   "205-210,212"            (bắt buộc nên chỉ định để tránh OCR cả cuốn)
    --engines tesseract[,tên khác]     engine đăng ký trong ENGINES (mặc định: tesseract)
    --select  first | best_conf        cách chọn kết quả cuối khi chạy nhiều engine
    --lang    mã ngôn ngữ OCR          (vd chi_tra, chi_tra_vert, vie)
    --psm     page segmentation mode   (6 = khối văn bản; 5 = chữ dọc)
    --region  x0,y0,x1,y1  (0..1)      chỉ OCR một vùng/cột của trang
    --keep    cjk|latin|none           lọc dòng theo chữ viết (mặc định theo lớp)
    --dpi 300  --workers 4  --low-conf 60
 
OUTPUT (data/<WORK_ID>/02_extraction/)
    01_page_image/page_205.png   ảnh trang gốc (dựng từ PDF), số trang tối thiểu 3 chữ số
    02_ocr_raw/page_205.json     OCR thô của TỪNG engine: từng dòng + bbox + độ tin cậy
                                  (để truy vết / bỏ phiếu / hiệu đính — KHÔNG bị ghi đè khi chạy lại
                                  với cùng tham số; đổi tham số thì tự OCR lại)
    <layer>.xml                   XML thô (STATUS: Raw extraction)
    metadata.json                 nguồn, tham số, trang độ tin cậy thấp cần người rà soát
 
Kết quả OCR chỉ là bản nháp; metadata.json -> "low_conf_pages" liệt kê trang cần kiểm tra trước.
 
Mở rộng engine (PaddleOCR, Google Vision, VietOCR...): viết lớp kế thừa OcrEngine và gắn
@register_engine — không cần sửa phần còn lại. Bỏ phiếu nhiều engine: thêm hàm vào SELECTORS
(raw JSON đã lưu đủ output từng engine nên có thể bỏ phiếu lại mà không OCR lại).
 
Ví dụ:
    python -m src.ocr.ocr --layer han --work-id CPO_18 \
        --source data/CPO_18/01_sources/book.pdf --pages 205-210 --region 0,0,0.5,1

IMPORTANT!: Import các hàm đã defined sẵn trong src/common/corpus.py (parse_pages, parse_region,....)
Import các hàm bên dưới:
LAYERS	            Lấy cấu hình mặc định của từng lớp: han, am, nghia, tho
DataLayout	        Quản lý đường dẫn thư mục và file
register_source()	Đăng ký nguồn PDF hoặc thư mục ảnh
parse_pages()	    Phân tích phạm vi trang cần OCR
parse_region()	    Phân tích vùng ảnh cần OCR
filter_lines()	    Lọc dòng theo chữ viết
RawPage	            Chuẩn hóa kết quả OCR thành dữ liệu trung gian
make_meta()	        Tạo metadata cho lớp văn bản
build_document()	Dựng cấu trúc Document từ RawPage
finalize()	    Validate, ghi XML và cập nhật metadata
"""
