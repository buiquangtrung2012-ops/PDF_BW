"""Kiểm tra và tải bản cập nhật (thuần Python, không phụ thuộc Qt). Việc xác minh/cài nằm ở launcher_core."""
import json
import os
import tempfile
import urllib.parse
import urllib.request

from . import version

TIMEOUT = 12
MAX_MANIFEST_BYTES = 256 * 1024
UA = f"PDFBWStudio/{version.__version__}"


class UpdateCheckError(Exception):
    pass


def _check_url(url: str):
    u = urllib.parse.urlparse(url)
    local = u.hostname in ("localhost", "127.0.0.1")
    if u.scheme != "https" and not (u.scheme == "http" and local):
        raise UpdateCheckError("Địa chỉ cập nhật phải dùng https.")


def _open(url):
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    try:
        return urllib.request.urlopen(req, timeout=TIMEOUT)
    except Exception as e:
        raise UpdateCheckError(f"Không kết nối được máy chủ cập nhật ({e.__class__.__name__}). Kiểm tra mạng rồi thử lại.")


def fetch_manifest(url: str) -> dict:
    """Đọc version.json. Trả về dict đã chuẩn hóa: version, url (tuyệt đối), notes, requires_api."""
    with _open(url) as r:
        raw = r.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateCheckError("File version.json quá lớn.")
    try:
        m = json.loads(raw.decode("utf-8-sig"))
        ver, patch_url = str(m["version"]), str(m["url"])
    except Exception:
        raise UpdateCheckError("File version.json trên máy chủ không đúng định dạng.")
    notes = m.get("notes", "")
    return {
        "version": ver,
        "url": urllib.parse.urljoin(url, patch_url),      # cho phép ghi đường dẫn tương đối
        "notes": notes if isinstance(notes, str) else "\n".join(map(str, notes)),
        "requires_api": int(m.get("requires_api", 1)),
    }


def download(url: str, progress=None, cancelled=None, max_bytes=60 * 1024 * 1024) -> str:
    """Tải file .bwpatch về thư mục tạm, trả về đường dẫn. progress(đã_tải, tổng)."""
    fd, path = tempfile.mkstemp(suffix=".bwpatch", prefix="pdfbw_")
    try:
        with _open(url) as r, os.fdopen(fd, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            if total > max_bytes:
                raise UpdateCheckError("File cập nhật quá lớn.")
            done = 0
            while True:
                if cancelled and cancelled():
                    raise UpdateCheckError("Đã hủy tải.")
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                done += len(chunk)
                if done > max_bytes:
                    raise UpdateCheckError("File cập nhật quá lớn.")
                out.write(chunk)
                if progress:
                    progress(done, total)
        return path
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
