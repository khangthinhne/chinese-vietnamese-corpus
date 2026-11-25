"""Giải gói OCR (từ scripts/pack_ocr.py) vào data/ một cách an toàn.

    python scripts/unpack_ocr.py dist/CPO_19_ocr_20261125.zip --dry-run
    python scripts/unpack_ocr.py dist/CPO_19_ocr_20261125.zip

- Kiểm tra SHA256SUMS trước khi ghi; sai checksum -> dừng, không ghi gì.
- Chỉ nhận đường dẫn `<WORK>/03_ocr/...` (chặn `..`, đường dẫn tuyệt đối).
- File chưa có -> ghi; có và giống hệt -> bỏ qua; có nhưng KHÁC -> xung đột, KHÔNG ghi đè, liệt kê.
  Riêng `03_ocr/raw/**.json` (append-only): nếu run trong máy là phần đầu của run trong gói (gói chỉ có thêm run)
  thì được cập nhật; ngược lại là xung đột.
Mã thoát 1 nếu còn xung đột.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath

ALLOWED_TOP = "03_ocr"


def safe_target(arc: str, data_root: Path) -> Path | None:
    parts = PurePosixPath(arc).parts
    if (len(parts) < 3 or parts[1] != ALLOWED_TOP or any(p in ("..", "") for p in parts)
            or PurePosixPath(arc).is_absolute() or ":" in parts[0]):
        return None
    return data_root.joinpath(*parts)


def raw_is_append(old: bytes, new: bytes) -> bool:
    """Gói chỉ thêm run vào cuối file raw JSON đã có (không sửa/xoá run cũ)."""
    try:
        a, b = json.loads(old), json.loads(new)
    except ValueError:
        return False
    runs_a, runs_b = a.get("runs"), b.get("runs")
    same_head = {k: v for k, v in a.items() if k != "runs"} == {k: v for k, v in b.items() if k != "runs"}
    return (same_head and isinstance(runs_a, list) and isinstance(runs_b, list)
            and len(runs_b) > len(runs_a) and runs_b[:len(runs_a)] == runs_a)


def plan(zf: zipfile.ZipFile, data_root: Path) -> dict[str, list]:
    sums = {}
    for line in zf.read("SHA256SUMS").decode("utf-8").splitlines():
        if line.strip():
            digest, arc = line.split("  ", 1)
            sums[arc] = digest
    out: dict[str, list] = {"new": [], "same": [], "append": [], "conflict": [], "rejected": [], "bad_sum": []}
    for arc, digest in sums.items():
        target = safe_target(arc, data_root)
        if target is None:
            out["rejected"].append(arc)
            continue
        data = zf.read(arc)
        if hashlib.sha256(data).hexdigest() != digest:
            out["bad_sum"].append(arc)
            continue
        if not target.exists():
            out["new"].append((arc, target))
        else:
            old = target.read_bytes()
            if old == data:
                out["same"].append((arc, target))
            elif "/03_ocr/raw/" in arc and arc.endswith(".json") and raw_is_append(old, data):
                out["append"].append((arc, target))
            else:
                out["conflict"].append((arc, target))
    extra = [n for n in zf.namelist() if n not in sums and n not in ("SHA256SUMS", "PACK_INFO.json")]
    out["rejected"] += extra
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("zip")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    data_root = Path(args.data_root)
    with zipfile.ZipFile(args.zip) as zf:
        info = json.loads(zf.read("PACK_INFO.json"))
        p = plan(zf, data_root)
        if p["bad_sum"] or p["rejected"]:
            print(f"Dừng: checksum sai {p['bad_sum'][:5]}, đường dẫn bị từ chối {p['rejected'][:5]}")
            return 2
        print(f"Gói {info['work_id']} ({info['created_at']}, {info['files']} file): mới {len(p['new'])}, "
              f"giống {len(p['same'])}, thêm run {len(p['append'])}, XUNG ĐỘT {len(p['conflict'])}")
        for arc, _ in p["conflict"][:50]:
            print(f"  xung đột (giữ bản trong máy): {arc}")
        if not args.dry_run:
            for arc, target in p["new"] + p["append"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".part")
                tmp.write_bytes(zf.read(arc))
                tmp.replace(target)
            print(f"Đã ghi {len(p['new']) + len(p['append'])} file vào {data_root}")
    return 1 if p["conflict"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
