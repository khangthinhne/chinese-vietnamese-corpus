"""So Qwen (vie.xml) với chữ Latin paddlevl trên cùng trang (src/ocr/cross_check.py) + cảnh báo trong ocr_check."""
from src.ocr import ocr_check
from src.ocr.cross_check import compare_page, fold_letters, latin_lines
from tests.test_ocr_check import codes, setup_work

BODY = ["... Thiện Hội (...) xuất gia, tự hiệu Tổ Phong, biến du phương ngoại, cầu học Thiền yếu.",
        "Hậu ngộ Kiến Sơ Cảm Thành tiện ủy sự chi, nhất thập dư niên lược vô quyện sắc.",
        "Thường nhất nhật nhập thất vấn viết: Giáo trung đạo Thích Ca Như Lai nhân địa tu hành."]
# paddlevl đọc cùng đoạn, sai dấu khác Qwen (so sau khi bỏ dấu nên vẫn khớp)
PADDLE = ["... Thiên Hôi (...) xuất gia, tư hiệu Tổ Phong, biến du phương ngoai, câu học Thiên yếu.",
          "Hâu ngộ Kiến Sơ Cảm Thành tiến ủy sự chi, nhất thấp dữ niên lược vô quyện sắc.",
          "Thường nhất nhất nhập thất vấn viết : Giáo trung đào Thích Ca Như Lai nhân địa tu hành."]
INVENTED = 'Một ngày, một người đến gặp một thầy. Người ta hỏi: "Thầy có thể giúp gì cho tôi không?"'


def test_fold_letters_drops_marks_spaces_digits():
    assert fold_letters("Đạo  thực, (12) là!") == "daothucla"


def test_latin_lines_keeps_latin_part_of_mixed_lines():
    run = {"lines": [{"text": "麼生會？師云"}, {"text": "(19) Bản A: 見; Bản B: 㒮"}, {"text": "PHIÊN ÂM:"},
                     {"text": "  "}]}
    lines = latin_lines(run)
    assert len(lines) == 2 and "見" not in lines[0] and "Bản A" in lines[0]


def test_same_text_with_different_marks_scores_high():
    r = compare_page(PADDLE, BODY)
    assert r["precision"] > 0.9 and r["recall"] > 0.9 and r["unsupported"] == []


def test_invented_line_is_unsupported_even_when_page_score_is_high():
    r = compare_page(PADDLE, [BODY[0], INVENTED, *BODY[1:]])
    assert [u["line"] for u in r["unsupported"]] == [2]
    assert r["recall"] > 0.9


def test_omission_lowers_recall_not_precision():
    notes = ["1. Nguyên dẫn: Ngày mùng một nhà vua từ Thanh Hà tiến ra, buổi trưa tới đồn Doanh Ngói.",
             "2. Thái thốc: Một trong mười hai ống luật nói trong Kinh Lễ, thuộc về tháng giêng."]
    r = compare_page(PADDLE + notes, BODY)
    assert r["precision"] > 0.9 and r["recall"] < 0.7


def test_too_little_text_is_not_compared():
    assert compare_page(["Phần II"], BODY) is None
    assert compare_page(PADDLE, ["Trời"]) is None


def test_check_work_warns_but_does_not_block(tmp_path):
    layout = setup_work(tmp_path, [1, 2],
                        han={1: ["天地玄黃宇宙洪荒", *PADDLE], 2: ["天地玄黃宇宙洪荒", *PADDLE]},
                        vie={1: BODY, 2: [BODY[0], INVENTED, *BODY[1:]]})
    report = ocr_check.check_work(layout, "1-2")
    assert report["complete"], codes(report, "error")
    assert (2, "vie_unconfirmed_line") in codes(report, "warning")
    assert (1, "vie_unconfirmed_line") not in codes(report, "warning")
    assert report["pages"][0]["vie_cross"]["precision"] > 0.9
