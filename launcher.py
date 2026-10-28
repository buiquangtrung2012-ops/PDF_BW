"""
Điểm khởi động CỐ ĐỊNH của PDF BW Studio (đây là script mà PyInstaller đóng gói thành .exe).
Việc của nó chỉ là chọn đúng thư mục mã (bản cập nhật hoặc bản đi kèm) rồi chạy bwapp.main.main().
Mọi tính năng/sửa lỗi nằm trong gói bwapp/ và được cập nhật bằng file vá, không cần build lại.
"""
import importlib
import sys

import launcher_core


def _purge():
    for name in [m for m in sys.modules if m == launcher_core.PACKAGE or m.startswith(launcher_core.PACKAGE + ".")]:
        del sys.modules[name]


def _load(root):
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    return importlib.import_module(launcher_core.PACKAGE + ".main")


def run():
    root, _ver, is_update = launcher_core.select_root()
    try:
        mod = _load(root)
    except Exception as e:                      # bản cập nhật hỏng ngay lúc nạp -> dùng bản đi kèm
        if not is_update:
            raise
        sys.path[:] = [p for p in sys.path if p != root]
        _purge()
        mod = _load(launcher_core.fallback_to_bundled(repr(e)))
    mod.main()


if __name__ == "__main__":
    run()
