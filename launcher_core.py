"""
LÕI CỐ ĐỊNH của PDF BW Studio (nằm trong bộ cài, KHÔNG thay đổi qua bản vá).

Nhiệm vụ:
  - chọn mã ứng dụng (gói `bwapp`) để chạy: bản cập nhật đã cài hoặc bản đi kèm bộ cài, bản nào mới hơn;
  - cài bản vá (.bwpatch): kiểm tra chữ ký số Ed25519, kiểm tra cấu trúc, kiểm tra cú pháp rồi mới kích hoạt;
  - tự rollback về bản đi kèm nếu bản cập nhật làm app không khởi động được 2 lần liên tiếp.

Phần việc nhạy cảm về bảo mật (xác minh chữ ký, giải nén) nằm ở đây, nên một bản vá lỗi hay độc hại
không thể tắt hoặc qua mặt nó.

Cấu trúc dữ liệu (mỗi người dùng):  %LOCALAPPDATA%\\PDFBWStudio\\updates\\
    state.json            {"current": "2.2.1", "failures": 0, "bad": []}
    2.2.1\\bwapp\\...      mã của bản cập nhật đã được xác minh
"""
import io
import json
import os
import re
import shutil
import sys
import zipfile

import ed25519

LAUNCHER_API = 1              # tăng khi lõi thay đổi theo cách bản vá cũ/mới không còn tương thích
PATCH_MAGIC = b"BWPAT1"       # file .bwpatch = MAGIC (6 byte) + chữ ký (64 byte) + nội dung zip
MAX_PATCH_BYTES = 60 * 1024 * 1024
MAX_UNPACKED_BYTES = 120 * 1024 * 1024
MAX_FAILURES = 2              # số lần khởi động liên tiếp không được xác nhận trước khi tự rollback
PACKAGE = "bwapp"

DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share"), "PDFBWStudio")
UPDATES_DIR = os.path.join(DATA_DIR, "updates")
STATE_FILE = os.path.join(UPDATES_DIR, "state.json")

BOOT_LOG = []                 # ghi chú khởi động; app sẽ chép vào log.txt
_active = {"root": None, "version": None, "is_update": False}


class UpdateError(Exception):
    """Lỗi cập nhật; nội dung là thông báo tiếng Việt hiển thị thẳng cho người dùng."""


# ----------------------------------------------------------------------
# Tiện ích nhỏ
# ----------------------------------------------------------------------
def _log(msg):
    BOOT_LOG.append(msg)


def vkey(v):
    """'2.10.1' -> (2, 10, 1, 0) để so sánh phiên bản."""
    nums = [int(x) for x in re.findall(r"\d+", str(v))][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def bundled_root():
    """Thư mục chứa gói bwapp đi kèm bộ cài."""
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "code")
    return os.path.dirname(os.path.abspath(__file__))


def read_version(root):
    """Đọc __version__ từ <root>/bwapp/version.py mà không import."""
    try:
        with open(os.path.join(root, PACKAGE, "version.py"), encoding="utf-8") as f:
            m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', f.read(), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def _load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict):
            st.setdefault("failures", 0)
            st.setdefault("bad", [])
            return st
    except (OSError, ValueError):
        pass
    return {"current": None, "failures": 0, "bad": []}


def _save_state(st):
    os.makedirs(UPDATES_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_FILE)


def public_key():
    """Khóa công khai (base64) nằm trong bộ cài, file update_pubkey.txt. Trống = tắt cập nhật."""
    base = getattr(sys, "_MEIPASS", None) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(base, "update_pubkey.txt"), encoding="ascii") as f:
            import base64
            raw = base64.b64decode(f.read().strip())
        return raw if len(raw) == 32 else None
    except Exception:
        return None


# ----------------------------------------------------------------------
# Chọn mã để chạy
# ----------------------------------------------------------------------
def _prune(keep):
    try:
        for name in os.listdir(UPDATES_DIR):
            p = os.path.join(UPDATES_DIR, name)
            if os.path.isdir(p) and name != keep:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def select_root():
    """Trả về (thư mục gốc chứa gói bwapp, phiên bản, là_bản_cập_nhật)."""
    b_root = bundled_root()
    b_ver = read_version(b_root) or "0"
    st = _load_state()
    cur = st.get("current")
    chosen = (b_root, b_ver, False)

    if cur:
        u_root = os.path.join(UPDATES_DIR, cur)
        u_ver = read_version(u_root)
        if not u_ver or cur in st["bad"]:
            _log(f"Bỏ qua bản cập nhật {cur}: không hợp lệ hoặc đã bị đánh dấu lỗi")
        elif vkey(u_ver) <= vkey(b_ver):
            _log(f"Bản đi kèm bộ cài ({b_ver}) mới hơn hoặc bằng bản cập nhật ({u_ver}); dùng bản đi kèm")
        elif st["failures"] >= MAX_FAILURES:
            _log(f"Bản cập nhật {u_ver} không khởi động được {st['failures']} lần liên tiếp -> TỰ ĐỘNG QUAY LẠI bản {b_ver}")
            st["bad"].append(cur)
            st["current"], st["failures"] = None, 0
            _save_state(st)
        else:
            st["failures"] += 1          # sẽ về 0 khi app gọi mark_boot_ok()
            _save_state(st)
            chosen = (u_root, u_ver, True)

    # chỉ giữ thư mục của bản đang dùng, dọn bản cũ (lúc này chưa có gì được nạp nên xóa an toàn)
    _prune(chosen[1] if chosen[2] else None)
    _active.update(root=chosen[0], version=chosen[1], is_update=chosen[2])
    _log(f"Chạy mã {'CẬP NHẬT' if chosen[2] else 'đi kèm bộ cài'} phiên bản {chosen[1]} tại {chosen[0]}")
    return chosen


def fallback_to_bundled(reason):
    """Gọi khi nạp bản cập nhật bị lỗi: đánh dấu hỏng và trả về thư mục bản đi kèm."""
    st = _load_state()
    if _active["is_update"] and st.get("current"):
        st["bad"].append(st["current"])
        st["current"], st["failures"] = None, 0
        _save_state(st)
    _log(f"Bản cập nhật bị lỗi khi nạp ({reason}) -> quay lại bản đi kèm")
    root = bundled_root()
    _active.update(root=root, version=read_version(root), is_update=False)
    return root


def mark_boot_ok():
    """App gọi sau khi khởi động ổn định: xóa bộ đếm lỗi của bản cập nhật."""
    if not _active["is_update"]:
        return
    try:
        st = _load_state()
        if st.get("failures"):
            st["failures"] = 0
            _save_state(st)
    except Exception:
        pass


def active_info():
    return dict(_active, api=LAUNCHER_API)


# ----------------------------------------------------------------------
# Cài bản vá
# ----------------------------------------------------------------------
def installed_version():
    """Phiên bản mã đang có hiệu lực (cao nhất giữa bản đi kèm và bản cập nhật đã cài)."""
    best = read_version(bundled_root()) or "0"
    st = _load_state()
    cur = st.get("current")
    if cur and cur not in st["bad"]:
        v = read_version(os.path.join(UPDATES_DIR, cur))
        if v and vkey(v) > vkey(best):
            best = v
    return best


def _safe_member(name):
    n = name.replace("\\", "/")
    if n.endswith("/"):
        return None
    if n.startswith("/") or ":" in n.split("/")[0]:
        return False
    parts = n.split("/")
    if ".." in parts or "" in parts:
        return False
    if parts[0] == "patch.json":
        return len(parts) == 1
    return parts[0] == PACKAGE


def verify_patch(data: bytes):
    """Kiểm tra chữ ký; trả về nội dung zip. Ném UpdateError nếu có vấn đề."""
    pub = public_key()
    if pub is None:
        raise UpdateError("Bản cài đặt này chưa có khóa xác minh cập nhật (update_pubkey.txt), nên không thể cài bản vá.")
    if len(data) > MAX_PATCH_BYTES:
        raise UpdateError("File cập nhật quá lớn hoặc không hợp lệ.")
    if len(data) < len(PATCH_MAGIC) + 64 or not data.startswith(PATCH_MAGIC):
        raise UpdateError("Đây không phải file cập nhật hợp lệ của PDF BW Studio (.bwpatch).")
    sig = data[len(PATCH_MAGIC):len(PATCH_MAGIC) + 64]
    body = data[len(PATCH_MAGIC) + 64:]
    if not ed25519.verify(pub, body, sig):
        raise UpdateError("CHỮ KÝ KHÔNG HỢP LỆ: file cập nhật không do nhà phát hành ký hoặc đã bị sửa/tải lỗi. Đã từ chối cài.")
    return body


def install_patch(path: str):
    """
    Xác minh và cài bản vá. Trả về dict {version, notes}. Không động đến bản đang chạy;
    bản mới được kích hoạt ở lần khởi động tiếp theo.
    """
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_PATCH_BYTES + 1)
    except OSError as e:
        raise UpdateError(f"Không đọc được file cập nhật: {e}")
    body = verify_patch(data)

    try:
        zf = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        raise UpdateError("File cập nhật bị hỏng (không đọc được nội dung).")
    with zf:
        infos = zf.infolist()
        if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
            raise UpdateError("Nội dung cập nhật quá lớn.")
        for i in infos:
            ok = _safe_member(i.filename)
            if ok is False:
                raise UpdateError(f"Gói cập nhật chứa đường dẫn không hợp lệ: {i.filename}")
            if (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise UpdateError("Gói cập nhật chứa liên kết tượng trưng (symlink) không được phép.")
        try:
            meta = json.loads(zf.read("patch.json").decode("utf-8"))
        except Exception:
            raise UpdateError("Gói cập nhật thiếu patch.json hợp lệ.")

        ver = str(meta.get("version", ""))
        if not re.fullmatch(r"\d+(\.\d+){1,3}", ver):
            raise UpdateError("Số phiên bản trong gói cập nhật không hợp lệ.")
        if int(meta.get("requires_api", 1)) > LAUNCHER_API:
            raise UpdateError(
                f"Bản cập nhật {ver} cần phiên bản lõi mới hơn. Hãy tải và cài lại bộ cài đầy đủ (Setup.exe) một lần.")
        have = installed_version()
        if vkey(ver) <= vkey(have):
            raise UpdateError(f"Bạn đang dùng bản {have}, không cần cài bản {ver}.")

        os.makedirs(UPDATES_DIR, exist_ok=True)
        tmp = os.path.join(UPDATES_DIR, f"_tmp_{ver}")
        final = os.path.join(UPDATES_DIR, ver)
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            for i in infos:
                if i.filename.endswith("/") or i.filename == "patch.json":
                    continue
                dest = os.path.join(tmp, *i.filename.split("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(i) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)

            # kiểm tra trước khi kích hoạt: có file bắt buộc, version khớp, mọi file .py biên dịch được
            if read_version(tmp) != ver:
                raise UpdateError("Số phiên bản trong mã không khớp với patch.json.")
            if not os.path.isfile(os.path.join(tmp, PACKAGE, "main.py")):
                raise UpdateError("Gói cập nhật thiếu bwapp/main.py.")
            for dp, _, fs in os.walk(tmp):
                for fn in fs:
                    if fn.endswith(".py"):
                        fp = os.path.join(dp, fn)
                        try:
                            with open(fp, "rb") as f:
                                compile(f.read(), fp, "exec")
                        except SyntaxError as e:
                            raise UpdateError(f"Lỗi cú pháp trong {fn} (dòng {e.lineno}); đã hủy cài để an toàn.")
            shutil.rmtree(final, ignore_errors=True)
            os.replace(tmp, final)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        st = _load_state()
        st["current"], st["failures"] = ver, 0
        st["bad"] = [b for b in st["bad"] if b != ver]
        _save_state(st)
        notes = meta.get("notes", "")
        return {"version": ver, "notes": notes if isinstance(notes, str) else "\n".join(map(str, notes))}
