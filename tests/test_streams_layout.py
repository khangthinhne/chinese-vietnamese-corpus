"""Hai luồng han/vie: khối Hán không bị cắt đôi (bbox Qwen lệch, dòng rác, tiêu đề chạy OCR sai dấu),
dòng pinyin của Qwen bị loại, chữ khuyết □ được giữ và đổi thành [MASK] ở bước làm sạch."""
from src.cleaning.model import CleanConfig
from src.cleaning.normalization import clean_han_line
from src.ocr.script_filter import filter_han_line, vie_reject_reason
from src.segmentation import streams
from src.segmentation.model import OcrLine


def line(ws, page, idx, text, top):
    return OcrLine(page=page, index=idx, text=text, bbox=(0, top, 100, top + 40), writing_system=ws,
                   source_id=f"{ws}.P{page:04d}.L{idx:03d}")


def test_vie_label_inside_han_block_goes_after_it():
    # CPO_20 tr.626: "Phiên âm:" (y=1766) có mép trên cao hơn câu Hán cuối (y=1798)
    han = [line("han", 626, k, f"漢字句{k}", 1500 + 74 * k) for k in range(1, 5)]
    vie = [line("vie", 626, 1, "Phiên âm:", 1500 + 74 * 3 + 40), line("vie", 626, 2, "Thị Phan gia tử,", 1900)]
    merged = streams.merge_streams(han, vie)
    assert [ln.text for ln in merged] == ["漢字句1", "漢字句2", "漢字句3", "漢字句4", "Phiên âm:", "Thị Phan gia tử,"]


def test_vie_after_real_gap_stays_between_han_blocks():
    han = [line("han", 1, 1, "一二三", 100), line("han", 1, 2, "四五六", 200), line("han", 1, 3, "七八九", 300),
           line("han", 1, 4, "十百千", 900), line("han", 1, 5, "萬億兆", 1000)]
    vie = [line("vie", 1, 1, "Nhất nhị tam", 500)]
    merged = streams.merge_streams(han, vie)
    assert [ln.writing_system for ln in merged] == ["han", "han", "han", "vie", "han", "han"]


def test_running_header_with_ocr_diacritic_error_does_not_split_han_block():
    lines = []
    for p in (10, 11, 12):
        lines.append(line("vie", p, 1, "HOÀNG HOA ĐỒ PHẢ", 10))
        lines.append(line("vie", p, 2, "Nội dung tiếng Việt", 300))
    lines += [line("han", 189, 1, "將軍此地奉金牌", 2500), line("han", 189, 2, "和局成時戰局灰", 2640),
              line("vie", 190, 1, "HOÀNG HOA ĐÔ PHẢ", 13),
              line("han", 190, 2, "昏君實怕長公回", 268), line("han", 190, 3, "塤篪天顯尚如此", 386)]
    starts = streams.allowed_document_starts(lines)
    han_idx = [i for i, ln in enumerate(lines) if ln.writing_system == "han"]
    assert han_idx[0] in starts and han_idx[2] not in starts
    assert streams.header_predicate(lines)(lines[-3])


def test_header_matcher_tolerates_one_letter():
    match = streams.header_matcher({"ngôthìnhậmtoàntậptậpiii"})
    assert match("NGÔ THÌ NHÂN TOÀN TẬP - TẬP III")
    assert not match("Nội dung khác hẳn tiêu đề")


def test_pinyin_line_rejected_vietnamese_kept():
    assert vie_reject_reason("Yān cí zhì zhù jīng") == "pinyin"
    assert vie_reject_reason("máng zhōng ǒu guān wù") == "pinyin"
    assert vie_reject_reason("Lộ nhập Hàm Đan thành,") is None
    assert vie_reject_reason("Thăng Long, Hà Nội") is None


def test_missing_char_kept_in_han_stream():
    assert filter_han_line("講室之□。") == "講室之□。"
    assert filter_han_line("□□□□，") == "□□□□，"
    assert filter_han_line("abc") is None


def test_missing_char_becomes_mask_when_cleaning():
    cfg = CleanConfig(layer="han")
    assert clean_han_line("講室之□。", cfg) == "講室之[MASK]。"
    assert clean_han_line("□□□□，", cfg) == "[MASK][MASK][MASK][MASK]"
    assert clean_han_line("花銀□□，", cfg) == "花銀[MASK][MASK]"


def test_noise_vie_line_does_not_split_han_poem_across_pages():
    lines = [line("han", 217, 1, "推敲不盡意", 2803), line("han", 217, 2, "婦女少娉婷", 2900),
             line("vie", 217, 1, "E", 2950),
             line("han", 218, 1, "繾綣若爲情", 310), line("han", 218, 2, "輸路方馳馬", 432)]
    assert streams.allowed_document_starts(lines) == {0}


def test_pinyin_rule_keeps_vietnamese_notes_with_sanskrit():
    assert vie_reject_reason("(21) Lộc Viên (Mrgadāva): còn phiên âm là Lộc Dã Viên") is None
    assert vie_reject_reason("Zuo pān hóng liú yòu pān lí") == "pinyin"


def test_sentence_cleaner_turns_missing_char_into_mask():
    from src.cleaning.sentence_cleaner import clean_sentence_han
    assert clean_sentence_han("講室之□。") == "講室之[MASK]。"
    assert clean_sentence_han("□□□□，") == "[MASK][MASK][MASK][MASK]，"


def test_vietnamese_model_chatter_rejected_but_editor_text_kept():
    assert vie_reject_reason("Phía trên của ảnh có một đoạn văn bản được viết bằng chữ Hán") == "model_chatter"
    assert vie_reject_reason("Dựa trên thông tin được cung cấp, tôi có thể hiểu rằng") == "model_chatter"
    assert vie_reject_reason("Dòng văn dưới đó có thể được dịch như sau:") == "model_chatter"
    assert vie_reject_reason("Bản giới thiệu dưới đây là trích từ sách nói trên.") is None
    assert vie_reject_reason("và đáp án, chúng tôi không nêu cả.") is None


def test_junk_han_line_at_top_does_not_glue_bottom_han_block_over_vietnamese():
    # CPO_18 tr.492: rác "一" (y=156), đuôi phiên âm + DỊCH NGHĨA của bài trước, 2 dòng Hán bài sau ở cuối trang
    han = [line("han", 492, 1, "一", 156), line("han", 492, 2, "問曰：子云佛經如江海", 2962),
           line("han", 492, 3, "佛經答吾問，而復引詩書", 3081)]
    vie = [line("vie", 492, 1, "hĩ. Ngũ kinh tắc ngũ vị, Phật đạo tắc ngũ cốc hĩ.", 293),
           line("vie", 492, 2, "DỊCH NGHĨA:", 716), line("vie", 492, 3, "Hỏi rằng: Ông dùng Kinh truyện", 923)]
    merged = streams.merge_streams(han, vie)
    assert [ln.writing_system for ln in merged] == ["han", "vie", "vie", "vie", "han", "han"]
    starts = streams.allowed_document_starts(merged)
    assert starts == {4}
