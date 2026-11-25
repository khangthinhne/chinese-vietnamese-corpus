"""Đóng gói OCR thô hai luồng của MỘT khoảng trang để gửi về người điều phối (không kèm ảnh/PDF/XML).

    python scripts/pack_ocr.py --work-id CPO_19 --pages 5-623 --member tv2
    python scripts/pack_ocr.py --work-id CPO_20                 # khoảng = ocr.streams.pages của config

Gói `dist/<WORK>_<đầu>-<cuối>[_<member>]_<YYYYMMDD>.zip` gồm (đường dẫn tương đối với data root):
    <WORK>/03_ocr/raw/**/NNNN.json                      JSON thô (bất biến) của ĐÚNG các trang trong khoảng
    <WORK>/03_ocr/manifests/ocr_<han|vie>_<đầu>-<cuối>.json   manifest OCR lọc theo khoảng (khoá run đã chọn)
    <WORK>/03_ocr/ocr_check_<đầu>-<cuối>.json           kết quả check_ocr_complete trên khoảng (lúc đóng gói)
    PACK_INFO.json                                      ai/khi nào/commit/PDF, trang có run, trang thiếu
    MANIFEST.sha256                                     checksum từng file (định dạng sha256sum)
han.xml/vie.xml/TXT KHÔNG đóng gói: người nhận dựng lại bằng `build_layers` từ raw/. Ảnh trích lại được từ PDF.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.corpus import DataLayout, parse_pages  # noqa: E402
from src.common.page_naming import page_stem  # noqa: E402
from src.ocr import ocr_check, raw_store, work_config  # noqa: E402
from src.ocr.build_layers import LAYER_ENGINES, layer_manifest_paths, run_engine  # noqa: E402

SUMS_NAME = "MANIFEST.sha256"
INFO_NAME = "PACK_INFO.json"
RAW_NAME_RE = re.compile(r"(?:page_)?(\d{4})\.json")
REPO_ROOT = Path(__file__).resolve().parents[1]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def range_tag(pages: list[int]) -> str:
    return f"{page_stem(min(pages))}-{page_stem(max(pages))}"


def raw_files(layout: DataLayout, pages: list[int]) -> list[Path]:
    """Mọi file raw (kho engine, unified cũ, hệ chữ cũ) của các trang trong khoảng."""
    wanted = set(pages)
    root = layout.ocr_raw_root
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.rglob("*.json")):
        m = RAW_NAME_RE.fullmatch(path.name)
        if m and int(m[1]) in wanted and path.is_file():
            out.append(path)
    return out


def coverage(layout: DataLayout, pages: list[int]) -> dict[str, list[int]]:
    """Trang có ít nhất một run của engine từng luồng."""
    out = {}
    for layer, engine in LAYER_ENGINES.items():
        out[layer] = [p for p in pages
                      if any(run_engine(r) == engine for r in raw_store.page_runs(layout, engine, p, layout.work_id))]
    return out


def range_manifest(layout: DataLayout, layer: str, pages: list[int]) -> dict:
    """Bản ghi mới nhất theo trang (manifest gộp, rồi manifest theo khoảng mới trước), chỉ trong khoảng."""
    wanted, records, sources, updated = set(pages), {}, [], ""
    engine = LAYER_ENGINES[layer]
    for path in layer_manifest_paths(layout, layer):
        data = json.loads(path.read_text(encoding="utf-8"))
        hit = False
        for rec in data.get("pages", []):
            if isinstance(rec, dict) and rec.get("page") in wanted and rec["page"] not in records:
                records[rec["page"]] = rec
                hit = True
        if hit:
            sources.append(path.name)
            updated = max(updated, str(data.get("updated_at") or ""))
    return {"schema_version": 1, "work_id": layout.work_id, "layer": layer, "engine": engine,
            "updated_at": updated or raw_store.now(), "packed_from": sources,
            "pages_requested": work_config.compact(pages),
            "pages": [records[p] for p in sorted(records)],
            "error_pages": sorted(p for p, r in records.items() if r.get("status") == "error")}


def git(*argv: str) -> str | None:
    try:
        out = subprocess.run(["git", *argv], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def run_check(layout: DataLayout, pages_spec: str, settings: dict) -> dict:
    try:
        import opencc
        converter = opencc.OpenCC("s2t").convert
    except ImportError:
        converter = None
    return ocr_check.check_work(layout, pages_spec, settings["check"], converter)


def pack(work_id: str, data_root: Path, out_dir: Path, *, pages: str | None = None, member: str | None = None,
         config: Path | None = None, force: bool = False, date: str | None = None) -> tuple[Path, dict]:
    layout = DataLayout(data_root, work_id)
    config = config or Path("configs") / f"{work_id}.json"
    cfg = work_config.load_config(config) if config.exists() else {}
    settings = work_config.stream_settings(cfg)
    spec = pages or work_config.resolve_pages(settings, layout)
    page_list = parse_pages(spec)
    if member and not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", member):
        raise SystemExit("--member chỉ gồm chữ/số/_/- (vd tv2)")
    files = raw_files(layout, page_list)
    if not files:
        raise SystemExit(f"Không có JSON thô nào cho trang {spec} trong {layout.ocr_raw_root}")
    tag = range_tag(page_list)
    date = date or datetime.now().strftime("%Y%m%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{work_id}_{tag}{'_' + member if member else ''}_{date}.zip"
    if target.exists() and not force:
        raise SystemExit(f"{target} đã tồn tại; dùng --force để ghi đè")
    report = run_check(layout, spec, settings)
    cov = coverage(layout, page_list)
    missing = {layer: sorted(set(page_list) - set(have)) for layer, have in cov.items()}
    sums: list[str] = []
    total = 0
    tmp = target.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        def put(arc: str, data: bytes) -> None:
            nonlocal total
            zf.writestr(arc, data)
            sums.append(f"{sha256_bytes(data)}  {arc}")
            total += len(data)
        for path in files:
            put(path.relative_to(data_root).as_posix(), path.read_bytes())
        for layer in LAYER_ENGINES:
            body = range_manifest(layout, layer, page_list)
            put(f"{work_id}/03_ocr/manifests/ocr_{layer}_{tag}.json",
                json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8"))
        put(f"{work_id}/03_ocr/ocr_check_{tag}.json", json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
        info = {"format": 2, "work_id": work_id, "member": member, "pages": work_config.compact(page_list),
                "n_pages": len(page_list),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "git_head": git("rev-parse", "HEAD"), "git_describe": git("describe", "--tags", "--always"),
                "source": (cfg.get("common") or {}).get("source"), "source_sha256": settings.get("source_sha256"),
                "files": len(sums), "bytes_uncompressed": total,
                "pages_han": work_config.compact(cov["han"]), "pages_vie": work_config.compact(cov["vie"]),
                "missing_han": work_config.compact(missing["han"]), "missing_vie": work_config.compact(missing["vie"]),
                "check": {"complete": report["complete"], **{k: report["totals"][k] for k in
                          ("range", "pages", "han_run", "vie_run", "han_lines", "vie_lines", "errors", "warnings")}}}
        zf.writestr(INFO_NAME, json.dumps(info, ensure_ascii=False, indent=2))
        zf.writestr(SUMS_NAME, "\n".join(sums) + "\n")
    tmp.replace(target)
    return target, info


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--pages", help="Khoảng trang, vd 5-623 (mặc định ocr.streams.pages của config)")
    parser.add_argument("--member", help="Mã người chạy, vd tv2 (đưa vào tên gói)")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--config", help="Mặc định configs/<work-id>.json")
    parser.add_argument("--out", default="dist")
    parser.add_argument("--force", action="store_true", help="Ghi đè gói cùng tên đã có")
    args = parser.parse_args(argv)
    target, info = pack(args.work_id, Path(args.data_root), Path(args.out), pages=args.pages, member=args.member,
                        config=Path(args.config) if args.config else None, force=args.force)
    size = target.stat().st_size
    print(f"{target}: {size / 1e6:.1f} MB nén ({info['bytes_uncompressed'] / 1e6:.1f} MB gốc), {info['files']} file")
    print(f"   trang {info['pages']} ({info['n_pages']}): có Hán {info['pages_han'] or '-'}; có Việt {info['pages_vie'] or '-'}")
    print(f"   THIẾU Hán: {info['missing_han'] or 'không'}; THIẾU Việt: {info['missing_vie'] or 'không'}")
    print(f"   check: {'ĐỦ' if info['check']['complete'] else 'CHƯA ĐỦ'} "
          f"(lỗi {info['check']['errors']}, cảnh báo {info['check']['warnings']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
