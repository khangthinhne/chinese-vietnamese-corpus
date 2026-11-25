"""Đóng gói OCR thô hai luồng của một work để gửi cho nhóm (không kèm ảnh/PDF nguồn).

    python scripts/pack_ocr.py --work-id CPO_19
    python scripts/pack_ocr.py --work-id CPO_19 --out dist --force

Gói `dist/<WORK>_ocr_<YYYYMMDD>.zip` gồm, theo đường dẫn tương đối với data root:
    <WORK>/03_ocr/raw/**            JSON thô (bất biến)
    <WORK>/03_ocr/manifests/**      manifest OCR/build
    <WORK>/03_ocr/ocr_raw/**        han.xml, vie.xml (+ raw.xml, chi_tra.xml... nếu có)
    <WORK>/03_ocr/ocr_raw_text/**   TXT theo trang
    <WORK>/03_ocr/metadata.json     (nếu có)
    <WORK>/03_ocr/ocr_check.json    kết quả scripts/check_ocr_complete.py (chạy lại lúc đóng gói)
    PACK_INFO.json, SHA256SUMS      thông tin gói + checksum từng file
Ảnh trang trích lại được từ PDF (`src.extraction.pdf_extraction`), nên không đóng gói.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.corpus import DataLayout  # noqa: E402
from src.ocr import ocr_check, work_config  # noqa: E402

INCLUDE_DIRS = ("raw", "manifests", "ocr_raw", "ocr_raw_text")
INCLUDE_FILES = ("metadata.json",)
CHECK_NAME = "ocr_check.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect(layout: DataLayout) -> list[Path]:
    ocr_dir = layout.work_dir / "03_ocr"
    files: list[Path] = []
    for name in INCLUDE_DIRS:
        folder = ocr_dir / name
        if folder.is_dir():
            files += sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    files += [ocr_dir / n for n in INCLUDE_FILES if (ocr_dir / n).is_file()]
    return files


def git_head() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build_check(layout: DataLayout, config: Path) -> dict:
    cfg = work_config.load_config(config) if config.exists() else {}
    settings = work_config.stream_settings(cfg)
    try:
        import opencc
        converter = opencc.OpenCC("s2t").convert
    except ImportError:
        converter = None
    return ocr_check.check_work(layout, work_config.resolve_pages(settings, layout), settings["check"], converter)


def pack(work_id: str, data_root: Path, out_dir: Path, *, force: bool = False, date: str | None = None) -> Path:
    layout = DataLayout(data_root, work_id)
    files = collect(layout)
    if not files:
        raise SystemExit(f"Không có gì để đóng gói trong {layout.work_dir / '03_ocr'}")
    report = build_check(layout, Path("configs") / f"{work_id}.json")
    date = date or datetime.now().strftime("%Y%m%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{work_id}_ocr_{date}.zip"
    if target.exists() and not force:
        raise SystemExit(f"{target} đã tồn tại; dùng --force để ghi đè")
    sums: list[str] = []
    tmp = target.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            arc = path.relative_to(data_root).as_posix()
            data = path.read_bytes()
            zf.writestr(arc, data)
            sums.append(f"{sha256_bytes(data)}  {arc}")
        check_bytes = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
        check_arc = f"{work_id}/03_ocr/{CHECK_NAME}"
        zf.writestr(check_arc, check_bytes)
        sums.append(f"{sha256_bytes(check_bytes)}  {check_arc}")
        info = {"work_id": work_id, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "git_head": git_head(), "files": len(sums),
                "bytes_uncompressed": sum(p.stat().st_size for p in files) + len(check_bytes),
                "check": {"complete": report["complete"], **{k: report["totals"][k] for k in
                          ("range", "pages", "han_run", "vie_run", "han_lines", "vie_lines", "errors", "warnings")}}}
        zf.writestr("PACK_INFO.json", json.dumps(info, ensure_ascii=False, indent=2))
        zf.writestr("SHA256SUMS", "\n".join(sums) + "\n")
    tmp.replace(target)
    return target


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--out", default="dist")
    parser.add_argument("--force", action="store_true", help="Ghi đè gói cùng ngày đã có")
    args = parser.parse_args(argv)
    target = pack(args.work_id, Path(args.data_root), Path(args.out), force=args.force)
    with zipfile.ZipFile(target) as zf:
        info = json.loads(zf.read("PACK_INFO.json"))
    size = target.stat().st_size
    print(f"{target}: {size / 1e6:.1f} MB nén ({info['bytes_uncompressed'] / 1e6:.1f} MB gốc), {info['files']} file; "
          f"check: {'ĐỦ' if info['check']['complete'] else 'CHƯA ĐỦ'} "
          f"(lỗi {info['check']['errors']}, cảnh báo {info['check']['warnings']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
