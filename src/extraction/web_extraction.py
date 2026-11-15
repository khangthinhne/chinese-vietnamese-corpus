"""
Web -> HTML ->  -> RawPage (common/corpus.py) -> XML thô  (không tách câu, không làm sạch chú thích)

Crawl trang web (Wikisource...) -> lưu HTML gốc -> trích text -> XML thô.
 
Quy trình 2 pha (để tái lập được, không phụ thuộc mạng ở lần chạy sau):
    1. FETCH  : tải URL -> data/<WORK_ID>/01_sources/<layer>.html  (bất biến, không sửa)
    2. PARSE  : đọc HTML đã lưu -> RawPage -> XML
 
INPUT (chọn một)
    --url   URL        (lặp lại nhiều lần cho tác phẩm nhiều trang; lưu <layer>_001.html ...)
    --html  file.html  (HTML đã tải sẵn, vd data/CPO_17/01_sources/han.html; không cần mạng)
    --layer han | am | nghia | tho
    --profile NAME     cấu hình selector theo site (mặc định đoán theo domain; xem PROFILES)
    --selector CSS     ghi đè vùng nội dung chính
    --block-selector CSS / --heading-selector CSS / --remove CSS   tinh chỉnh khi site lạ
 
OUTPUT (data/<WORK_ID>/)
    01_sources/<layer>.html       HTML gốc
    02_extraction/<layer>.xml     XML thô; mỗi khối (p/li/dd) = 1 PAGE (#đoạn), mỗi dòng (<br>)
                                  = 1 câu hoặc chưa nhiều câu nha; heading (h2/h3) = SECT mới.
    02_extraction/metadata.json   URL, thời điểm tải, sha256, số đoạn/câu
 
Chỉ trích xuất; KHÔNG tách câu theo dấu câu, KHÔNG làm sạch chú thích (bước 03_cleaned).
 
Thêm site mới: thêm 1 SiteProfile vào PROFILES + 1 dòng vào HOST_PROFILES (không sửa logic).
 
Ví dụ:
    python -m src.extraction.web_extraction --layer han --work-id CPO_17 \
        --url https://zh.wikisource.org/zh-hant/征婦吟
    python -m src.extraction.web_extraction --layer tho --work-id CPO_17 \
        --html data/CPO_17/01_sources/viet.html

IMPORTANT!: Import các hàm đã defined sẵn trong src/common/corpus.py (RawPage, build_document, finalize, register_source)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
import soupsieve

from src.common.corpus import (
    DataLayout, RawPage, base_parser, build_document, finalize, make_meta,
    parse_args, register_source, sha256_file,
)


@dataclass(frozen=True)
class SiteProfile:
    content_selector: str
    block_selector: str = "p, li, dd"
    heading_selector: str = "h2, h3"
    remove_selector: str = (
        "script, style, noscript, .mw-editsection, .reference, "
        ".mw-cite-backlink, .noprint, .navbox, .toc"
    )
    parallel_table: bool = False


PROFILES = {
    "wikisource": SiteProfile(".mw-parser-output", parallel_table=True),
    "generic": SiteProfile("main, article, body"),
}
HOST_PROFILES = {
    "vi.wikisource.org": "wikisource",
    "zh.wikisource.org": "wikisource",
    "en.wikisource.org": "wikisource",
}

# Only remove page furniture, not textual annotations or superscripts.
# The default .reference selector is deliberately NOT used for extraction,
# because footnote markers are part of raw text.
SAFE_REMOVE = "script, style, noscript, .mw-editsection, .noprint, .navbox, .toc"
CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\U00020000-\U0002ffff]")


def _lines(node: Tag) -> list[str]:
    """Split only at explicit HTML line breaks, preserving inline text/notes."""
    pieces: list[str] = []
    def walk(n):
        if isinstance(n, NavigableString):
            pieces.append(str(n))
        elif isinstance(n, Tag):
            if n.name == "br":
                pieces.append("\n")
            elif n.name in {"script", "style", "noscript"}:
                return
            else:
                for child in n.children:
                    walk(child)
    walk(node)
    return [re.sub(r"\s+", " ", line).strip() for line in
            "".join(pieces).split("\n") if line.strip()]


def _table_columns(table: Tag) -> tuple[int, int] | None:
    """Identify bilingual column order from header labels, not text heuristics."""
    for tr in table.select("tr")[:5]:
        cells = tr.find_all(["th", "td"], recursive=False)
        names = [c.get_text(" ", strip=True).casefold() for c in cells]
        han = next((i for i, s in enumerate(names)
                    if "chữ hán" in s or "nguyên bản" in s), None)
        am = next((i for i, s in enumerate(names)
                if "phiên âm" in s or "hán việt" in s), None)
        if han is not None and am is not None and han != am:
            return han, am
    return None


def _split_cell(cell: Tag) -> list[list[str]]:
    """One table cell can contain multiple p/li/dd blocks, or plain br lines."""
    blocks = cell.select("p, li, dd")
    # Do not double-count nested blocks.
    blocks = [b for b in blocks if not any(a in blocks for a in b.parents)]
    if blocks:
        return [lines for b in blocks if (lines := _lines(b))]
    lines = _lines(cell)
    return [lines] if lines else []


def _extract_parallel(root: Tag, layer: str) -> tuple[list[RawPage], set[int]]:
    pages: list[RawPage] = []
    used: set[int] = set()
    for table in root.select("table"):
        columns = _table_columns(table)
        if columns is None:
            continue
        used.add(id(table))
        idx = columns[0] if layer == "han" else columns[1]
        for tr in table.select("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) <= idx or cells[idx].name == "th":
                continue
            for lines in _split_cell(cells[idx]):
                pages.append(RawPage(len(pages) + 1, lines))
    return pages, used


def _extract_blocks(root: Tag, profile: SiteProfile, layer: str,
                    excluded_tables: set[int]) -> list[RawPage]:
    pages: list[RawPage] = []
    heading: str | None = None
    # Process headings and blocks in DOM order; exclude nested duplicates.
    selector = f"{profile.heading_selector}, {profile.block_selector}"
    selected = root.select(selector)
    selected_ids = {id(n) for n in selected}
    for node in selected:
        if any(id(parent) in excluded_tables for parent in node.parents):
            continue
        if node.find_parent("table") and excluded_tables:
            # The bilingual source has been handled by column extraction.
            continue
        if any(id(parent) in selected_ids for parent in node.parents):
            continue
        if soupsieve.match(profile.heading_selector, node):
            heading = node.get_text(" ", strip=True) or heading
            continue
        lines = _lines(node)
        if lines:
            pages.append(RawPage(len(pages) + 1, lines, heading=heading))
            heading = None
    return pages


def parse_html(html: bytes, layer: str, profile: SiteProfile) -> list[RawPage]:
    if layer not in {"han", "am", "nghia", "tho"}:
        raise ValueError(f"Unsupported layer: {layer}")
    soup = BeautifulSoup(html, "html.parser")
    root = (soup.select_one("#mw-content-text .mw-parser-output")
            if profile.parallel_table and profile.content_selector == ".mw-parser-output"
            else None) or soup.select_one(profile.content_selector)
    if root is None:
        raise ValueError(f"Main content not found: {profile.content_selector}")
    for el in list(root.select(profile.remove_selector)):
        el.decompose()
    if profile.parallel_table and layer in {"han", "am"}:
        pages, used = _extract_parallel(root, layer)
        if pages:
            return pages
        raise ValueError("No bilingual table with Chinese/phonetic headers found; refusing to mix layers")
    pages = _extract_blocks(root, profile, layer, set())
    if not pages:
        raise ValueError("No text blocks found")
    return pages


def fetch(url: str, destination: Path, force: bool = False) -> dict:
    if destination.exists() and not force:
        raise FileExistsError(f"Immutable source already exists: {destination}")
    if urlparse(url).scheme not in {"http", "https"}:
        raise ValueError("Only HTTP(S) URLs are accepted")
    response = requests.get(url, timeout=40, headers={
        "User-Agent": "CorpusWebExtractor/1.0 (educational text research)"})
    response.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Never silently replace an original source, including with --force.
    if destination.exists():
        raise FileExistsError(f"Original HTML must not be overwritten: {destination}")
    destination.write_bytes(response.content)
    return {"url": url, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sha256": sha256_file(destination), "bytes": destination.stat().st_size}


def main(argv=None):
    parser = base_parser("Web HTML -> corpus raw XML")
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--url", action="append", help="Repeat for multi-page work")
    sources.add_argument("--html", type=Path, help="Offline saved HTML")
    parser.add_argument("--profile", choices=sorted(PROFILES))
    parser.add_argument("--selector")
    parser.add_argument("--block-selector")
    parser.add_argument("--heading-selector")
    parser.add_argument("--remove", help="Additional elements to remove (CSS)")
    args = parse_args(parser, argv)
    layout = DataLayout(Path(args.data_root), args.work_id)
    layout.ensure()
    input_paths: list[Path] = []
    fetch_records: list[dict] = []
    if args.url:
        for i, url in enumerate(args.url, 1):
            filename = (f"{args.layer}.html" if len(args.url) == 1
                        else f"{args.layer}_{i:03d}.html")
            path = layout.sources / filename
            fetch_records.append(fetch(url, path))
            input_paths.append(path)
    else:
        input_paths = [args.html]

    url_for_profile = args.url[0] if args.url else ""
    profile_name = args.profile or HOST_PROFILES.get(
        urlparse(url_for_profile).hostname,
        "wikisource" if args.html and "wikisource" in args.html.name.lower() else "generic",
    )
    base = PROFILES[profile_name]
    profile = SiteProfile(
        content_selector=args.selector or base.content_selector,
        block_selector=args.block_selector or base.block_selector,
        heading_selector=args.heading_selector or base.heading_selector,
        remove_selector=SAFE_REMOVE + (", " + args.remove if args.remove else ""),
        parallel_table=base.parallel_table,
    )
    all_pages: list[RawPage] = []
    source_info: list[dict] = []
    for path in input_paths:
        info = register_source(layout, path)
        source_info.append(info)
        parsed = parse_html(path.read_bytes(), args.layer, profile)
        for page in parsed:
            page.source_page = len(all_pages) + 1
            all_pages.append(page)
    meta = make_meta(args.layer, title=args.title or "Chinh phụ ngâm",
                    source=args.source_label or (args.url[0] if args.url else str(args.html)),
                    method="Web HTML (saved-source parse)",
                    version=args.version, translator=args.translator)
    doc = build_document(args.work_id, args.layer, meta, all_pages)
    out = finalize(doc, layout,
                source_info[0] if len(source_info) == 1 else {"files": source_info},
                {"profile": profile_name, "fetches": fetch_records,
                    "source_hashes": [s.get("sha256") for s in source_info]},
                force=args.force)
    print(f"Saved {out} ({doc.n_units} units; {sum(len(s.pages) for s in doc.sections)} blocks)")


if __name__ == "__main__":
    main()