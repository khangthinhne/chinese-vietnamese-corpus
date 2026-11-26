"""Lời tự nói của mô hình: build_layers loại, checker báo [error] nếu còn sót; checker không báo một nguyên nhân hai lần."""
from src.ocr.ocr_check import Issue, merge_issues
from src.ocr.script_filter import chatter_reason, han_reject_reason, vie_reject_reason


def test_han_chatter_rejected():
    for text in ("作为一个人工智能语言模型，我还没学习如何回答这个问题", "很抱歉，我无法识别", "抱歉",
                 "這里原文本可能存在識別偏差", "As an AI language model 我"):
        assert han_reject_reason(text) == "model_chatter", text
    assert han_reject_reason("行止由天，而非吾人之所能知。") is None


def test_vie_chatter_rejected_but_vietnamese_kept():
    assert vie_reject_reason("As an AI language model, I cannot read this image.") == "model_chatter"
    assert vie_reject_reason("I'm sorry, the text is unclear") == "model_chatter"
    assert vie_reject_reason("Nghĩ lại lời người ta xưng hô mình chỉ thấy mặt dày xấu hổ.") is None
    assert vie_reject_reason("Thach Can dich") is None


def test_chatter_reason_by_layer():
    assert chatter_reason("作为一个人工智能", "han") == "model_chatter"
    assert chatter_reason("作为一个人工智能", "vie") is None
    assert chatter_reason("Here is the transcription:", "vie") == "model_chatter"


def test_merge_issues_one_line_per_page_and_code():
    issues = [Issue(631, "error", "han_degenerate", "đơn vị 5"), Issue(631, "error", "han_degenerate", "đơn vị 9"),
              Issue(692, "warning", "vie_run_no_lines", "run qwen không có dòng nào"),
              Issue(692, "error", "no_text", "mực 0.014"),
              Issue(700, "warning", "vie_run_no_lines", "run qwen không có dòng nào"),
              Issue(679, "error", "han_model_chatter", "1 dòng"), Issue(679, "warning", "han_long_line", "1 dòng > 40")]
    out = merge_issues(issues)
    assert Issue(631, "error", "han_degenerate", "đơn vị 5; đơn vị 9") in out
    assert [i.code for i in out if i.page == 692] == ["no_text"]
    assert [i.code for i in out if i.page == 700] == ["vie_run_no_lines"]
    assert [i.code for i in out if i.page == 679] == ["han_model_chatter"]
    assert len(out) == 4
