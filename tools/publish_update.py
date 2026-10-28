"""
Công cụ phát hành bản cập nhật cho PDF BW Studio (chỉ dùng Python chuẩn, không cần cài thêm gì).

  python tools/publish_update.py keygen                 # chạy 1 LẦN DUY NHẤT: tạo cặp khóa ký
  python tools/publish_update.py pack --notes "Sửa lỗi X, thêm Y"

pack sẽ đọc phiên bản trong bwapp/version.py, đóng gói thư mục bwapp/ thành gói vá, ký bằng khóa bí mật
và tạo ra thư mục release/ gồm:
    PDF_BW_Studio_<phiên bản>.bwpatch   gói vá đã ký (gửi trực tiếp cho người dùng cũng cài được)
    version.json                         file mô tả bản mới nhất, để app tự kiểm tra cập nhật
"""
import argparse
import base64
import io
import json
import os
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ed25519                      # noqa: E402
import launcher_core as core        # noqa: E402

KEY_FILE = os.path.join(ROOT, "keys", "update_private.key")
PUB_FILE = os.path.join(ROOT, "update_pubkey.txt")
OUT_DIR = os.path.join(ROOT, "release")


def die(msg):
    print(f"[LOI] {msg}")
    sys.exit(1)


def cmd_keygen(_a):
    if os.path.exists(KEY_FILE):
        die(f"Đã có khóa tại {KEY_FILE}. Không ghi đè để tránh làm hỏng các bản cài hiện có.")
    secret = os.urandom(32)
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    with open(KEY_FILE, "w", encoding="ascii") as f:
        f.write(base64.b64encode(secret).decode())
    with open(PUB_FILE, "w", encoding="ascii") as f:
        f.write(base64.b64encode(ed25519.public_key(secret)).decode())
    print("Đã tạo cặp khóa.")
    print(f"  Khóa BÍ MẬT : {KEY_FILE}   <-- SAO LƯU Ở NƠI AN TOÀN, KHÔNG CHIA SẺ, KHÔNG ĐƯA LÊN MẠNG")
    print(f"  Khóa công khai: {PUB_FILE}   (được đóng vào bộ cài ở lần build kế tiếp)")
    print("Mất khóa bí mật = không thể phát hành bản vá cho các bản cài cũ (phải build + phát hành lại bộ cài).")


def _load_secret():
    try:
        with open(KEY_FILE, encoding="ascii") as f:
            secret = base64.b64decode(f.read().strip())
        with open(PUB_FILE, encoding="ascii") as f:
            pub = base64.b64decode(f.read().strip())
    except OSError:
        die("Chưa có khóa ký. Chạy trước:  python tools/publish_update.py keygen")
    if ed25519.public_key(secret) != pub:
        die("Khóa bí mật không khớp update_pubkey.txt. Các bản đã cài sẽ TỪ CHỐI bản vá này.")
    return secret


def cmd_pack(a):
    secret = _load_secret()
    ver = core.read_version(ROOT)
    if not ver:
        die("Không đọc được __version__ trong bwapp/version.py")
    notes = a.notes
    if a.notes_file:
        with open(a.notes_file, encoding="utf-8") as f:
            notes = f.read().strip()

    pkg = os.path.join(ROOT, core.PACKAGE)
    files = []
    for dp, dn, fs in os.walk(pkg):
        dn[:] = sorted(d for d in dn if d != "__pycache__")
        for fn in sorted(fs):
            if not fn.endswith((".pyc", ".pyo")):
                files.append(os.path.join(dp, fn))
    for fp in files:
        if fp.endswith(".py"):
            with open(fp, "rb") as f:
                try:
                    compile(f.read(), fp, "exec")
                except SyntaxError as e:
                    die(f"Lỗi cú pháp trong {os.path.relpath(fp, ROOT)} dòng {e.lineno}: {e.msg}")

    meta = {"version": ver, "requires_api": a.requires_api or core.LAUNCHER_API, "notes": notes,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("patch.json", json.dumps(meta, ensure_ascii=False, indent=1))
        for fp in files:
            z.write(fp, os.path.relpath(fp, ROOT).replace(os.sep, "/"))
    body = buf.getvalue()
    sig = ed25519.sign(secret, body)

    os.makedirs(OUT_DIR, exist_ok=True)
    name = f"PDF_BW_Studio_{ver}.bwpatch"
    out = os.path.join(OUT_DIR, name)
    with open(out, "wb") as f:
        f.write(core.PATCH_MAGIC + sig + body)
    with open(os.path.join(OUT_DIR, "version.json"), "w", encoding="utf-8") as f:
        json.dump({"version": ver, "url": name, "notes": notes, "requires_api": meta["requires_api"]},
                  f, ensure_ascii=False, indent=1)

    assert core.verify_patch(open(out, "rb").read()) == body      # tự kiểm tra lại bằng đúng code của app
    print(f"Đã tạo bản vá {ver}  ({os.path.getsize(out) / 1024:.0f} KB, {len(files)} file)")
    print(f"  {out}")
    print(f"  {os.path.join(OUT_DIR, 'version.json')}")
    print("Tải CẢ HAI file trong thư mục release/ lên nơi lưu trữ (xem README, mục 'Phát hành cập nhật').")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("keygen").set_defaults(fn=cmd_keygen)
    p = sp.add_parser("pack")
    p.add_argument("--notes", default="", help="Ghi chú phiên bản hiển thị cho người dùng")
    p.add_argument("--notes-file", help="Đọc ghi chú từ file văn bản")
    p.add_argument("--requires-api", type=int, default=0)
    p.set_defaults(fn=cmd_pack)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
