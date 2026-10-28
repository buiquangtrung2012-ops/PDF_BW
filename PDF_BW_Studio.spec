# -*- mode: python ; coding: utf-8 -*-
# Build dạng THƯ MỤC (onedir). Điểm vào là launcher.py (lõi cố định).
# Mã ứng dụng (gói bwapp/) được đóng gói dạng file .py thường vào code/bwapp để có thể thay bằng bản vá.
# PyInstaller không tự thấy các import trong bwapp (vì nạp động), nên ở đây:
#   1) quét bwapp/*.py để lấy toàn bộ import  -> hiddenimports
#   2) thêm cả thư viện chuẩn của Python và vài module Qt dự phòng, để bản vá sau này dùng thêm chúng mà không phải build lại
import ast
import importlib.util
import os
import sys

ROOT = os.path.abspath(SPECPATH)
PKG = os.path.join(ROOT, 'bwapp')

if not os.path.isfile(os.path.join(ROOT, 'update_pubkey.txt')):
    raise SystemExit('Thieu update_pubkey.txt - hay chay: python tools\\publish_update.py keygen')


def _exists(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


# 1) import thật sự của mã ứng dụng
found = set()
for fn in os.listdir(PKG):
    if not fn.endswith('.py'):
        continue
    with open(os.path.join(PKG, fn), encoding='utf-8') as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
            found.update(f'{node.module}.{a.name}' for a in node.names)
hidden = {n for n in found if not n.split('.')[0] == 'bwapp' and _exists(n)}

# 2) dự phòng cho các bản vá sau: toàn bộ thư viện chuẩn (trừ phần nặng/vô ích) + vài module Qt nhẹ
STD_SKIP = {'tkinter', 'turtle', 'turtledemo', 'idlelib', 'test', 'lib2to3', 'ensurepip', 'venv',
            'distutils', 'pydoc_data', 'antigravity', 'this', 'msilib'}
for name in sys.stdlib_module_names:
    if not name.startswith('_') and name not in STD_SKIP and _exists(name):
        hidden.add(name)
for name in ('PySide6.QtSvg', 'PySide6.QtPrintSupport', 'PySide6.QtNetwork'):
    if _exists(name):
        hidden.add(name)

# 3) file dữ liệu: gói bwapp (không kèm __pycache__) + icon + khóa công khai xác minh bản vá
datas = [('icon.png', '.'), ('icon.ico', '.'), ('update_pubkey.txt', '.')]
for dp, dn, fs in os.walk(PKG):
    dn[:] = [d for d in dn if d != '__pycache__']
    rel = os.path.relpath(dp, ROOT)
    for f in fs:
        if not f.endswith(('.pyc', '.pyo')):
            datas.append((os.path.join(dp, f), os.path.join('code', rel)))

a = Analysis(
    ['launcher.py'],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=sorted(hidden),
    hookspath=[],
    runtime_hooks=[],
    excludes=['pymupdf.layout', 'pymupdf4llm', 'onnxruntime', 'onnx', 'presidio_analyzer',
              'tkinter', 'flask', 'matplotlib', 'scipy', 'pandas',
              'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
              'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.Qt3DCore',
              'PySide6.QtMultimedia', 'PySide6.QtCharts', 'PySide6.QtSql'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PDF_BW_Studio',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon='icon.ico',
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name='PDF_BW_Studio',
)
