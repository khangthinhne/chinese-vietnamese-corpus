from types import SimpleNamespace

from openpyxl import load_workbook

from src.alignment import export
from src.alignment.serialization import write_alignments
from src.common.corpus import DataLayout
from src.common.sentences import Sentence, alignment_dir, save_sentences

WORK = "T_01"


def sentences(layer: str, texts: list[str]) -> list[Sentence]:
    return [Sentence(id=f"{WORK}.{layer}.{i:05d}", text=text, pages=[1], src_ids=[],
                     document_id=f"{WORK}.001")
            for i, text in enumerate(texts, 1)]


def bead(no: int, src: str, tgt: list[str], text: str) -> dict:
    return {"id": f"{WORK}.han-am.{no:05d}", "document_id": f"{WORK}.001",
            "kind": f"1-{len(tgt)}", "score": 0.9, "flags": [], "src_ids": [src],
            "tgt_ids": tgt, "pages": {"han": [1], "am": [1]},
            "src_text": src, "tgt_text": text}


def test_consolidated_export_has_four_columns_and_blank_poetry(tmp_path):
    layout = DataLayout(tmp_path, WORK)
    han = sentences("han", ["H1", "H2"])
    save_sentences(layout, "han", han)
    save_sentences(layout, "am", sentences("am", ["A1", "A2", "A3"]))
    save_sentences(layout, "nghia", sentences("nghia", ["N1", "N2"]))
    directory = alignment_dir(layout)
    directory.mkdir(parents=True)
    write_alignments(directory / "han-am.xml", WORK, "han-am", "han", "am", [
        bead(1, han[0].id, [f"{WORK}.am.00001", f"{WORK}.am.00002"], "A1 A2"),
        bead(2, han[1].id, [f"{WORK}.am.00003"], "A3")])
    write_alignments(directory / "han-nghia.xml", WORK, "han-nghia", "han", "nghia", [
        {**bead(1, han[0].id, [f"{WORK}.nghia.00001"], "N1"),
         "id": f"{WORK}.han-nghia.00001", "pages": {"han": [1], "nghia": [1]}},
        {**bead(2, han[1].id, [f"{WORK}.nghia.00002"], "N2"),
         "id": f"{WORK}.han-nghia.00002", "pages": {"han": [1], "nghia": [1]}}])

    args = SimpleNamespace(data_root=str(tmp_path), work_id=WORK,
                           name="T_01_han-viet_parallel", min_score=0.0,
                           exclude_flag=[], force=True)
    path = export.run_consolidated(args)
    wb = load_workbook(path)
    assert wb.sheetnames == ["DONG_HANG", "REVIEW"]
    ws = wb["DONG_HANG"]
    assert [c.value for c in ws[1]] == ["Hán", "Dịch âm", "Dịch nghĩa", "Dịch thơ"]
    assert ws.max_column == 4
    assert [[c.value for c in row] for row in ws.iter_rows(min_row=2)] == [
        ["H1", "A1 A2", "N1", None],
        ["H2", "A3", "N2", None]]
    review = wb["REVIEW"]
    statuses = {row[1].value: row[-1].value for row in review.iter_rows(min_row=2)}
    assert statuses == {"han-am": "included", "han-nghia": "included"}
