"""
Tạo từ điển chữ Hán -> âm Hán-Việt (TSV) cho aligner `levenshtein` từ cơ sở dữ liệu Unihan.

Tải Unihan.zip tại https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip (hoặc giải nén lấy
Unihan_Readings.txt), rồi:

    python scripts/build_han_viet_dict.py --unihan Unihan.zip --out resources/han_viet.tsv

Lấy trường kVietnamese. Mỗi dòng ra: <chữ>\t<âm1> <âm2> ... (chữ thường, NFC).
Độ phủ của kVietnamese không đầy đủ với mọi chữ (nhất là chữ hiếm, dị thể); aligner sẽ in ra tỉ lệ
chữ Hán trong văn bản có trong từ điển. Muốn bổ sung, thêm dòng thủ công vào cuối file TSV.
"""

from __future__ import annotations

import argparse
import io
import sys
import unicodedata
import zipfile
from pathlib import Path

FIELD = "kVietnamese"


def iter_lines(path: Path):
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            name = next((n for n in zf.namelist() if n.endswith("Unihan_Readings.txt")), None)
            if name is None:
                raise SystemExit("Không thấy Unihan_Readings.txt trong zip")
            with zf.open(name) as raw:
                yield from io.TextIOWrapper(raw, encoding="utf-8")
    else:
        with open(path, encoding="utf-8") as f:
            yield from f


def build(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in iter_lines(path):
        if line.startswith("#") or FIELD not in line:
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 3 or parts[1] != FIELD or not parts[0].startswith("U+"):
            continue
        ch = chr(int(parts[0][2:], 16))
        vals = [unicodedata.normalize("NFC", v.lower()) for v in parts[2].split()]
        if vals:
            out[ch] = list(dict.fromkeys(vals))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unihan", required=True, help="Unihan.zip hoặc Unihan_Readings.txt")
    ap.add_argument("--out", default="resources/han_viet.tsv")
    args = ap.parse_args(argv)
    table = build(Path(args.unihan))
    if not table:
        print("Không đọc được mục kVietnamese nào; kiểm tra file đầu vào", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"{c}\t{' '.join(v)}\n" for c, v in sorted(table.items())), encoding="utf-8", newline="\n")
    print(f"Đã ghi {len(table)} chữ -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
