"""Ghép nhiều gói OCR (scripts/pack_ocr.py) của cùng một work vào data/ một cách an toàn.

    python scripts/merge_ocr_packs.py dist/CPO_19_0005-0623_tv2_*.zip dist/CPO_19_0624-0833_tv3_*.zip --dry-run
    python scripts/merge_ocr_packs.py dist/CPO_19_*.zip --build --check

- Kiểm tra checksum (MANIFEST.sha256, hoặc SHA256SUMS của gói cũ) của MỌI gói trước khi ghi bất cứ gì.
- Chỉ nhận đường dẫn `<WORK>/03_ocr/...` (chặn `..`, đường dẫn tuyệt đối, ổ đĩa).
- Mỗi file: chưa có -> ghi; giống hệt -> bỏ qua; `raw/**.json` mà bản mới chỉ THÊM run vào cuối -> cập nhật;
  còn lại -> XUNG ĐỘT, không bao giờ ghi đè. Các gói được xét lần lượt, gói sau so với kết quả của gói trước.
- Báo cáo phủ trang so với `ocr.streams.pages` của configs/<WORK>.json: trang thiếu Hán/Việt, khoảng chồng lấn
  giữa các gói (chồng lấn cùng nội dung là bình thường, ví dụ gói hạt giống).
- Có xung đột: mặc định KHÔNG ghi gì (mã thoát 1); `--keep-going` ghi phần không xung đột.
- `--build` chạy build_layers trên toàn bộ raw; `--check` chạy check_ocr_complete trên khoảng đầy đủ của work.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ALLOWED_TOP = "03_ocr"
SUMS_NAMES = ("MANIFEST.sha256", "SHA256SUMS")
INFO_NAME = "PACK_INFO.json"
ENGINE_LAYER = {"paddlevl": "han", "qwen": "vie"}
RAW_ENGINE_RE = re.compile(r"[^/]+/03_ocr/raw/(paddlevl|qwen)/(\d{4})\.json")


def safe_target(arc: str, data_root: Path) -> Path | None:
    parts = PurePosixPath(arc).parts
    if (len(parts) < 3 or parts[1] != ALLOWED_TOP or any(p in ("..", "", ".") for p in parts)
            or PurePosixPath(arc).is_absolute() or ":" in arc or "\\" in arc):
        return None
    return data_root.joinpath(*parts)


def raw_is_append(old: bytes, new: bytes) -> bool:
    """Bản mới chỉ thêm run vào cuối file raw JSON đã có (không sửa/xoá run cũ, phần đầu file giống hệt)."""
    try:
        a, b = json.loads(old), json.loads(new)
    except ValueError:
        return False
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    runs_a, runs_b = a.get("runs"), b.get("runs")
    same_head = {k: v for k, v in a.items() if k != "runs"} == {k: v for k, v in b.items() if k != "runs"}
    return (same_head and isinstance(runs_a, list) and isinstance(runs_b, list)
            and len(runs_b) > len(runs_a) and runs_b[:len(runs_a)] == runs_a)


def read_sums(zf: zipfile.ZipFile) -> dict[str, str]:
    names = set(zf.namelist())
    for name in SUMS_NAMES:
        if name in names:
            sums = {}
            for line in zf.read(name).decode("utf-8").splitlines():
                if line.strip():
                    digest, arc = line.split("  ", 1)
                    sums[arc] = digest
            return sums
    raise ValueError(f"{zf.filename}: thiếu {SUMS_NAMES[0]}")


def parse_range(text: str | None) -> list[int]:
    from src.common.corpus import parse_pages
    return parse_pages(text) if text else []


def compact(pages) -> str:
    from src.ocr.work_config import compact as _compact
    return _compact(sorted(pages))


class Plan:
    """Kết quả xét các gói: không ghi gì cho tới khi `apply`."""

    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.overlay: dict[Path, bytes] = {}           # đích -> nội dung sẽ ghi (sau khi xét các gói trước)
        self.packs: list[dict] = []
        self.problems: list[str] = []                  # checksum sai / đường dẫn bị từ chối / sai work
        self.conflicts: list[tuple[str, str]] = []     # (gói, arc)
        self.counts = {"new": 0, "same": 0, "append": 0, "conflict": 0}
        self.work_id: str | None = None

    def current(self, target: Path) -> bytes | None:
        if target in self.overlay:
            return self.overlay[target]
        return target.read_bytes() if target.exists() else None

    def add_pack(self, path: Path) -> None:
        with zipfile.ZipFile(path) as zf:
            info = json.loads(zf.read(INFO_NAME)) if INFO_NAME in zf.namelist() else {}
            sums = read_sums(zf)
            work = info.get("work_id") or (next(iter(sums)).split("/", 1)[0] if sums else None)
            if self.work_id and work != self.work_id:
                self.problems.append(f"{path.name}: work {work} khác {self.work_id}")
                return
            self.work_id = work
            record = {"zip": path.name, "member": info.get("member"), "pages": parse_range(info.get("pages")),
                      "raw_pages": {"han": set(), "vie": set()}, "counts": dict.fromkeys(self.counts, 0),
                      "git_head": info.get("git_head"), "source_sha256": info.get("source_sha256")}
            extra = [n for n in zf.namelist() if n not in sums and n not in (INFO_NAME, *SUMS_NAMES)]
            self.problems += [f"{path.name}: file không có trong checksum: {n}" for n in extra]
            staged: list[tuple[str, Path, bytes]] = []
            for arc, digest in sums.items():
                target = safe_target(arc, self.data_root)
                if target is None or not arc.startswith(f"{work}/"):
                    self.problems.append(f"{path.name}: đường dẫn bị từ chối: {arc}")
                    continue
                try:
                    data = zf.read(arc)
                except KeyError:
                    self.problems.append(f"{path.name}: thiếu file {arc}")
                    continue
                if hashlib.sha256(data).hexdigest() != digest:
                    self.problems.append(f"{path.name}: checksum sai: {arc}")
                    continue
                staged.append((arc, target, data))
                m = RAW_ENGINE_RE.fullmatch(arc)
                if m:
                    record["raw_pages"][ENGINE_LAYER[m[1]]].add(int(m[2]))
            for arc, target, data in staged:
                old = self.current(target)
                if old is None:
                    outcome = "new"
                elif old == data:
                    outcome = "same"
                elif "/03_ocr/raw/" in arc and arc.endswith(".json") and raw_is_append(old, data):
                    outcome = "append"
                elif "/03_ocr/raw/" in arc and arc.endswith(".json") and raw_is_append(data, old):
                    outcome = "same"                    # máy này đã có thêm run: bản trong gói là phần đầu
                else:
                    outcome = "conflict"
                record["counts"][outcome] += 1
                self.counts[outcome] += 1
                if outcome in ("new", "append"):
                    self.overlay[target] = data
                elif outcome == "conflict":
                    self.conflicts.append((path.name, arc))
            if not record["pages"]:
                record["pages"] = sorted(record["raw_pages"]["han"] | record["raw_pages"]["vie"])
            self.packs.append(record)

    def overlaps(self) -> list[tuple[str, str, list[int]]]:
        out = []
        for i, a in enumerate(self.packs):
            for b in self.packs[i + 1:]:
                common = sorted(set(a["pages"]) & set(b["pages"]))
                if common:
                    out.append((a["zip"], b["zip"], common))
        return out

    def coverage(self, full: list[int]) -> dict[str, list[int]]:
        """Trang của khoảng đầy đủ đã có run từng luồng (máy này + các gói)."""
        from src.common.corpus import DataLayout
        from src.ocr import raw_store
        from src.ocr.build_layers import LAYER_ENGINES, run_engine
        layout = DataLayout(self.data_root, self.work_id)
        have = {}
        for layer, engine in LAYER_ENGINES.items():
            from_packs = set().union(*(p["raw_pages"][layer] for p in self.packs)) if self.packs else set()
            local = {p for p in full if p not in from_packs and layout.ocr_raw_root.is_dir()
                     and any(run_engine(r) == engine for r in raw_store.page_runs(layout, engine, p, self.work_id))}
            have[layer] = sorted((from_packs | local) & set(full))
        return have

    def apply(self) -> int:
        for target, data in self.overlay.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".part")
            tmp.write_bytes(data)
            tmp.replace(target)
        return len(self.overlay)


def full_range(work_id: str, config: Path | None) -> list[int]:
    from src.ocr import work_config
    config = config or Path("configs") / f"{work_id}.json"
    if not config.exists():
        return []
    settings = work_config.stream_settings(work_config.load_config(config))
    return parse_range(settings.get("pages"))


def summarize(plan: Plan, full: list[int]) -> dict:
    overlaps = plan.overlaps()
    have = plan.coverage(full) if full else {"han": [], "vie": []}
    missing = {layer: sorted(set(full) - set(v)) for layer, v in have.items()}
    return {"work_id": plan.work_id, "full_range": compact(full), "counts": plan.counts,
            "packs": [{**{k: v for k, v in p.items() if k not in ("pages", "raw_pages")},
                       "pages": compact(p["pages"])} for p in plan.packs],
            "overlaps": [{"a": a, "b": b, "pages": compact(c), "n": len(c)} for a, b, c in overlaps],
            "missing_han": compact(missing["han"]), "missing_vie": compact(missing["vie"]),
            "n_missing": len(set(missing["han"]) | set(missing["vie"])),
            "conflicts": [f"{z}: {a}" for z, a in plan.conflicts], "problems": plan.problems}


def format_summary(s: dict) -> str:
    n_overlap = sum(o["n"] for o in s["overlaps"])
    lines = [f"== {s['work_id']}: {len(s['packs'])} gói; mới {s['counts']['new']}, giống {s['counts']['same']}, "
             f"thêm run {s['counts']['append']}, XUNG ĐỘT {s['counts']['conflict']}"]
    for p in s["packs"]:
        lines.append(f"   {p['zip']}: trang {p['pages']} (member {p['member']}, commit {str(p['git_head'])[:10]})")
    if s["full_range"]:
        status = "đủ" if s["n_missing"] == 0 else "CHƯA đủ"
        lines.append(f"   {status} {s['full_range']}, {s['n_missing']} thiếu, {n_overlap} chồng")
        if s["missing_han"]:
            lines.append(f"   thiếu Hán: {s['missing_han']}")
        if s["missing_vie"]:
            lines.append(f"   thiếu Việt: {s['missing_vie']}")
    for o in s["overlaps"]:
        lines.append(f"   chồng lấn {o['a']} x {o['b']}: {o['pages']} ({o['n']} trang)")
    shas = {p["source_sha256"] for p in s["packs"] if p["source_sha256"]}
    if len(shas) > 1:
        lines.append(f"   CẢNH BÁO: các gói dùng PDF khác nhau (source_sha256 {sorted(shas)})")
    heads = {p["git_head"] for p in s["packs"] if p["git_head"]}
    if len(heads) > 1:
        lines.append(f"   ghi chú: các gói từ {len(heads)} commit khác nhau (xem docs/ocr_assignment.md)")
    for c in s["conflicts"][:50]:
        lines.append(f"   xung đột (giữ bản trong máy): {c}")
    for p in s["problems"][:50]:
        lines.append(f"   LỖI: {p}")
    return "\n".join(lines)


def merge(zips: list[Path], data_root: Path, *, dry_run: bool = False, keep_going: bool = False,
          config: Path | None = None) -> tuple[int, dict]:
    plan = Plan(data_root)
    for z in zips:
        plan.add_pack(z)
    summary = summarize(plan, full_range(plan.work_id, config) if plan.work_id else [])
    if plan.problems:
        summary["written"] = 0
        return 2, summary
    if plan.conflicts and not keep_going:
        summary["written"] = 0
        return 1, summary
    summary["written"] = 0 if dry_run else plan.apply()
    return (1 if plan.conflicts else 0), summary


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("zips", nargs="+")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--config", help="Mặc định configs/<work>.json (khoảng trang đầy đủ của work)")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ báo cáo, không ghi")
    parser.add_argument("--keep-going", action="store_true", help="Có xung đột vẫn ghi phần không xung đột")
    parser.add_argument("--build", action="store_true", help="Sau khi ghép: build_layers trên mọi raw")
    parser.add_argument("--check", action="store_true", help="Sau khi ghép: check_ocr_complete trên khoảng đầy đủ")
    parser.add_argument("--json", help="Ghi báo cáo ghép ra file JSON")
    args = parser.parse_args(argv)
    config = Path(args.config) if args.config else None
    code, summary = merge([Path(z) for z in args.zips], Path(args.data_root), dry_run=args.dry_run,
                          keep_going=args.keep_going, config=config)
    print(format_summary(summary))
    print(f"   {'(dry-run) ' if args.dry_run else ''}đã ghi {summary['written']} file vào {args.data_root}"
          + ("" if code == 0 else " -- DỪNG: có lỗi/xung đột, không ghi" if summary["written"] == 0 else ""))
    if args.json:
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if code == 0 and not args.dry_run and (args.build or args.check):
        cfg = str(config or Path("configs") / f"{summary['work_id']}.json")
        if args.build:
            from src.ocr import build_layers
            if build_layers.main(["--config", cfg, "--work-id", summary["work_id"],
                                  "--data-root", args.data_root]) != 0:
                return 1
        if args.check:
            import importlib.util
            spec = importlib.util.spec_from_file_location("check_ocr_complete",
                                                          Path(__file__).with_name("check_ocr_complete.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            code = mod.main(["--work-id", summary["work_id"], "--data-root", args.data_root, "--config", cfg])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
