"""Kiểm thử đầu-cuối bằng dữ liệu GIẢ LẬP mô phỏng đầu ra OCR (nhiễu giống ảnh trang 205 CPO_18).

Chạy từ thư mục gốc repo:  python -m pytest tests -q
Âm Hán-Việt/dịch nghĩa trong fixture do người viết test tự soạn, chỉ để thử code, không phải dữ liệu thật.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.alignment import align, export                       # noqa: E402
from src.common.corpus import (DataLayout, RawPage, build_document, make_meta,  # noqa: E402
                               write_xml)
from src.common.sentences import load_sentences                 # noqa: E402
from src.alignment.serialization import read_alignments          # noqa: E402
from src.preprocessing import clean                              # noqa: E402

WORK = "T_01"

HAN_PAGES = {
    205: ["蕤鱒草", "1· 與高明二法師雙佛不見形書 |", "李淼", "— * 流炳交州刺史",
          "夫道處清虛 , 四大理常 , 而有法門 , 妙出群", "域。若稱其功能利物 , 度脫無量爲教 , 何居不見",
          "真形於世 ？ 真空說而無實耶 ？", "今正就尋西方根源 , 伏願大和尚垂懷 , 允納",
          "( 『弘明集』卷十一 = 見 ” 全上古三代秦漢三國公麴文 , 全宅文卻五十七 )"],
    206: ["下心 , 無惜神語 。", "弟子李淼和南 。"],
}
AM_PAGES = {
    207: ["Phần II: Nguyên văn tác phẩm dịch và chú thích",
          "Phu đạo xứ thanh hư, tứ đại lý thường, nhi hữu pháp môn, diệu xuất quần",   # rớt dấu: Phu
          "vực. Nhược xưng kỳ công năng lợi vật, độ thoát vô lượng vi giáo, hà cư bất kiến [1]",
          "* Chú thích: theo Hoằng minh tập."],
    208: ["chân hình ư thế? Chân không thuyết nhi vô thực da?",
          "Kim chính tựu tầm tây phương căn nguyên, phục nguyện đại hòa thượng thùy hoài, doãn nạp",
          "hạ tâm, vô tích thần ngữ. Đệ tử Lý Sâm hòa nam."],
}
NGHIA_PAGES = {
    209: ["Phần II: Nguyên văn tác phẩm dịch và chú thích",
          "Đạo vốn ở nơi thanh tịnh hư vô, bốn đại có lẽ thường, lại có pháp môn, huyền diệu vượt hơn mọi cõi.",
          "Nếu nói công năng của nó là lợi ích muôn vật, độ thoát vô lượng chúng sinh làm giáo hóa, vì sao",
          "không thấy hình thật hiện ra nơi đời? Chân không chỉ là lời nói mà không có thực chăng?"],
    210: ["Nay xin đi tìm cội nguồn phương Tây, mong đại hòa thượng rủ lòng, nhận lời thỉnh cầu, chớ tiếc lời thiêng.",
          "Đệ tử Lý Sâm xin kính lễ."],
}


def write_layer(root: Path, layer: str, pages: dict[int, list[str]]) -> None:
    layout = DataLayout(root, WORK)
    layout.ensure()
    doc = build_document(WORK, layer, make_meta(layer, title="fixture", method="OCR: fixture"),
                         [RawPage(p, lines) for p, lines in pages.items()])
    write_xml(doc, layout.layer_xml(layer), force=True)


def make_dict(path: Path) -> None:
    """Từ điển Hán-Việt mini dựng từ chính cặp chữ-âm chuẩn của fixture (đủ để thử thuật toán)."""
    han = "夫道處清虛四大理常而有法門妙出群域若稱其功能利物度脫無量爲教何居不見真形於世空說實耶今正就尋西方根源伏願和尚垂懷允納下心惜神語弟子李淼南"
    am = ("phù đạo xứ thanh hư tứ đại lý thường nhi hữu pháp môn diệu xuất quần vực nhược xưng kỳ công năng lợi vật độ "
          "thoát vô lượng vi giáo hà cư bất kiến chân hình ư thế không thuyết thực da kim chính tựu tầm tây phương "
          "căn nguyên phục nguyện hòa thượng thùy hoài doãn nạp hạ tâm tích thần ngữ đệ tử lý sâm nam").split()
    # ghép theo thứ tự xuất hiện chữ Hán duy nhất -> âm; kiểm tra độ dài khớp để test không âm thầm sai
    uniq = list(dict.fromkeys(han))
    assert len(uniq) == len(am), (len(uniq), len(am))
    path.write_text("".join(f"{c}\t{r}\n" for c, r in zip(uniq, am)), encoding="utf-8")


@pytest.fixture()
def root(tmp_path):
    write_layer(tmp_path, "han", HAN_PAGES)
    write_layer(tmp_path, "am", AM_PAGES)
    write_layer(tmp_path, "nghia", NGHIA_PAGES)
    make_dict(tmp_path / "han_viet.tsv")
    return tmp_path


def run_clean(root, layer, *extra):
    argv = ["--work-id", WORK, "--layer", layer, "--data-root", str(root), "--force", *extra]
    assert clean.main(argv) == 0


def texts(root, layer):
    return [s.text for s in load_sentences(DataLayout(root, WORK), layer)]


# --------------------------------------------------------------------------- #
def test_clean_han_cross_page_and_noise(root):
    # văn xuôi có tiêu đề/tên người viết ngắn: luật tiêu đề Hán là tuỳ chọn (mặc định tắt vì phá thơ)
    run_clean(root, "han", "--han-heading-heuristic")
    assert texts(root, "han") == [
        "夫道處清虛，四大理常，而有法門，妙出群域。",
        "若稱其功能利物，度脫無量爲教，何居不見真形於世？",
        "真空說而無實耶？",
        "今正就尋西方根源，伏願大和尚垂懷，允納下心，無惜神語。",
        "弟子李淼和南。",
    ]
    sents = load_sentences(DataLayout(root, WORK), "han")
    assert sents[3].pages == [205, 206] and "cross_page" in sents[3].flags
    dropped = (root / WORK / "05_cleaning" / "han.dropped.tsv").read_text(encoding="utf-8")
    assert dropped.count("\theading\t") == 4 and dropped.count("\tfootnote\t") == 1


def test_clean_vi_markers_header_footnote(root):
    run_clean(root, "am", "--drop-pattern", "^Phần II")
    got = texts(root, "am")
    assert len(got) == 5
    assert got[1].endswith("bất kiến chân hình ư thế?")            # ghép qua trang, bỏ [1]
    assert got[4] == "Đệ tử Lý Sâm hòa nam."
    assert all("[1]" not in t and "Chú thích" not in t for t in got)


def test_vi_split_keeps_abbreviations(root):
    cfg = clean.CleanConfig(layer="nghia")
    text = "Ông ở TP. Hồ Chí Minh. Bà đến lúc 5 giờ; rồi đi. Thế là xong?!"
    spans = clean.split_vi(text, cfg)
    assert [text[a:b].strip() for a, b in spans] == [
        "Ông ở TP. Hồ Chí Minh.", "Bà đến lúc 5 giờ;", "rồi đi.", "Thế là xong?!"]


def test_tho_defaults_to_line_split(root):
    write_layer(root, "tho", {1: ["Thiếp nhớ chàng, đêm dài", "Gió lùa qua cửa sổ"]})
    run_clean(root, "tho")
    assert texts(root, "tho") == ["Thiếp nhớ chàng, đêm dài", "Gió lùa qua cửa sổ"]


def test_han_am_levenshtein_then_export(root):
    run_clean(root, "han", "--han-heading-heuristic")
    run_clean(root, "am", "--drop-pattern", "^Phần II")
    argv = ["--work-id", WORK, "--data-root", str(root), "--pair", "han-am", "--method", "levenshtein",
            "--opt", f"reading_dict={root / 'han_viet.tsv'}"]
    assert align.main(argv) == 0
    beads = read_alignments(root / WORK / "06_alignment" / "han-am.xml")
    assert [b["kind"] for b in beads] == ["1-1"] * 5
    assert all(b["score"] >= 0.9 for b in beads), [b["score"] for b in beads]

    assert export.main(["--work-id", WORK, "--data-root", str(root), "--pair", "han-am",
                        "--name", "T_01_parallel"]) == 0
    out = root / WORK / "07_export"
    lines = (out / "T_01_parallel.tsv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5 and all(l.count("\t") == 1 for l in lines)
    ws = load_workbook(out / "T_01_parallel.xlsx")["DONG_HANG"]
    assert [c.value for c in ws[1]] == ["Hán", "Việt"] and ws.max_row == 6
    # chạy lại khi đầu vào không đổi: bỏ qua êm (mã 0), không ghi lại file
    before = (out / "T_01_parallel.tsv").stat().st_mtime_ns
    assert export.main(["--work-id", WORK, "--data-root", str(root), "--pair", "han-am",
                        "--name", "T_01_parallel"]) == 0
    assert (out / "T_01_parallel.tsv").stat().st_mtime_ns == before
    # đầu vào đổi mà không --force: vẫn chặn, không ghi đè ngầm
    am = root / WORK / "05_cleaning" / "am.xml"
    am.write_text(am.read_text(encoding="utf-8").replace("Lý Sâm", "Lý Sâm "), encoding="utf-8")
    assert export.main(["--work-id", WORK, "--data-root", str(root), "--pair", "han-am",
                        "--name", "T_01_parallel"]) == 1


def test_han_am_merge_and_omission(root):
    # bản âm gộp câu 2+3 thành một và bỏ mất câu cuối
    write_layer(root, "am", {1: [
        "Phù đạo xứ thanh hư, tứ đại lý thường, nhi hữu pháp môn, diệu xuất quần vực.",
        "Nhược xưng kỳ công năng lợi vật, độ thoát vô lượng vi giáo, hà cư bất kiến chân hình ư thế, "
        "chân không thuyết nhi vô thực da?",
        "Kim chính tựu tầm tây phương căn nguyên, phục nguyện đại hòa thượng thùy hoài, doãn nạp hạ tâm, "
        "vô tích thần ngữ."]})
    run_clean(root, "han")
    run_clean(root, "am")
    assert align.main(["--work-id", WORK, "--data-root", str(root), "--pair", "han-am", "--method", "levenshtein",
                       "--opt", f"reading_dict={root / 'han_viet.tsv'}"]) == 0
    beads = read_alignments(root / WORK / "06_alignment" / "han-am.xml")
    assert [b["kind"] for b in beads] == ["1-1", "2-1", "1-1", "1-0"]
    assert beads[-1]["flags"] == ["unaligned"]
    export.main(["--work-id", WORK, "--data-root", str(root), "--pair", "han-am"])
    tsv = (root / WORK / "07_export" / f"{WORK}_han-am_parallel.tsv").read_text(encoding="utf-8").splitlines()
    assert len(tsv) == 3                                              # câu Hán bị bỏ không vào file bàn giao


def test_han_nghia_length_baseline_covers_everything(root):
    run_clean(root, "han")
    run_clean(root, "nghia", "--drop-pattern", "^Phần II")
    assert align.main(["--work-id", WORK, "--data-root", str(root), "--pair", "han-nghia",
                       "--method", "length"]) == 0
    beads = read_alignments(root / WORK / "06_alignment" / "han-nghia.xml")
    assert sum(len(b["src_ids"]) for b in beads) == 5
    assert sum(len(b["tgt_ids"]) for b in beads) == len(texts(root, "nghia"))


# --------------------------------------------------------------------------- #
def test_build_han_viet_dict(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import build_han_viet_dict as b
    unihan = tmp_path / "Unihan_Readings.txt"
    unihan.write_text("# comment\nU+4E0D\tkMandarin\tbù\nU+4E0D\tkVietnamese\tbất phủ\n"
                      "U+5922\tkVietnamese\tMộng\n", encoding="utf-8")
    out = tmp_path / "d.tsv"
    assert b.main(["--unihan", str(unihan), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "不\tbất phủ\n夢\tmộng\n"


def test_pipeline_plan_and_dry_run(tmp_path, capsys):
    import json
    from src import pipeline
    cfg = {"common": {"work_id": "T_01", "data_root": str(tmp_path), "dpi": 300},
           "layers": {"han": {"pdf": "book.pdf", "pages": "205", "region": "0,0.1,1,0.9"},
                      "am": {}},
           "clean": {"am": {"drop_pattern": ["^Phần II"]}},
           "pairs": {"han-am": {"method": "levenshtein", "opt": ["reading_dict=d.tsv"]}}}
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert pipeline.main(["--config", str(path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    steps = [line.split()[3] for line in out.splitlines() if line.startswith("$")]
    # han: extract, ocr, clean; am (không có pdf): chỉ clean; cặp: align, export
    assert steps == ["src.extraction.pdf_extraction", "src.ocr.ocr", "src.cleaning.pipeline",
                     "src.cleaning.pipeline", "src.alignment.align", "src.alignment.export"]
    assert "--region 0,0.1,1,0.9" in out and "--drop-pattern '^Phần II'" in out


def test_pipeline_new_config_uses_two_ocr_runs(tmp_path, capsys):
    import json
    from src import pipeline
    cfg = {"common": {"work_id": "T_01", "data_root": str(tmp_path), "source": "book.pdf",
                      "pages": "1-2"},
           "ocr": {"chi_tra": {"engines": "tesseract"}, "vie": {"engines": "tesseract"}},
           "segmentation": {"layers": {"han": {"source": "chi_tra"}, "am": {"source": "vie"},
                                       "nghia": {"source": "vie"}}},
           "metadata_extraction": {"enabled": True},
           "pairs": {"han-am": {"method": "levenshtein"}}}
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert pipeline.main(["--config", str(path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    steps = [line.split()[3] for line in out.splitlines() if line.startswith("$")]
    assert steps == ["src.extraction.pdf_extraction", "src.ocr.ocr", "src.ocr.ocr",
                     "src.segmentation.pipeline", "src.cleaning.pipeline",
                     "src.cleaning.pipeline", "src.cleaning.pipeline",
                     "src.segmentation.sentences", "src.segmentation.sentences", "src.segmentation.sentences",
                     "src.validation.validate",
                     "src.alignment.align", "src.alignment.export"]
    assert out.count("--writing-system chi_tra") == 1
    assert out.count("--writing-system vie") == 1
    assert out.count("--no-metadata") == 3
    assert "--config" in out.split("src.segmentation.pipeline")[1]


def test_pipeline_plans_consolidated_han_viet_export(tmp_path, capsys):
    import json
    from src import pipeline
    cfg = {"common": {"work_id": "T_01", "data_root": str(tmp_path)},
           "pairs": {"han-viet": {}},
           "consolidated": {"name": "T_01_han-viet_parallel"}}
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert pipeline.main(["--config", str(path), "--steps", "export", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.count("src.alignment.export") == 1
    assert "--pair han-viet" in out
    assert "--name T_01_han-viet_parallel" in out


def test_pipeline_runs_clean_align_export(root):
    import json
    from src import pipeline
    cfg = {"common": {"work_id": WORK, "data_root": str(root)},
           "layers": {"han": {}, "am": {}},
           "clean": {"am": {"drop_pattern": ["^Phần II"]}},
           "pairs": {"han-am": {"method": "levenshtein", "opt": [f"reading_dict={root / 'han_viet.tsv'}"],
                                "name": "T_01_parallel"}}}
    path = root / "cfg.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert pipeline.main(["--config", str(path), "--steps", "clean,align,export", "--force"]) == 0
    assert (root / WORK / "07_export" / "T_01_parallel.xlsx").exists()
