"""
PDF -> image -> Drive
Tách PDF thành ảnh từng trang (+ upload Google Drive) để OCR hàng loạt.
 
INPUT
    --source  file .pdf (nếu nằm ngoài 01_sources sẽ được copy vào, giữ nguyên bản gốc)
    --layer   han | am | nghia | tho   (mỗi lớp có thư mục ảnh riêng, tránh trùng tên trang)
    --start N --end M   trang bắt đầu / kết thúc (1-based, mặc định 1 .. hết)
    --pages "205-210,212"   thay thế cho --start/--end khi cần chọn trang rời rạc
    --dpi 300  --workers 4  --format png|jpg
    --upload none|drive|sync  (sync: chép vào My Drive đang được Drive for desktop đồng bộ)
    --drive-folder-id ID    thư mục cha trên Google Drive (bắt buộc khi --upload drive)
    --drive-credentials FILE  OAuth client secret JSON hoặc service-account JSON
    --drive-token FILE        nơi lưu token OAuth (mặc định drive_token.json)
    --drive-sync-dir DIR      thư mục My Drive cục bộ (bắt buộc khi --upload sync)
    --poppler-path DIR        thư mục chứa pdfinfo/pdftoppm nếu không muốn dùng PATH
 
OUTPUT (data/<WORK_ID>/)
    02_extraction/01_page_image/<layer>/page_205.png   ảnh từng trang, số trang tối thiểu 3 chữ số
    Google Drive: <drive-folder>/<WORK_ID>_<layer>/page_205.png   (khi --upload drive)
    Drive for desktop: <drive-sync-dir>/<WORK_ID>_<layer>/page_205.png (khi --upload sync)
    02_extraction/metadata.json -> layers["<layer>_images"]:
        nguồn + sha256, tổng số trang PDF, tham số, danh sách ảnh
        [{"page", "file", "drive_id", "drive_url"}]  <- dùng làm manifest cho OCR hàng loạt
 
Chạy lại là tiếp tục: ảnh đã có / đã upload (theo manifest) được bỏ qua; --force để làm lại.
Việc OCR không làm ở đây: lấy ảnh từ Drive (hoặc thư mục local) đưa vào src/ocr/ocr.py
hoặc notebook OCR.
 
Ví dụ:
    python -m src.extraction.pdf_extraction --layer han --work-id CPO_18 \
        --source data/CPO_18/01_sources/book.pdf --start 205 --end 400
    python -m src.extraction.pdf_extraction --layer han --work-id CPO_18 \
        --source data/CPO_18/01_sources/book.pdf --start 205 --end 400 \
        --upload drive --drive-folder-id <ID> --drive-credentials credentials.json
    python -m src.extraction.pdf_extraction --config configs/CPO_18.json --layer nghia
 
Cần: poppler (pdftoppm), pdf2image; upload cần google-api-python-client, google-auth-oauthlib.
 
IMPORTANT!: Dùng các hàm đã defined sẵn trong src/common/corpus.py (parse_pages, DataLayout,
register_source, MetadataStore, base_parser, parse_args). Việc tách ảnh không sinh XML nên
không dùng build_document/finalize/RawPage/parse_region/filter_lines.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import shutil
import uuid
from pathlib import Path

from pdf2image import convert_from_path, pdfinfo_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError
from PIL import Image, UnidentifiedImageError

from src.common.corpus import (
    DataLayout,
    MetadataStore,
    base_parser,
    parse_args,
    parse_pages,
    register_source,
    sha256_file,
    write_json,
)


log = logging.getLogger(__name__)
DRIVE_SCOPES = [
    # The script receives an existing folder ID directly and does not use Drive Picker.
    "https://www.googleapis.com/auth/drive",
]


def _poppler_candidates(explicit: str | None) -> list[Path | None]:
    if explicit:
        return [Path(explicit).expanduser().resolve()]
    candidates: list[Path | None] = []
    binary = "pdfinfo.exe" if os.name == "nt" else "pdfinfo"
    renderer = "pdftoppm.exe" if os.name == "nt" else "pdftoppm"
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        folder = Path(entry.strip('"'))
        if (folder / binary).is_file() and (folder / renderer).is_file():
            resolved = folder.resolve()
            if resolved not in candidates:
                candidates.append(resolved)
    if not candidates:
        candidates.append(None)
    return candidates


def _pdf_info(source: Path, explicit_poppler: str | None) -> tuple[int, Path | None]:
    failures = []
    for folder in _poppler_candidates(explicit_poppler):
        try:
            info = pdfinfo_from_path(str(source), poppler_path=str(folder) if folder else None)
            total = int(info["Pages"])
            if total < 1:
                raise ValueError("PDF không có trang")
            log.info("Poppler: %s", folder or "PATH")
            return total, folder
        except (OSError, ValueError, KeyError, RuntimeError,
                PDFInfoNotInstalledError, PDFPageCountError) as exc:
            failures.append(f"{folder or 'PATH'}: {exc}")
    raise RuntimeError("Không đọc được số trang PDF bằng Poppler. " + " | ".join(failures))


def _selected_pages(args, total: int) -> list[int]:
    if args.pages:
        for part in args.pages.split(","):
            if not re.fullmatch(r"\s*\d+\s*(?:-\s*\d+\s*)?", part):
                raise ValueError(f"--pages không hợp lệ: {part!r}")
            bounds = [int(value) for value in part.split("-")]
            if len(bounds) == 2 and bounds[0] > bounds[1]:
                raise ValueError(f"Khoảng trang ngược: {part!r}")
        return parse_pages(args.pages, total)
    start = 1 if args.start is None else args.start
    end = total if args.end is None else args.end
    if start < 1 or end < start:
        raise ValueError("Cần 1 <= --start <= --end")
    return parse_pages(f"{start}-{end}", total)


def _valid_image(path: Path, fmt: str) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        with Image.open(path) as image:
            image.verify()
            return image.format == ("JPEG" if fmt == "jpg" else "PNG")
    except PermissionError:
        # Windows may deny reads while VS Code's image preview has the file open.
        # Keep a non-empty existing output instead of trying to replace a locked file.
        return path.is_file() and path.stat().st_size > 0
    except (OSError, ValueError, UnidentifiedImageError):
        return False


def _batches(pages: list[int], size: int = 16):
    batch: list[int] = []
    for page in pages:
        if batch and (page != batch[-1] + 1 or len(batch) == size):
            yield batch
            batch = []
        batch.append(page)
    if batch:
        yield batch


def _page_filename(page: int, fmt: str) -> str:
    return f"page_{page:03d}.{fmt}"


def _render_batch(
    source: Path, pages: list[int], output_dir: Path, fmt: str,
    dpi: int, workers: int, poppler_path: Path | None,
) -> None:
    temporary_dir = output_dir / f".pdf_pages_{uuid.uuid4().hex}"
    temporary_dir.mkdir()
    try:
        files = convert_from_path(
            str(source), dpi=dpi, first_page=pages[0], last_page=pages[-1],
            fmt="jpeg" if fmt == "jpg" else "png", output_folder=str(temporary_dir),
            paths_only=True, thread_count=min(workers, len(pages)),
            poppler_path=str(poppler_path) if poppler_path else None,
        )
        if len(files) != len(pages):
            raise RuntimeError(f"Poppler trả {len(files)} ảnh cho {len(pages)} trang")
        for page, temporary_file in zip(pages, files):
            temporary_path = Path(temporary_file)
            if not _valid_image(temporary_path, fmt):
                raise RuntimeError(f"Ảnh trang {page} không hợp lệ: {temporary_path}")
            os.replace(temporary_path, output_dir / _page_filename(page, fmt))
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)


def _drive_service(credentials_path: Path, token_path: Path):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import credentials as user_credentials
        from google.oauth2 import service_account
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Upload Drive cần google-api-python-client và google-auth-oauthlib"
        ) from exc

    kind = json.loads(credentials_path.read_text(encoding="utf-8")).get("type")
    if kind == "service_account":
        creds = service_account.Credentials.from_service_account_file(
            str(credentials_path), scopes=DRIVE_SCOPES
        )
    elif kind in ("installed", "web", None):
        creds = None
        if token_path.is_file():
            creds = user_credentials.Credentials.from_authorized_user_file(
                str(token_path), DRIVE_SCOPES
            )
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), DRIVE_SCOPES
            )
            creds = flow.run_local_server(port=0)
        write_json(token_path, json.loads(creds.to_json()))
    else:
        raise ValueError("--drive-credentials phải là OAuth client JSON hoặc service-account JSON")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _drive_find(service, parent_id: str, name: str, mime_type: str | None = None):
    def quoted(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    query = f"'{quoted(parent_id)}' in parents and name = '{quoted(name)}' and trashed = false"
    if mime_type:
        query += f" and mimeType = '{quoted(mime_type)}'"
    found = []
    page_token = None
    while True:
        response = service.files().list(
            q=query, spaces="drive", fields="nextPageToken,files(id,name,mimeType,webViewLink)",
            pageSize=1000, pageToken=page_token, supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute(num_retries=3)
        found.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    if len(found) > 1:
        raise RuntimeError(f"Drive có nhiều file cùng tên {name!r} trong thư mục {parent_id}")
    return found[0] if found else None


def _drive_folder(service, parent_id: str, name: str) -> str:
    folder_type = "application/vnd.google-apps.folder"
    existing = _drive_find(service, parent_id, name, folder_type)
    if existing:
        return existing["id"]
    created = service.files().create(
        body={"name": name, "mimeType": folder_type, "parents": [parent_id]},
        fields="id", supportsAllDrives=True,
    ).execute(num_retries=3)
    return created["id"]


def _drive_upload(service, folder_id: str, path: Path, existing_id: str | None):
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(
        str(path), mimetype="image/jpeg" if path.suffix == ".jpg" else "image/png",
        resumable=True,
    )
    if existing_id:
        request = service.files().update(
            fileId=existing_id, body={"name": path.name}, media_body=media,
            fields="id,webViewLink",
            supportsAllDrives=True,
        )
    else:
        request = service.files().create(
            body={"name": path.name, "parents": [folder_id]}, media_body=media,
            fields="id,webViewLink", supportsAllDrives=True,
        )
    response = None
    while response is None:
        _, response = request.next_chunk(num_retries=3)
    return response


def _sync_to_drive_desktop(
    source_dir: Path,
    sync_root: Path,
    work_id: str,
    layer: str,
    pages: list[int],
    fmt: str,
    force: bool,
) -> tuple[Path, dict[int, Path], int]:
    """Copy page images into a local Drive for desktop folder for background sync."""
    sync_root = sync_root.expanduser().resolve()
    if not sync_root.is_dir():
        raise NotADirectoryError(
            f"Không tìm thấy thư mục Drive for desktop: {sync_root}. "
            "Hãy đăng nhập Drive for desktop và truyền thư mục nằm trong My Drive."
        )
    target_dir = sync_root / f"{work_id}_{layer}"
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[int, Path] = {}
    count = 0
    for page in pages:
        source = source_dir / _page_filename(page, fmt)
        target = target_dir / source.name
        if not source.is_file():
            raise FileNotFoundError(f"Thiếu ảnh đầu vào để đồng bộ: {source}")
        same_content = (
            target.is_file()
            and source.stat().st_size == target.stat().st_size
            and sha256_file(source) == sha256_file(target)
        )
        if force or not same_content:
            shutil.copy2(source, target)
            count += 1
        copied[page] = target
    return target_dir, copied, count


def run(args) -> dict[str, int]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.work_id):
        raise ValueError("--work-id chỉ được chứa chữ, số, dấu _ và -")
    if not args.source:
        raise ValueError("Thiếu --source (CLI hoặc config)")
    if args.dpi < 1 or args.workers < 1:
        raise ValueError("--dpi và --workers phải lớn hơn 0")
    if args.upload == "drive" and (not args.drive_folder_id or not args.drive_credentials):
        raise ValueError("--upload drive cần --drive-folder-id và --drive-credentials")
    if args.upload == "sync" and not args.drive_sync_dir:
        raise ValueError("--upload sync cần --drive-sync-dir")
    credentials = None
    if args.upload == "drive":
        credentials = Path(args.drive_credentials).expanduser().resolve()
        if not credentials.is_file():
            raise FileNotFoundError(
                f"Không tìm thấy Drive credentials: {credentials}. "
                "Hãy đặt OAuth client JSON hoặc service-account JSON tại đường dẫn này."
            )

    source = Path(args.source).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError(f"Nguồn phải là file PDF có thật: {source}")
    layout = DataLayout(Path(args.data_root), args.work_id)
    destination = layout.sources.resolve() / source.name
    if source != destination and destination.is_file() and sha256_file(source) != sha256_file(destination):
        raise FileExistsError(f"Nguồn khác nội dung nhưng trùng tên với {destination}")
    source_info = register_source(layout, source)
    stored_source = layout.work_dir / source_info["path"]
    total, poppler_path = _pdf_info(stored_source, args.poppler_path)
    pages = _selected_pages(args, total)

    output_dir = layout.page_images / args.layer
    layout.ensure(output_dir)
    store = MetadataStore(layout)
    data = store.load()
    if "layers" not in data:
        data["layers"] = {}
        write_json(layout.metadata_path, data)
    elif not isinstance(data["layers"], dict):
        raise ValueError("metadata.json: 'layers' phải là object")

    key = f"{args.layer}_images"
    previous = data["layers"].get(key, {})
    if previous and not isinstance(previous, dict):
        raise ValueError(f"metadata.json: {key} phải là object")
    parameters = {"dpi": args.dpi, "format": args.format}
    old_parameters = previous.get("parameters", {})
    incompatible = bool(previous) and (
        previous.get("source", {}).get("sha256") != source_info["sha256"]
        or {k: old_parameters.get(k) for k in parameters} != parameters
    )
    if incompatible and not args.force:
        raise ValueError("Nguồn, DPI hoặc định dạng đã đổi; dùng --force để tạo lại ảnh")

    old_images = {entry["page"]: entry for entry in previous.get("images", [])}
    images = {} if incompatible else old_images.copy()
    old_parent = previous.get("drive_parent_id")
    if args.upload == "drive" and old_parent and old_parent != args.drive_folder_id:
        for entry in images.values():
            entry = entry.copy()
            entry.update(drive_id=None, drive_url=None)
            images[entry["page"]] = entry
    record = {
        "source": source_info,
        "total_pages": total,
        "parameters": {**parameters, "workers": args.workers},
        "pages": sorted(images),
        "images": [images[p] for p in sorted(images)],
        "drive_parent_id": old_parent,
        "drive_folder_id": previous.get("drive_folder_id")
        if not incompatible and (args.upload != "drive" or old_parent == args.drive_folder_id)
        else None,
    }

    def save() -> None:
        record["pages"] = sorted(images)
        record["images"] = [images[p] for p in sorted(images)]
        store.update(key, record)

    to_render = []
    reused = 0
    for page in pages:
        path = output_dir / _page_filename(page, args.format)
        legacy_path = output_dir / f"page_{page:04d}.{args.format}"
        if (
            not args.force and not path.exists() and legacy_path != path
            and _valid_image(legacy_path, args.format)
        ):
            try:
                os.replace(legacy_path, path)
            except PermissionError:
                shutil.copy2(legacy_path, path)
                try:
                    legacy_path.unlink()
                except OSError:
                    log.warning("Ảnh cũ đang được mở, chưa xóa được: %s", legacy_path)
            if page in images:
                images[page] = {
                    **images[page], "file": layout.rel(path),
                    "drive_id": None, "drive_url": None,
                }
        if args.force or not _valid_image(path, args.format):
            to_render.append(page)
        else:
            reused += 1
            if page not in images:
                images[page] = {
                    "page": page, "file": layout.rel(path),
                    "drive_id": None, "drive_url": None,
                }
            elif images[page].get("file") != layout.rel(path):
                images[page] = {
                    **images[page], "file": layout.rel(path),
                    "drive_id": None, "drive_url": None,
                }
    save()
    for batch in _batches(to_render):
        _render_batch(stored_source, batch, output_dir, args.format,
                      args.dpi, args.workers, poppler_path)
        for page in batch:
            path = output_dir / _page_filename(page, args.format)
            images[page] = {
                "page": page, "file": layout.rel(path),
                "drive_id": None, "drive_url": None,
            }
            old_format = old_parameters.get("format")
            if args.force and old_format in ("png", "jpg") and old_format != args.format:
                for old_path in {
                    output_dir / _page_filename(page, old_format),
                    output_dir / f"page_{page:04d}.{old_format}",
                }:
                    if old_path != path:
                        old_path.unlink(missing_ok=True)
            legacy_path = output_dir / f"page_{page:04d}.{args.format}"
            if legacy_path != path:
                try:
                    legacy_path.unlink(missing_ok=True)
                except OSError:
                    log.warning("Ảnh tên cũ đang được mở, chưa xóa được: %s", legacy_path)
        save()
        log.info("Đã tạo ảnh trang %s..%s", batch[0], batch[-1])

    uploaded = 0
    synced = 0
    if args.upload == "drive":
        pending = [p for p in pages if args.force or not images[p].get("drive_id")]
        if pending:
            service = _drive_service(credentials, Path(args.drive_token).expanduser())
            folder_id = _drive_folder(service, args.drive_folder_id,
                                      f"{args.work_id}_{args.layer}")
            record["drive_parent_id"] = args.drive_folder_id
            record["drive_folder_id"] = folder_id
            save()
            for page in pending:
                path = output_dir / _page_filename(page, args.format)
                old_id = None
                if old_parent == args.drive_folder_id:
                    old_id = old_images.get(page, {}).get("drive_id")
                mime_type = "image/jpeg" if args.format == "jpg" else "image/png"
                existing = None if old_id else _drive_find(
                    service, folder_id, path.name, mime_type
                )
                result = _drive_upload(service, folder_id, path,
                                       old_id or (existing or {}).get("id"))
                drive_id = result["id"]
                images[page]["drive_id"] = drive_id
                images[page]["drive_url"] = result.get("webViewLink") or (
                    f"https://drive.google.com/file/d/{drive_id}/view"
                )
                save()
                uploaded += 1
                log.info("Đã upload trang %s: %s", page, drive_id)

    sync_folder = None
    if args.upload == "sync":
        sync_folder, sync_paths, synced = _sync_to_drive_desktop(
            output_dir, Path(args.drive_sync_dir), args.work_id, args.layer,
            pages, args.format, args.force,
        )
        record["drive_sync"] = {
            "method": "Google Drive for desktop",
            "local_folder": str(sync_folder),
            "status": "copied; cloud sync is handled by Drive for desktop",
        }
        for page, path in sync_paths.items():
            images[page]["sync_path"] = str(path)
            images[page]["sync_status"] = "copied_to_drive_desktop"
        save()
        log.info("Đã chép %s ảnh vào thư mục Drive for desktop: %s", len(pages), sync_folder)

    result = {
        "rendered": len(to_render), "reused": reused,
        "uploaded": uploaded, "synced": synced,
    }
    log.info("Hoàn tất %s/%s: %s", args.work_id, args.layer, result)
    return result


def main(argv: list[str] | None = None) -> None:
    if os.name == "nt" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = base_parser("Tách PDF thành ảnh trang và tùy chọn upload Google Drive")
    parser.add_argument("--source", help="File PDF nguồn")
    parser.add_argument("--start", type=int, help="Trang đầu (1-based)")
    parser.add_argument("--end", type=int, help="Trang cuối (1-based)")
    parser.add_argument("--pages", help="Trang rời rạc, vd 205-210,212")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--format", choices=("png", "jpg"), default="png")
    parser.add_argument("--upload", choices=("none", "drive", "sync"), default="none")
    parser.add_argument("--drive-folder-id", help="ID thư mục cha trên Google Drive")
    parser.add_argument("--drive-credentials", help="OAuth client hoặc service-account JSON")
    parser.add_argument("--drive-token", default="drive_token.json")
    parser.add_argument("--drive-sync-dir", help="Thư mục My Drive cục bộ được Drive for desktop đồng bộ")
    parser.add_argument("--poppler-path", help="Thư mục chứa pdfinfo và pdftoppm")
    args = parse_args(parser, argv)
    try:
        run(args)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
