"""
PDF Black & White Studio - Native Desktop Edition (PySide6)

Ứng dụng desktop thật, chạy hoàn toàn offline, không có máy chủ web / trình duyệt.
Phần lọc ảnh nằm trong engine.py.
"""

import os
import sys
import re
import json
import logging
import threading
import traceback
import subprocess
from collections import OrderedDict
from logging.handlers import RotatingFileHandler

# PyMuPDF bản mới tự nạp "pymupdf.layout" (mô hình ONNX phân tích bố cục) nếu gói pymupdf-layout có mặt.
# App này không dùng, và PyInstaller không đóng gói được file mô hình, nên chặn hẳn trước khi import pymupdf.
for _m in ("pymupdf.layout", "pymupdf4llm"):
    sys.modules[_m] = None

from PIL import Image
from PySide6.QtCore import (
    Qt, QTimer, QThread, Signal, QObject, QRunnable, QThreadPool,
    QRectF, QPointF, QSettings, QUrl, QSize,
)
from PySide6.QtGui import (
    QImage, QPainter, QColor, QPen, QIcon, QDesktopServices, QKeySequence,
    QShortcut, QBrush, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QSlider, QCheckBox, QComboBox, QLineEdit,
    QScrollArea, QFrame, QStackedWidget, QSplitter, QProgressBar, QFileDialog, QMessageBox,
    QButtonGroup, QSizePolicy, QListWidget, QListWidgetItem, QListView,
    QDialog, QPlainTextEdit, QInputDialog,
)
import pymupdf as fitz

from . import engine
from . import version as _version
from . import update_ui

APP_NAME = "PDF Black & White Studio"
APP_VERSION = _version.__version__
THUMB_W, THUMB_H = 116, 148   # ô ảnh thu nhỏ
FAST_DPI = 80          # độ phân giải xem trước khi đang kéo thanh trượt (cho mượt)


def resource_path(name: str) -> str:
    """Icon và tài nguyên cố định: nằm trong bộ cài (frozen) hoặc thư mục dự án (chạy từ mã nguồn)."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ----------------------------------------------------------------------
# Ghi log ra file + báo lỗi thân thiện (app chạy chế độ cửa sổ nên không có console)
# ----------------------------------------------------------------------
LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share"), "PDFBWStudio")
LOG_FILE = os.path.join(LOG_DIR, "log.txt")


def setup_logging():
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(LOG_FILE, maxBytes=512_000, backupCount=2, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, handlers=[handler],
                            format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
        logging.info("=== %s %s khởi động (Python %s) ===", APP_NAME, APP_VERSION, sys.version.split()[0])
        for line in update_ui.boot_log():
            logging.info("[launcher] %s", line)
    except Exception:
        pass


class ErrorBus(QObject):
    error = Signal(str)


ERROR_BUS = ErrorBus()


def install_excepthooks():
    def hook(t, v, tb):
        logging.error("Lỗi không bắt được", exc_info=(t, v, tb))
        ERROR_BUS.error.emit("".join(traceback.format_exception_only(t, v)).strip())

    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)


def friendly_error(e: Exception) -> str:
    if isinstance(e, PermissionError):
        return ("Không ghi được file. Có thể file đang mở ở chương trình khác (ví dụ trình đọc PDF) "
                "hoặc bạn không có quyền ghi vào thư mục này.")
    if isinstance(e, MemoryError):
        return "Không đủ bộ nhớ. Hãy chọn DPI thấp hơn hoặc xuất ít trang hơn mỗi lần."
    if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
        return "Ổ đĩa đã đầy. Hãy giải phóng dung lượng rồi thử lại."
    return str(e) or e.__class__.__name__


# ----------------------------------------------------------------------
# Tham số mặc định, presets
# ----------------------------------------------------------------------
DEFAULT_PARAMS = {
    "mode": "binarize", "darkness": 65, "contrast": 25, "brightness": 0,
    "threshold": 145, "adaptive": True, "adaptive_block_size": 25,
    "adaptive_c": 8, "clean_bg": True, "stroke_boldness": 0, "denoise": 0,
    "sharpness": 20, "detail": 0, "dark_mode": "solid", "invert": False,
}
RANGES = {"darkness": (0, 100), "contrast": (-100, 100), "brightness": (-100, 100), "threshold": (30, 230),
          "detail": (0, 100), "sharpness": (0, 100), "denoise": (0, 3), "stroke_boldness": (-2, 2)}
MODES = ("binarize", "grayscale", "dither")
DARK_MODES = ("solid", "invert", "classic")
MODE_NAMES = {"binarize": "Đen trắng tài liệu", "grayscale": "Thang xám", "dither": "Bán sắc"}


def sanitize_params(d) -> dict:
    """Lọc dữ liệu đọc từ cài đặt đã lưu: chỉ nhận khóa hợp lệ, ép kiểu và kẹp trong giới hạn."""
    out = dict(DEFAULT_PARAMS)
    if not isinstance(d, dict):
        return out
    for k, default in DEFAULT_PARAMS.items():
        if k not in d:
            continue
        v = d[k]
        try:
            if isinstance(default, bool):
                out[k] = bool(v)
            elif isinstance(default, int):
                v = int(v)
                lo, hi = RANGES.get(k, (-10**6, 10**6))
                out[k] = max(lo, min(hi, v))
            else:
                out[k] = str(v)
        except (TypeError, ValueError):
            pass
    if out["mode"] not in MODES:
        out["mode"] = DEFAULT_PARAMS["mode"]
    if out["dark_mode"] not in DARK_MODES:
        out["dark_mode"] = DEFAULT_PARAMS["dark_mode"]
    return out


PRESETS = {
    "darken": dict(mode="binarize", darkness=80, contrast=35, brightness=0, threshold=160,
                   adaptive=True, clean_bg=True, stroke_boldness=1, denoise=0, sharpness=20,
                   detail=0, dark_mode="solid", invert=False),
    "clean_scan": dict(mode="binarize", darkness=50, contrast=25, brightness=0, threshold=130,
                       adaptive=True, clean_bg=True, stroke_boldness=0, denoise=0, sharpness=10,
                       detail=0, dark_mode="solid", invert=False),
    "standard_gray": dict(mode="grayscale", darkness=60, contrast=20, brightness=0, threshold=135,
                          adaptive=False, clean_bg=True, stroke_boldness=0, denoise=0, sharpness=25,
                          detail=30, dark_mode="solid", invert=False),
    "eco_ink": dict(mode="binarize", darkness=35, contrast=15, brightness=0, threshold=120,
                    adaptive=False, clean_bg=True, stroke_boldness=-1, denoise=0, sharpness=0,
                    detail=0, dark_mode="invert", invert=False),
    "photo_detail": dict(mode="dither", darkness=50, contrast=15, brightness=5, threshold=145,
                         adaptive=False, clean_bg=True, stroke_boldness=0, denoise=0, sharpness=30,
                         detail=60, dark_mode="solid", invert=False),
}
PRESET_LABELS = [("darken", "Làm đậm chữ mờ"), ("clean_scan", "Tẩy trắng nền cũ"),
                 ("standard_gray", "Thang xám chuẩn"), ("eco_ink", "Tiết kiệm mực"),
                 ("photo_detail", "Giữ chi tiết ảnh")]


# ----------------------------------------------------------------------
# Giao diện sáng / tối
# ----------------------------------------------------------------------
THEMES = {
    "dark": dict(bg="#14161b", panel="#1b1e25", card="#242832", border="#323744", text="#e8eaf0",
                 muted="#8b92a5", accent="#4f8cff", accent_text="#ffffff", view="#0e1014",
                 hover="#2d3240", warn="#ff7b72"),
    "light": dict(bg="#f1f3f7", panel="#ffffff", card="#eef1f6", border="#d5dae4", text="#1b1f2a",
                  muted="#6a7285", accent="#2f6fe4", accent_text="#ffffff", view="#d9dde6",
                  hover="#e3e8f1", warn="#c62828"),
}


def build_qss(c: dict) -> str:
    return f"""
    QWidget {{ color: {c['text']}; font-size: 13px; }}
    QMainWindow, #root, QDialog {{ background: {c['bg']}; }}
    #header {{ background: {c['panel']}; border-bottom: 1px solid {c['border']}; }}
    #sidebar {{ background: {c['panel']}; border-right: 1px solid {c['border']}; }}
    #exportPanel {{ background: {c['panel']}; border-top: 1px solid {c['border']}; }}
    QScrollArea {{ border: none; background: transparent; }}
    #sideContent {{ background: {c['panel']}; }}
    #title {{ font-size: 17px; font-weight: 700; }}
    #subtitle {{ color: {c['muted']}; font-size: 11px; }}
    #warn {{ color: {c['warn']}; font-size: 12px; }}
    #sectionTitle {{ font-weight: 700; font-size: 12px; color: {c['muted']}; }}
    #valBadge {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 8px;
                padding: 1px 8px; font-weight: 600; }}
    QPushButton {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 8px;
                  padding: 7px 10px; }}
    QPushButton:hover {{ background: {c['hover']}; }}
    QPushButton:disabled {{ color: {c['muted']}; }}
    QPushButton[preset="true"] {{ text-align: left; padding: 9px 10px; }}
    QPushButton[preset="true"]:checked, QPushButton[tab="true"]:checked, QPushButton[toggle="true"]:checked {{
        border: 1px solid {c['accent']}; background: {c['hover']}; }}
    QPushButton[tab="true"] {{ text-align: left; padding: 8px 12px; }}
    QPushButton[seg="true"] {{ border-radius: 0; padding: 6px 4px; margin-left: -1px; }}
    QPushButton[seg="true"]:checked {{ background: {c['accent']}; color: {c['accent_text']};
                                      border: 1px solid {c['accent']}; }}
    QPushButton[segpos="first"] {{ border-top-left-radius: 8px; border-bottom-left-radius: 8px; margin-left: 0; }}
    QPushButton[segpos="last"] {{ border-top-right-radius: 8px; border-bottom-right-radius: 8px; }}
    QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border: none;
                          font-weight: 700; font-size: 14px; padding: 11px; }}
    QPushButton#primary:disabled {{ background: {c['card']}; color: {c['muted']}; }}
    QPushButton#link {{ background: transparent; border: none; color: {c['accent']}; padding: 2px 4px; }}
    QSlider::groove:horizontal {{ height: 6px; background: {c['card']}; border: 1px solid {c['border']};
                                 border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 3px; }}
    QSlider::handle:horizontal {{ background: #ffffff; border: 2px solid {c['accent']};
                                 width: 14px; height: 14px; margin: -7px 0; border-radius: 9px; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {c['border']};
                           background: {c['card']}; }}
    QCheckBox::indicator:checked {{ background: {c['accent']}; border: 1px solid {c['accent']}; }}
    QLineEdit, QComboBox, QPlainTextEdit {{ background: {c['card']}; border: 1px solid {c['border']};
                                     border-radius: 8px; padding: 5px 8px; }}
    QComboBox QAbstractItemView {{ background: {c['panel']}; selection-background-color: {c['accent']}; }}
    QProgressBar {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 6px;
                   height: 10px; text-align: center; color: transparent; }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}
    #viewerBar {{ background: {c['panel']}; border-bottom: 1px solid {c['border']}; }}
    #dropZone {{ border: 2px dashed {c['border']}; border-radius: 16px; background: {c['panel']}; }}
    #footer {{ background: {c['panel']}; border-top: 1px solid {c['border']}; }}
    QListWidget {{ background: {c['panel']}; border: none; border-right: 1px solid {c['border']}; outline: 0; }}
    QListWidget#fileList {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 8px; }}
    QListWidget::item {{ border-radius: 8px; }}
    QListWidget::item:selected {{ background: {c['accent']}; color: {c['accent_text']}; }}
    QSplitter::handle {{ background: {c['border']}; }}
    QSplitter::handle:hover {{ background: {c['accent']}; }}
    QScrollBar:vertical {{ width: 10px; background: transparent; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """


def pil_to_qimage(im: Image.Image) -> QImage:
    if im.mode != "RGB":
        im = im.convert("L")
        qi = QImage(im.tobytes(), im.width, im.height, im.width, QImage.Format_Grayscale8)
    else:
        qi = QImage(im.tobytes(), im.width, im.height, im.width * 3, QImage.Format_RGB888)
    return qi.copy()


_RANGE_RE = re.compile(r"^\d+(-\d+)?$")


def check_range(text: str, total: int):
    """Trả về (danh sách trang, hợp lệ?). Nghiêm ngặt hơn engine.parse_page_range: không âm thầm xuất cả file khi gõ sai."""
    t = text.replace(" ", "")
    if t.lower() in ("", "all", "tat-ca"):
        return list(range(1, total + 1)), True
    parts = [p for p in t.split(",") if p]
    if not parts or not all(_RANGE_RE.match(p) for p in parts):
        return [], False
    pages = set()
    for p in parts:
        a, _, b = p.partition("-")
        a = int(a)
        b = int(b) if b else a
        pages.update(range(max(1, a), min(total, b) + 1))
    return sorted(pages), bool(pages)


def unique_path(folder: str, base: str, used: set) -> str:
    n = 1
    while True:
        name = f"{base}_bw.pdf" if n == 1 else f"{base}_bw_{n}.pdf"
        p = os.path.join(folder, name)
        if p not in used and not os.path.exists(p):
            used.add(p)
            return p
        n += 1


# ----------------------------------------------------------------------
# Phiên làm việc với 1 file PDF: mở MỘT lần, dùng lại cho mọi lần xem trước
# ----------------------------------------------------------------------
class SessionClosed(Exception):
    pass


class PdfSession:
    def __init__(self, path):
        self.path = path
        self.doc = fitz.open(path)
        self.lock = threading.Lock()
        self.closed = False
        if self.doc.needs_pass:
            self.doc.close()
            raise ValueError("File PDF này được bảo vệ bằng mật khẩu. Hãy gỡ mật khẩu trước khi mở.")
        self.page_count = len(self.doc)
        if self.page_count == 0:
            self.doc.close()
            raise ValueError("File PDF không có trang nào.")
        self.sizes = [(p.rect.width, p.rect.height) for p in self.doc]

    def render(self, page_num, dpi):
        with self.lock:
            if self.closed:
                raise SessionClosed()
            img = engine.render_page_to_pil(self.doc, page_num, dpi)
        w, h = self.sizes[page_num - 1]
        return img, w, h

    def close(self):
        with self.lock:
            if not self.closed:
                self.closed = True
                self.doc.close()


# ----------------------------------------------------------------------
# Luồng nền
# ----------------------------------------------------------------------
class PreviewSignals(QObject):
    done = Signal(int, int, int, int, object, object)   # sid, req_id, page, dpi, (img, w_pt, h_pt), proc
    failed = Signal(int, int, str)                      # sid, req_id, message
    latest = 0                                          # req_id mới nhất, để bỏ qua việc cũ


class PreviewTask(QRunnable):
    def __init__(self, signals, sid, req_id, session, page, dpi, params, cached):
        super().__init__()
        self.sg, self.sid, self.req_id, self.session = signals, sid, req_id, session
        self.page, self.dpi, self.params, self.cached = page, dpi, params, cached

    def run(self):
        sg = self.sg
        try:
            if self.req_id < sg.latest:
                return                                   # đã có yêu cầu mới hơn, bỏ qua
            entry = self.cached or self.session.render(self.page, self.dpi)
            if self.req_id < sg.latest:                  # xong phần render nhưng đã lỗi thời: chỉ để lưu cache
                sg.done.emit(self.sid, self.req_id, self.page, self.dpi, entry, None)
                return
            img, wpt, _ = entry
            proc = engine.process_image(img, self.params, engine.compute_scale(img.width, wpt))
            sg.done.emit(self.sid, self.req_id, self.page, self.dpi, entry, proc)
        except SessionClosed:
            pass
        except Exception as e:  # noqa
            logging.exception("Lỗi tạo bản xem trước")
            sg.failed.emit(self.sid, self.req_id, friendly_error(e))


class ThumbThread(QThread):
    thumb = Signal(int, QImage)

    def __init__(self, path, box=(112, 142)):
        super().__init__()
        self.path, self.box, self._stop = path, box, False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            with fitz.open(self.path) as doc:
                for i in range(len(doc)):
                    if self._stop:
                        return
                    page = doc[i]
                    r = page.rect
                    z = min(self.box[0] / r.width, self.box[1] / r.height) * 1.5
                    pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
                    qi = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                    self.thumb.emit(i + 1, qi)
        except Exception:
            logging.exception("Lỗi tạo ảnh thu nhỏ")


class ConvertThread(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(str, float, int)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, src, dst, params, pages, dpi):
        super().__init__()
        self.src, self.dst, self.params, self.pages, self.dpi = src, dst, params, pages, dpi
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            logging.info("Bắt đầu chuyển đổi: %s -> %s (%d trang, %d DPI, %s)", self.src, self.dst,
                         len(self.pages), self.dpi, self.params.get("mode"))
            engine.convert_pdf(self.src, self.dst, self.params, page_numbers=self.pages, dpi=self.dpi,
                               progress_callback=lambda c, t: self.progress.emit(c, t),
                               cancel_check=self._cancel.is_set)
            size_mb = os.path.getsize(self.dst) / (1024 * 1024)
            logging.info("Chuyển đổi xong (%.2f MB)", size_mb)
            self.finished_ok.emit(self.dst, size_mb, len(self.pages))
        except engine.ConversionCancelled:
            logging.info("Người dùng đã hủy chuyển đổi")
            self.cancelled.emit()
        except Exception as e:  # noqa
            logging.exception("Chuyển đổi thất bại")
            self.failed.emit(friendly_error(e))


class BatchThread(QThread):
    file_started = Signal(int, str)
    file_progress = Signal(int, int, int)       # idx, cur, total
    file_done = Signal(int, bool, str)          # idx, ok, message
    finished_all = Signal(int, int, bool)       # ok, fail, cancelled

    def __init__(self, jobs, params, dpi):
        super().__init__()
        self.jobs, self.params, self.dpi = jobs, params, dpi
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        ok = fail = 0
        cancelled = False
        for i, (src, dst) in enumerate(self.jobs):
            if self._cancel.is_set():
                cancelled = True
                break
            self.file_started.emit(i, os.path.basename(src))
            try:
                engine.convert_pdf(src, dst, self.params, None, self.dpi,
                                   progress_callback=lambda c, t, i=i: self.file_progress.emit(i, c, t),
                                   cancel_check=self._cancel.is_set)
                ok += 1
                self.file_done.emit(i, True, dst)
            except engine.ConversionCancelled:
                cancelled = True
                break
            except Exception as e:  # noqa
                logging.exception("Lỗi xử lý hàng loạt: %s", src)
                fail += 1
                self.file_done.emit(i, False, friendly_error(e))
        self.finished_all.emit(ok, fail, cancelled)


# ----------------------------------------------------------------------
# Widget nhỏ dùng lại
# ----------------------------------------------------------------------
class LabeledSlider(QWidget):
    changed = Signal(int)
    drag_state = Signal(bool)

    def __init__(self, title, lo, hi, val, fmt=lambda v: str(v), hint_l="", hint_r=""):
        super().__init__()
        self.fmt = fmt
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        top = QHBoxLayout()
        top.addWidget(QLabel(title))
        top.addStretch()
        self.badge = QLabel(fmt(val))
        self.badge.setObjectName("valBadge")
        top.addWidget(self.badge)
        lay.addLayout(top)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(val)
        self.slider.valueChanged.connect(self._on_change)
        self.slider.sliderPressed.connect(lambda: self.drag_state.emit(True))
        self.slider.sliderReleased.connect(lambda: self.drag_state.emit(False))
        lay.addWidget(self.slider)
        if hint_l or hint_r:
            h = QHBoxLayout()
            for text, align_right in ((hint_l, False), (hint_r, True)):
                lb = QLabel(text)
                lb.setObjectName("subtitle")
                if align_right:
                    h.addStretch()
                h.addWidget(lb)
            lay.addLayout(h)

    def _on_change(self, v):
        self.badge.setText(self.fmt(v))
        self.changed.emit(v)

    def set_value(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self.badge.setText(self.fmt(self.slider.value()))


class Segmented(QWidget):
    changed = Signal(object)

    def __init__(self, options):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for i, (label, value, tip) in enumerate(options):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setProperty("seg", True)
            b.setProperty("segpos", "first" if i == 0 else "last" if i == len(options) - 1 else "mid")
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.group.addButton(b)
            lay.addWidget(b)
            self.buttons[value] = b
            b.clicked.connect(lambda _=False, v=value: self.changed.emit(v))

    def set_value(self, v):
        b = self.buttons.get(v)
        if b:
            b.setChecked(True)


class PageBox(QLineEdit):
    """Ô nhập số trang (thay QSpinBox: ổn định hơn khi áp style)."""

    def __init__(self):
        super().__init__("1")
        self._max = 1
        self.setAlignment(Qt.AlignCenter)
        self.setFixedWidth(60)

    def setMinimum(self, _):
        pass

    def setMaximum(self, n):
        self._max = max(1, int(n))

    def value(self):
        try:
            return max(1, min(self._max, int(self.text())))
        except ValueError:
            return 1

    def setValue(self, n):
        self.setText(str(max(1, min(self._max, int(n)))))


def section_title(text):
    l = QLabel(text)
    l.setObjectName("sectionTitle")
    return l


# ----------------------------------------------------------------------
# Vùng xem trước: so sánh chia đôi, zoom, pan
# ----------------------------------------------------------------------
class CompareView(QWidget):
    zoom_changed = Signal(float)
    MARGIN = 16

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumSize(300, 300)
        self.orig = self.proc = None
        self.logical = (1.0, 1.0)      # kích thước trang ở 140 DPI chuẩn; ảnh có thể có độ phân giải cao hơn
        self.mode = "split"
        self.zoom, self.ox, self.oy, self.split = 1.0, 0.0, float(self.MARGIN), 0.5
        self.busy = False
        self.fit_mode = True           # True: luôn vừa chiều rộng khung (tự co giãn khi đổi kích thước khung)
        self._drag = None
        self._last = QPointF()
        self.bg = QColor("#0e1014")

    def set_images(self, orig, proc, logical, refit=False):
        self.orig, self.proc, self.logical = orig, proc, logical
        if refit:
            self.fit()
        self.update()

    def clear(self):
        self.orig = self.proc = None
        self.update()

    def set_mode(self, mode):
        self.mode = mode
        self.update()

    def has_image(self):
        return self.orig is not None or self.proc is not None

    def fit(self):
        if not self.has_image():
            return
        iw, _ = self.logical
        self.zoom = max(0.05, (self.width() - 2 * self.MARGIN) / iw)
        self.ox = (self.width() - iw * self.zoom) / 2
        self.oy = float(self.MARGIN)
        self.fit_mode = True
        self.zoom_changed.emit(self.zoom)
        self.update()

    def one_to_one(self):
        if not self.has_image():
            return
        self.fit_mode = False
        self.zoom = 1.0
        self.ox = (self.width() - self.logical[0]) / 2
        self.oy = float(self.MARGIN)
        self.zoom_changed.emit(self.zoom)
        self.update()

    def zoom_by(self, factor, center=None):
        """Phóng to / thu nhỏ. Mặc định LUÔN quanh tâm khung xem (không phụ thuộc vị trí con trỏ chuột)."""
        if not self.has_image():
            return
        self.fit_mode = False
        c = center or QPointF(self.width() / 2, self.height() / 2)
        new = min(8.0, max(0.05, self.zoom * factor))
        k = new / self.zoom
        self.ox = c.x() - (c.x() - self.ox) * k
        self.oy = c.y() - (c.y() - self.oy) * k
        self.zoom = new
        self.zoom_changed.emit(self.zoom)
        self.update()

    def _target(self):
        lw, lh = self.logical
        return QRectF(self.ox, self.oy, lw * self.zoom, lh * self.zoom)

    def _split_x(self):
        t = self._target()
        return t.left() + self.split * t.width()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), self.bg)
        if not self.has_image():
            return
        t = self._target()
        orig = self.orig or self.proc
        proc = self.proc or self.orig
        # ảnh có độ phân giải cao hơn vùng hiển thị -> làm mượt khi thu nhỏ
        p.setRenderHint(QPainter.SmoothPixmapTransform, (t.width() / max(1, orig.width())) < 2.0)
        p.fillRect(t, Qt.white)
        if self.mode == "before":
            p.drawImage(t, orig)
        elif self.mode == "after":
            p.drawImage(t, proc)
        else:
            p.drawImage(t, orig)
            sx = self._split_x()
            p.save()
            p.setClipRect(QRectF(sx, 0, max(0.0, self.width() - sx), self.height()))
            p.drawImage(t, proc)
            p.restore()
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(QPen(QColor("#4f8cff"), 2))
            p.drawLine(QPointF(sx, 0), QPointF(sx, self.height()))
            cy = self.height() / 2
            p.setBrush(QBrush(QColor("#4f8cff")))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(sx, cy), 16, 16)
            p.setPen(QPen(Qt.white, 2))
            for dx in (-1, 1):
                p.drawLine(QPointF(sx + dx * 7, cy), QPointF(sx + dx * 3, cy - 4))
                p.drawLine(QPointF(sx + dx * 7, cy), QPointF(sx + dx * 3, cy + 4))
            self._pill(p, "GỐC", sx - 8, 10, align_right=True)
            self._pill(p, "ĐEN TRẮNG", sx + 8, 10, align_right=False)
        if self.busy:
            p.setRenderHint(QPainter.Antialiasing, True)
            self._pill(p, "Đang xử lý…", self.width() / 2 + 50, self.height() - 40,
                       align_right=True, color=QColor(79, 140, 255, 230))

    def _pill(self, p, text, x, y, align_right, color=QColor(0, 0, 0, 170)):
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 16, fm.height() + 6
        r = QRectF(x - w if align_right else x, y, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(r, 9, 9)
        p.setPen(Qt.white)
        p.drawText(r, Qt.AlignCenter, text)

    def _near_divider(self, x):
        return self.mode == "split" and self.has_image() and abs(x - self._split_x()) < 14

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self._last = e.position()
        self._drag = "split" if self._near_divider(e.position().x()) else "pan"
        self.setCursor(Qt.SizeHorCursor if self._drag == "split" else Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag == "split":
            t = self._target()
            self.split = min(1.0, max(0.0, (pos.x() - t.left()) / max(1.0, t.width())))
            self.update()
        elif self._drag == "pan":
            d = pos - self._last
            self.ox += d.x()
            self.oy += d.y()
            self._last = pos
            self.fit_mode = False
            self.update()
        else:
            self.setCursor(Qt.SizeHorCursor if self._near_divider(pos.x()) else Qt.OpenHandCursor)

    def mouseReleaseEvent(self, _):
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)

    def resizeEvent(self, e):
        """Đổi kích thước khung: chế độ 'Vừa' thì tự vừa lại; ngược lại giữ nguyên điểm đang nằm ở tâm khung."""
        super().resizeEvent(e)
        if not self.has_image():
            return
        if self.fit_mode:
            self.fit()
            return
        old = e.oldSize()
        if old.width() > 0 and old.height() > 0 and self.zoom > 0:
            px = (old.width() / 2 - self.ox) / self.zoom
            py = (old.height() / 2 - self.oy) / self.zoom
            self.ox = self.width() / 2 - px * self.zoom
            self.oy = self.height() / 2 - py * self.zoom
            self.update()

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        if steps:
            self.zoom_by(1.12 ** steps)          # quanh tâm khung, không theo con trỏ


# ----------------------------------------------------------------------
# Xử lý hàng loạt
# ----------------------------------------------------------------------
class BatchDialog(QDialog):
    def __init__(self, parent, params, dpi, start_dir):
        super().__init__(parent)
        self.setWindowTitle("Xử lý hàng loạt")
        self.resize(640, 560)
        self.params, self.dpi, self.start_dir = dict(params), dpi, start_dir
        self.thread = None
        self.n_jobs = 0

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        info = QLabel(f"Áp dụng cài đặt hiện tại cho mọi file: chế độ {MODE_NAMES.get(params['mode'], '')}, "
                      f"{dpi} DPI, toàn bộ trang.")
        info.setWordWrap(True)
        info.setObjectName("subtitle")
        lay.addWidget(info)

        self.list = QListWidget()
        self.list.setObjectName("fileList")
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        self.btn_add = QPushButton("Thêm file PDF…")
        self.btn_add.clicked.connect(self.add_files)
        self.btn_rm = QPushButton("Bỏ file đã chọn")
        self.btn_rm.clicked.connect(self.remove_selected)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_rm)
        row.addStretch()
        lay.addLayout(row)

        self.chk_same = QCheckBox("Lưu kết quả cạnh file gốc (tên thêm hậu tố _bw)")
        self.chk_same.setChecked(True)
        self.chk_same.toggled.connect(self._toggle_out)
        lay.addWidget(self.chk_same)
        orow = QHBoxLayout()
        self.ed_out = QLineEdit()
        self.ed_out.setPlaceholderText("Thư mục lưu kết quả")
        self.btn_out = QPushButton("Chọn thư mục…")
        self.btn_out.clicked.connect(self.pick_out)
        orow.addWidget(self.ed_out, 1)
        orow.addWidget(self.btn_out)
        lay.addLayout(orow)
        self._toggle_out(True)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        lay.addWidget(self.progress)
        self.lbl = QLabel("")
        lay.addWidget(self.lbl)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        lay.addWidget(self.log)

        brow = QHBoxLayout()
        brow.addStretch()
        self.btn_start = QPushButton("Bắt đầu")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self.start)
        self.btn_close = QPushButton("Đóng")
        self.btn_close.clicked.connect(self.reject)
        brow.addWidget(self.btn_start)
        brow.addWidget(self.btn_close)
        lay.addLayout(brow)

    def _toggle_out(self, same):
        self.ed_out.setEnabled(not same)
        self.btn_out.setEnabled(not same)

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Chọn các file PDF", self.start_dir, "PDF (*.pdf)")
        existing = {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())}
        for f in files:
            if f not in existing:
                it = QListWidgetItem(os.path.basename(f))
                it.setToolTip(f)
                it.setData(Qt.UserRole, f)
                self.list.addItem(it)

    def remove_selected(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu kết quả", self.start_dir)
        if d:
            self.ed_out.setText(d)

    def start(self):
        n = self.list.count()
        if n == 0:
            QMessageBox.information(self, APP_NAME, "Hãy thêm ít nhất một file PDF.")
            return
        same = self.chk_same.isChecked()
        out_dir = self.ed_out.text().strip()
        if not same and not os.path.isdir(out_dir):
            QMessageBox.warning(self, APP_NAME, "Hãy chọn thư mục lưu kết quả hợp lệ.")
            return
        used, jobs = set(), []
        for i in range(n):
            src = self.list.item(i).data(Qt.UserRole)
            folder = os.path.dirname(src) if same else out_dir
            base = os.path.splitext(os.path.basename(src))[0]
            jobs.append((src, unique_path(folder, base, used)))
        self.n_jobs = n
        for w in (self.btn_add, self.btn_rm, self.chk_same, self.ed_out, self.btn_out, self.btn_start, self.list):
            w.setEnabled(False)
        self.btn_close.setText("Hủy")
        self.log.clear()
        self.thread = BatchThread(jobs, self.params, self.dpi)
        self.thread.file_started.connect(lambda i, name: self.lbl.setText(f"Tệp {i + 1}/{self.n_jobs}: {name}"))
        self.thread.file_progress.connect(self.on_progress)
        self.thread.file_done.connect(self.on_file_done)
        self.thread.finished_all.connect(self.on_finished)
        self.thread.start()

    def on_progress(self, idx, cur, total):
        self.progress.setValue(int((idx + cur / max(1, total)) / self.n_jobs * 1000))

    def on_file_done(self, idx, ok, msg):
        name = os.path.basename(self.list.item(idx).data(Qt.UserRole))
        self.log.appendPlainText(f"[OK]  {name}  ->  {msg}" if ok else f"[LỖI] {name}: {msg}")

    def on_finished(self, ok, fail, cancelled):
        self.thread = None
        self.progress.setValue(1000 if not cancelled else self.progress.value())
        self.lbl.setText(("Đã hủy. " if cancelled else "Hoàn tất. ") + f"Thành công: {ok}, lỗi: {fail}.")
        self.btn_close.setText("Đóng")
        self.btn_close.setEnabled(True)

    def reject(self):
        if self.thread and self.thread.isRunning():
            self.thread.cancel()
            self.btn_close.setEnabled(False)
            self.lbl.setText("Đang hủy…")
            return
        super().reject()


# ----------------------------------------------------------------------
# Cửa sổ chính
# ----------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, initial_file=None):
        super().__init__()
        self.settings = QSettings("PDFBWStudio", "PDFBWStudio")
        self.theme = self.settings.value("theme", "dark")
        if self.theme not in THEMES:
            self.theme = "dark"
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(resource_path("icon.png")))
        self.resize(1400, 860)
        self.setMinimumSize(1180, 680)
        self.setAcceptDrops(True)

        try:
            saved = json.loads(self.settings.value("params", "{}"))
        except Exception:
            saved = {}
        self.params = sanitize_params(saved)

        self.session = None
        self.pdf_path = None
        self.sid = 0
        self.page, self.total_pages = 1, 1
        self.orig_cache = OrderedDict()
        self.req_id = 0
        self.fast = False
        self.shown_dpi = 0
        self._refit = False
        self.convert_thread = None
        self.thumb_thread = None
        self.export_pages = []

        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(2)
        self.preview_signals = PreviewSignals()
        self.preview_signals.done.connect(self.on_preview_done)
        self.preview_signals.failed.connect(self.on_preview_failed)
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self.fetch_preview)
        self.zoom_timer = QTimer(self, singleShot=True, interval=400)
        self.zoom_timer.timeout.connect(self.on_zoom_settled)
        self.save_timer = QTimer(self, singleShot=True, interval=800)
        self.save_timer.timeout.connect(self.persist_params)

        self._build_ui()
        self.apply_theme()
        self.sync_ui_from_params()
        self.refresh_user_combo()

        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        st = self.settings.value("splitter")
        if st:
            self.splitter.restoreState(st)
        self.sidebar_wrap.setVisible(self.btn_panel.isChecked())

        QShortcut(QKeySequence.Open, self, activated=self.browse)
        QShortcut(QKeySequence(Qt.Key_PageUp), self, activated=lambda: self.change_page(self.page - 1))
        QShortcut(QKeySequence(Qt.Key_PageDown), self, activated=lambda: self.change_page(self.page + 1))

        if initial_file and os.path.isfile(initial_file):
            QTimer.singleShot(50, lambda: self.load_pdf(initial_file))
        QTimer.singleShot(3000, update_ui.mark_boot_ok)     # đã chạy ổn -> xác nhận bản cập nhật tốt
        self.updates.start_auto_check()

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setObjectName("header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 10, 18, 10)
        logo = QLabel()
        logo.setPixmap(QPixmap(resource_path("icon.png")).scaled(36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        hl.addWidget(logo)
        tbox = QVBoxLayout()
        tbox.setSpacing(0)
        t = QLabel(APP_NAME)
        t.setObjectName("title")
        s = QLabel("Chuyển PDF sang đen trắng, điều chỉnh độ đậm nhạt nét chữ  •  100% offline")
        s.setObjectName("subtitle")
        tbox.addWidget(t)
        tbox.addWidget(s)
        hl.addLayout(tbox)
        hl.addStretch()
        self.file_badge = QLabel("")
        self.file_badge.setObjectName("valBadge")
        self.file_badge.hide()
        hl.addWidget(self.file_badge)
        b_open = QPushButton("Mở file…")
        b_open.clicked.connect(self.browse)
        hl.addWidget(b_open)
        b_batch = QPushButton("Xử lý hàng loạt…")
        b_batch.clicked.connect(self.open_batch)
        hl.addWidget(b_batch)
        self.btn_theme = QPushButton("")
        self.btn_theme.setToolTip("Chuyển giao diện Sáng / Tối")
        self.btn_theme.clicked.connect(self.toggle_theme)
        hl.addWidget(self.btn_theme)
        self.updates = update_ui.UpdateController(self)
        hl.addWidget(self.updates.button)
        outer.addWidget(header)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(6)
        self.splitter.setChildrenCollapsible(False)
        self.sidebar_wrap = self._build_sidebar()
        viewer = self._build_viewer()
        viewer.setMinimumWidth(820)          # đủ chỗ cho thanh công cụ của khung Preview
        self.splitter.addWidget(self.sidebar_wrap)
        self.splitter.addWidget(viewer)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([420, 980])
        outer.addWidget(self.splitter, 1)

    def _build_sidebar(self):
        wrap = QWidget()
        wrap.setObjectName("sidebar")
        wrap.setMinimumWidth(340)
        wrap.setMaximumWidth(640)
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("sideContent")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(16, 14, 22, 14)
        lay.setSpacing(12)

        # Presets
        lay.addWidget(section_title("CÀI ĐẶT NHANH (PRESETS)"))
        grid = QGridLayout()
        grid.setSpacing(6)
        self.preset_btns = {}
        for i, (key, label) in enumerate(PRESET_LABELS):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setProperty("preset", True)
            b.clicked.connect(lambda _=False, k=key: self.apply_preset(k))
            self.preset_btns[key] = b
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)

        urow = QHBoxLayout()
        self.cb_user = QComboBox()
        self.cb_user.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cb_user.currentIndexChanged.connect(self.on_user_preset_selected)
        b_save = QPushButton("Lưu…")
        b_save.setToolTip("Lưu cài đặt hiện tại thành preset riêng")
        b_save.clicked.connect(lambda: self.save_user_preset())
        b_del = QPushButton("Xóa")
        b_del.clicked.connect(self.delete_user_preset)
        urow.addWidget(self.cb_user, 1)
        urow.addWidget(b_save)
        urow.addWidget(b_del)
        lay.addLayout(urow)

        # Chế độ
        lay.addWidget(section_title("CHẾ ĐỘ CHUYỂN ĐỔI"))
        self.mode_btns = {}
        for mode, text in [
            ("binarize", "Đen trắng tài liệu (Scan)\nNét chữ sắc gọn, nền trắng tinh"),
            ("grayscale", "Thang độ xám (Grayscale)\nGiữ sắc độ ảnh và đồ họa"),
            ("dither", "Bán sắc giữ chi tiết (Dithering)\nTối ưu cho ảnh chụp và đồ họa màu"),
        ]:
            b = QPushButton(text.replace("&", "&&"))
            b.setCheckable(True)
            b.setProperty("tab", True)
            b.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            self.mode_btns[mode] = b
            lay.addWidget(b)

        head = QHBoxLayout()
        head.addWidget(section_title("ĐIỀU CHỈNH CHI TIẾT"))
        head.addStretch()
        reset = QPushButton("Đặt lại")
        reset.setObjectName("link")
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(lambda: self.apply_preset("clean_scan"))
        head.addWidget(reset)
        lay.addLayout(head)

        signed = lambda v: f"+{v}" if v > 0 else str(v)

        def add_slider(attr, key, title, lo, hi, fmt, hl_="", hr_="", parent=lay):
            sl = LabeledSlider(title, lo, hi, self.params[key], fmt, hl_, hr_)
            sl.changed.connect(lambda v, k=key: self.set_param(k, v))
            sl.drag_state.connect(self.set_fast)
            setattr(self, attr, sl)
            parent.addWidget(sl)

        pct = lambda v: f"{v}%"
        add_slider("s_darkness", "darkness", "Độ đậm nét chữ (Darkness)", 0, 100, pct, "Nhạt / Thanh mảnh", "Đậm / Dày nét")

        lay.addWidget(QLabel("Khử nhiễu hạt quanh chữ (Denoise)"))
        self.seg_denoise = Segmented([("Tắt (Nhanh)", 0, "Nhanh nhất, không lọc"), ("Nhẹ", 1, ""),
                                      ("Vừa", 2, ""), ("Mạnh", 3, "")])
        self.seg_denoise.changed.connect(lambda v: self.set_param("denoise", v))
        lay.addWidget(self.seg_denoise)

        add_slider("s_detail", "detail", "Giữ chi tiết ảnh (Image Detail)", 0, 100, pct, "Tắt (chỉ văn bản)", "Chi tiết cao (ảnh chụp)")
        add_slider("s_contrast", "contrast", "Độ tương phản (Contrast)", -100, 100, signed)
        add_slider("s_brightness", "brightness", "Độ sáng (Brightness)", -100, 100, signed)

        self.grp_binarize = QWidget()
        bl = QVBoxLayout(self.grp_binarize)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(12)
        add_slider("s_threshold", "threshold", "Ngưỡng đen trắng (Threshold)", 30, 230, str, "Tẩy nền mờ", "Tăng độ đen", parent=bl)
        self.s_threshold.setToolTip("Khi bật \"Cân bằng sáng cục bộ\", giá trị trên khoảng 156 không tăng thêm độ đen "
                                    "(để nền giấy không bị đen). Tắt tùy chọn đó nếu muốn ngưỡng cố định.")
        self.chk_adaptive = QCheckBox("Cân bằng sáng cục bộ (khử bóng tối)")
        self.chk_adaptive.toggled.connect(lambda v: self.set_param("adaptive", v))
        bl.addWidget(self.chk_adaptive)
        bl.addWidget(QLabel("Độ dày nét chữ (Stroke Weight)"))
        self.seg_stroke = Segmented([("Mảnh", -1, ""), ("Chuẩn", 0, ""), ("Đậm +1", 1, ""), ("Đậm +2", 2, "")])
        self.seg_stroke.changed.connect(lambda v: self.set_param("stroke_boldness", v))
        bl.addWidget(self.seg_stroke)
        bl.addWidget(QLabel("Tiêu đề bảng / Khối nền sẫm chữ trắng"))
        self.seg_dark = Segmented([
            ("Nền đen đặc", "solid", "Khối sẫm thành đen đặc, chữ trắng bên trong nổi rõ"),
            ("Đảo nền trắng", "invert", "Đảo khối sẫm thành nền trắng chữ đen, tiết kiệm mực"),
            ("Cổ điển", "classic", "Thuật toán gốc, không xử lý riêng khối tối"),
        ])
        self.seg_dark.changed.connect(lambda v: self.set_param("dark_mode", v))
        bl.addWidget(self.seg_dark)
        lay.addWidget(self.grp_binarize)

        self.grp_gray = QWidget()
        gl = QVBoxLayout(self.grp_gray)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(12)
        add_slider("s_sharp", "sharpness", "Độ sắc nét viền chữ (Sharpness)", 0, 100, pct, parent=gl)
        self.chk_cleanbg = QCheckBox("Tẩy trắng nền ngả vàng / xám nhạt")
        self.chk_cleanbg.toggled.connect(lambda v: self.set_param("clean_bg", v))
        gl.addWidget(self.chk_cleanbg)
        lay.addWidget(self.grp_gray)

        self.chk_invert = QCheckBox("Đảo ngược màu (nền đen ↔ nền trắng)")
        self.chk_invert.toggled.connect(lambda v: self.set_param("invert", v))
        lay.addWidget(self.chk_invert)
        lay.addStretch()

        scroll.setWidget(content)
        wl.addWidget(scroll, 1)

        # ---- Khu vực xuất file: cố định ở đáy, luôn thấy ----
        panel = QWidget()
        panel.setObjectName("exportPanel")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(16, 10, 16, 12)
        pl.setSpacing(6)
        pl.addWidget(section_title("XUẤT FILE PDF"))
        row = QHBoxLayout()
        col1 = QVBoxLayout()
        col1.setSpacing(2)
        col1.addWidget(QLabel("Chọn trang:"))
        self.ed_range = QLineEdit("all")
        self.ed_range.setPlaceholderText("all hoặc 1-3, 5, 8-10")
        self.ed_range.textChanged.connect(self.update_export_state)
        col1.addWidget(self.ed_range)
        col2 = QVBoxLayout()
        col2.setSpacing(2)
        col2.addWidget(QLabel("Độ nét (DPI):"))
        self.cb_dpi = QComboBox()
        self.cb_dpi.addItem("150 DPI - Siêu nhẹ", 150)
        self.cb_dpi.addItem("200 DPI - Chuẩn văn phòng", 200)
        self.cb_dpi.addItem("300 DPI - In ấn", 300)
        try:
            self.cb_dpi.setCurrentIndex(max(0, min(2, int(self.settings.value("dpi_idx", 1)))))
        except (TypeError, ValueError):
            self.cb_dpi.setCurrentIndex(1)
        self.cb_dpi.currentIndexChanged.connect(lambda i: self.settings.setValue("dpi_idx", i))
        col2.addWidget(self.cb_dpi)
        row.addLayout(col1, 1)
        row.addLayout(col2, 1)
        pl.addLayout(row)

        self.lbl_export = QLabel("")
        self.lbl_export.setObjectName("subtitle")
        pl.addWidget(self.lbl_export)

        brow = QHBoxLayout()
        self.btn_convert = QPushButton("Chuyển đổi và lưu PDF")
        self.btn_convert.setObjectName("primary")
        self.btn_convert.setEnabled(False)
        self.btn_convert.clicked.connect(self.start_convert)
        self.btn_cancel = QPushButton("Hủy")
        self.btn_cancel.clicked.connect(self.cancel_convert)
        self.btn_cancel.hide()
        brow.addWidget(self.btn_convert, 1)
        brow.addWidget(self.btn_cancel)
        pl.addLayout(brow)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        pl.addWidget(self.progress)
        self.lbl_progress = QLabel("")
        self.lbl_progress.setObjectName("subtitle")
        self.lbl_progress.hide()
        pl.addWidget(self.lbl_progress)
        wl.addWidget(panel)
        return wrap

    def _build_viewer(self):
        self.stack = QStackedWidget()

        page0 = QWidget()
        p0 = QVBoxLayout(page0)
        p0.setContentsMargins(40, 40, 40, 40)
        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("dropZone")
        dz = QVBoxLayout(self.drop_zone)
        dz.setAlignment(Qt.AlignCenter)
        icon = QLabel()
        icon.setPixmap(QPixmap(resource_path("icon.png")).scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        icon.setAlignment(Qt.AlignCenter)
        h = QLabel("Kéo thả file PDF vào đây")
        h.setStyleSheet("font-size: 20px; font-weight: 700;")
        h.setAlignment(Qt.AlignCenter)
        sub = QLabel("hoặc bấm nút bên dưới để chọn file từ máy tính")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignCenter)
        btn = QPushButton("Chọn file PDF…")
        btn.setObjectName("primary")
        btn.setFixedWidth(200)
        btn.clicked.connect(self.browse)
        hint = QLabel("Hỗ trợ mọi PDF: tài liệu scan, hóa đơn, sách, giáo trình")
        hint.setObjectName("subtitle")
        hint.setAlignment(Qt.AlignCenter)
        for w in (icon, h, sub):
            dz.addWidget(w)
        dz.addSpacing(10)
        dz.addWidget(btn, alignment=Qt.AlignCenter)
        dz.addSpacing(6)
        dz.addWidget(hint)
        p0.addWidget(self.drop_zone)
        self.stack.addWidget(page0)

        page1 = QWidget()
        v = QVBoxLayout(page1)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("viewerBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 8, 14, 8)
        bl.setSpacing(8)

        self.btn_panel = QPushButton("Bảng")
        self.btn_panel.setToolTip("Ẩn / hiện bảng điều khiển để khung Preview rộng tối đa")
        self.btn_panel.setCheckable(True)
        self.btn_panel.setProperty("toggle", True)
        self.btn_panel.setChecked(str(self.settings.value("panel", "true")).lower() == "true")
        self.btn_panel.toggled.connect(self.on_panel_toggled)
        bl.addWidget(self.btn_panel)
        self.btn_thumbs = QPushButton("Thu nhỏ")
        self.btn_thumbs.setToolTip("Hiện / ẩn danh sách trang thu nhỏ")
        self.btn_thumbs.setCheckable(True)
        self.btn_thumbs.setProperty("toggle", True)
        self.btn_thumbs.setChecked(str(self.settings.value("thumbs", "true")).lower() == "true")
        self.btn_thumbs.toggled.connect(self.on_thumbs_toggled)
        bl.addWidget(self.btn_thumbs)
        b_prev = QPushButton("‹")
        b_prev.setToolTip("Trang trước (PageUp)")
        b_prev.setFixedWidth(34)
        b_prev.clicked.connect(lambda: self.change_page(self.page - 1))
        b_next = QPushButton("›")
        b_next.setToolTip("Trang sau (PageDown)")
        b_next.setFixedWidth(34)
        b_next.clicked.connect(lambda: self.change_page(self.page + 1))
        self.spin_page = PageBox()
        self.spin_page.editingFinished.connect(lambda: self.change_page(self.spin_page.value()))
        self.lbl_total = QLabel("/ 1")
        for w in (b_prev, self.spin_page, self.lbl_total, b_next):
            bl.addWidget(w)
        bl.addSpacing(8)

        self.view_seg = Segmented([("So sánh", "split", "Kéo thanh chia đôi để so sánh Trước / Sau"),
                                   ("Đen trắng", "after", "Chỉ xem bản đã chuyển đổi"), ("Gốc", "before", "Chỉ xem bản gốc")])
        self.view_seg.set_value("split")
        self.view_seg.changed.connect(lambda m: self.view.set_mode(m))
        bl.addWidget(self.view_seg)
        bl.addStretch()

        b_out = QPushButton("−")
        b_out.setFixedWidth(36)
        b_out.clicked.connect(lambda: self.view.zoom_by(1 / 1.25))
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setObjectName("valBadge")
        b_in = QPushButton("+")
        b_in.setFixedWidth(36)
        b_in.clicked.connect(lambda: self.view.zoom_by(1.25))
        b_fit = QPushButton("Vừa")
        b_fit.setToolTip("Vừa chiều rộng")
        b_fit.clicked.connect(lambda: self.view.fit())
        b_one = QPushButton("1:1")
        b_one.clicked.connect(lambda: self.view.one_to_one())
        for w in (b_out, self.lbl_zoom, b_in, b_fit, b_one):
            bl.addWidget(w)
        v.addWidget(bar)

        mid = QHBoxLayout()
        mid.setSpacing(0)
        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListView.ListMode)
        self.thumbs.setIconSize(QSize(THUMB_W, THUMB_H))
        self.thumbs.setSpacing(4)
        self.thumbs.setUniformItemSizes(True)
        self.thumbs.setFixedWidth(THUMB_W + 34)
        self.thumbs.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.thumbs.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.thumbs.currentRowChanged.connect(lambda r: self.change_page(r + 1) if r >= 0 else None)
        self.thumbs.setVisible(self.btn_thumbs.isChecked())
        mid.addWidget(self.thumbs)
        self.view = CompareView()
        self.view.setCursor(Qt.OpenHandCursor)
        self.view.zoom_changed.connect(self.on_zoom_changed)
        mid.addWidget(self.view, 1)
        v.addLayout(mid, 1)

        foot = QWidget()
        foot.setObjectName("footer")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(14, 6, 14, 6)
        tip = QLabel("Mẹo: kéo thanh xanh để so sánh. Con lăn chuột phóng to / thu nhỏ quanh tâm khung. "
                     "Kéo đường viền giữa để đổi cỡ khung Preview.")
        tip.setObjectName("subtitle")
        fl.addWidget(tip)
        v.addWidget(foot)
        self.stack.addWidget(page1)
        return self.stack

    # ---------------- Giao diện sáng/tối ----------------
    def apply_theme(self):
        c = THEMES[self.theme]
        self.setStyleSheet(build_qss(c))
        self.view.bg = QColor(c["view"])
        self.view.update()
        self.btn_theme.setText("Chế độ sáng" if self.theme == "dark" else "Chế độ tối")

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self.settings.setValue("theme", self.theme)
        self.apply_theme()

    # ---------------- Tham số ----------------
    def set_param(self, key, value):
        self.params[key] = value
        self.after_params_changed()

    def after_params_changed(self):
        self.update_preset_highlight()
        self.save_timer.start()
        self.schedule_preview()

    def set_mode(self, mode):
        self.params["mode"] = mode
        self.sync_mode_ui()
        self.after_params_changed()

    def sync_mode_ui(self):
        mode = self.params["mode"]
        for m, b in self.mode_btns.items():
            b.setChecked(m == mode)
        self.grp_binarize.setVisible(mode == "binarize")
        self.grp_gray.setVisible(mode != "binarize")

    def sync_ui_from_params(self):
        p = self.params
        self.sync_mode_ui()
        for sl, k in ((self.s_darkness, "darkness"), (self.s_contrast, "contrast"), (self.s_brightness, "brightness"),
                      (self.s_threshold, "threshold"), (self.s_detail, "detail"), (self.s_sharp, "sharpness")):
            sl.set_value(p[k])
        for w, v in ((self.chk_adaptive, p["adaptive"]), (self.chk_cleanbg, p["clean_bg"]), (self.chk_invert, p["invert"])):
            w.blockSignals(True)
            w.setChecked(v)
            w.blockSignals(False)
        self.seg_stroke.set_value(p["stroke_boldness"])
        self.seg_dark.set_value(p["dark_mode"])
        self.seg_denoise.set_value(p["denoise"])
        self.update_preset_highlight()

    def update_preset_highlight(self):
        for k, b in self.preset_btns.items():
            b.setChecked(all(self.params.get(pk) == pv for pk, pv in PRESETS[k].items()))
        # ô "Preset của tôi": chỉ hiện tên khi cài đặt hiện tại trùng khớp, nếu không về mục gợi ý
        match = 0
        for i in range(1, self.cb_user.count()):
            data = self._user_presets().get(self.cb_user.itemText(i))
            if data and sanitize_params(data) == self.params:
                match = i
                break
        if self.cb_user.currentIndex() != match:
            self.cb_user.blockSignals(True)
            self.cb_user.setCurrentIndex(match)
            self.cb_user.blockSignals(False)

    def apply_preset(self, name):
        self.params.update(PRESETS[name])
        self.sync_ui_from_params()
        self.after_params_changed()

    def persist_params(self):
        self.settings.setValue("params", json.dumps(self.params))

    # ---- preset riêng của người dùng ----
    def _user_presets(self) -> dict:
        try:
            d = json.loads(self.settings.value("user_presets", "{}"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def refresh_user_combo(self, select=None):
        self.cb_user.blockSignals(True)
        self.cb_user.clear()
        self.cb_user.addItem("Preset của tôi…")
        names = sorted(self._user_presets())
        self.cb_user.addItems(names)
        self.cb_user.setCurrentIndex(names.index(select) + 1 if select in names else 0)
        self.cb_user.blockSignals(False)

    def save_user_preset(self, name=None):
        if name is None:
            name, ok = QInputDialog.getText(self, "Lưu preset", "Đặt tên cho preset:")
            if not ok:
                return
        name = (name or "").strip()
        if not name:
            return
        presets = self._user_presets()
        presets[name] = dict(self.params)
        self.settings.setValue("user_presets", json.dumps(presets))
        self.refresh_user_combo(select=name)

    def on_user_preset_selected(self, idx):
        if idx <= 0:
            return
        data = self._user_presets().get(self.cb_user.itemText(idx))
        if data:
            self.params = sanitize_params(data)
            self.sync_ui_from_params()
            self.after_params_changed()

    def delete_user_preset(self):
        idx = self.cb_user.currentIndex()
        if idx <= 0:
            return
        name = self.cb_user.itemText(idx)
        if QMessageBox.question(self, APP_NAME, f"Xóa preset “{name}”?") != QMessageBox.Yes:
            return
        presets = self._user_presets()
        presets.pop(name, None)
        self.settings.setValue("user_presets", json.dumps(presets))
        self.refresh_user_combo()

    # ---------------- Mở file ----------------
    def browse(self):
        start = self.settings.value("last_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file PDF", start, "PDF (*.pdf)")
        if path:
            self.load_pdf(path)

    def open_batch(self):
        d = BatchDialog(self, self.params, self.cb_dpi.currentData(), self.settings.value("last_dir", os.path.expanduser("~")))
        d.exec()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(".pdf") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            f = u.toLocalFile()
            if f.lower().endswith(".pdf"):
                self.load_pdf(f)
                break

    def load_pdf(self, path):
        try:
            session = PdfSession(path)
        except Exception as e:  # noqa
            logging.exception("Không mở được PDF: %s", path)
            QMessageBox.warning(self, APP_NAME, f"Không thể đọc file PDF:\n{friendly_error(e)}")
            return
        logging.info("Đã mở: %s (%d trang)", path, session.page_count)
        self.settings.setValue("last_dir", os.path.dirname(path))
        self.sid += 1
        old, self.session = self.session, session
        if old:
            old.close()
        self._stop_thumbs()
        self.pdf_path = path
        self.total_pages = session.page_count
        self.page = 1
        self.orig_cache.clear()
        self.spin_page.setMaximum(self.total_pages)
        self.spin_page.setValue(1)
        self.lbl_total.setText(f"/ {self.total_pages}")
        self.file_badge.setText(f"{os.path.basename(path)}  •  {self.total_pages} trang")
        self.file_badge.show()
        self.setWindowTitle(f"{os.path.basename(path)} - {APP_NAME}")
        self.stack.setCurrentIndex(1)
        self.view.clear()
        self._refit = True
        self.update_export_state()

        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        for i in range(1, self.total_pages + 1):
            it = QListWidgetItem(str(i))
            it.setSizeHint(QSize(THUMB_W + 8, THUMB_H + 4))
            self.thumbs.addItem(it)
        self.thumbs.setCurrentRow(0)
        self.thumbs.blockSignals(False)
        self.thumb_thread = ThumbThread(path)
        self.thumb_thread.thumb.connect(self.on_thumb)
        self.thumb_thread.start()

        self.schedule_preview(delay=0)

    def _stop_thumbs(self):
        t = self.thumb_thread
        if t:
            try:
                t.thumb.disconnect()
            except Exception:
                pass
            t.stop()
            t.wait(5000)
            self.thumb_thread = None

    def on_thumb(self, page, qi):
        it = self.thumbs.item(page - 1)
        if not it:
            return
        canvas = QImage(THUMB_W, THUMB_H, QImage.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.transparent)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        img = qi.scaled(THUMB_W - 4, THUMB_H - 4, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x, y = (THUMB_W - img.width()) // 2, (THUMB_H - img.height()) // 2
        p.drawImage(x, y, img)
        p.setPen(QPen(QColor(120, 128, 145), 1))
        p.drawRect(x, y, img.width() - 1, img.height() - 1)
        label = str(page)
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(label) + 12, fm.height() + 2
        r = QRectF((THUMB_W - w) / 2, THUMB_H - h - 3, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 175))
        p.drawRoundedRect(r, 7, 7)
        p.setPen(Qt.white)
        p.drawText(r, Qt.AlignCenter, label)
        p.end()
        it.setText("")
        it.setIcon(QIcon(QPixmap.fromImage(canvas)))

    def on_panel_toggled(self, on):
        self.sidebar_wrap.setVisible(on)
        self.settings.setValue("panel", "true" if on else "false")

    def on_thumbs_toggled(self, on):
        self.thumbs.setVisible(on)
        self.settings.setValue("thumbs", "true" if on else "false")

    def change_page(self, n):
        if not self.session:
            return
        n = max(1, min(self.total_pages, n))
        self.spin_page.setValue(n)
        self.thumbs.blockSignals(True)
        self.thumbs.setCurrentRow(n - 1)
        self.thumbs.scrollToItem(self.thumbs.item(n - 1))
        self.thumbs.blockSignals(False)
        if n != self.page:
            self.page = n
            self._refit = True
            self.schedule_preview(delay=0)

    # ---------------- Xem trước ----------------
    def set_fast(self, on):
        self.fast = on
        if not on:
            self.schedule_preview(delay=0)      # thả chuột: vẽ lại ngay ở độ nét đầy đủ

    def current_preview_dpi(self):
        if self.fast:
            return FAST_DPI
        needed = engine.REF_DPI * self.view.zoom * self.devicePixelRatioF()
        dpi = 140 if needed <= 170 else 200 if needed <= 240 else 300
        w, h = self.session.sizes[self.page - 1]
        while dpi > 140 and w * h * (dpi / 72.0) ** 2 > 45e6:      # trang khổ lớn: không đẩy DPI quá cao
            dpi = 200 if dpi == 300 else 140
        return dpi

    def schedule_preview(self, delay=None):
        if not self.session:
            return
        self.view.busy = True
        self.view.update()
        self.timer.start(delay if delay is not None else (40 if self.fast else 160))

    def fetch_preview(self):
        if not self.session:
            return
        dpi = self.current_preview_dpi()
        self.req_id += 1
        self.preview_signals.latest = self.req_id
        task = PreviewTask(self.preview_signals, self.sid, self.req_id, self.session, self.page, dpi,
                           dict(self.params), self.orig_cache.get((self.page, dpi)))
        self.pool.start(task)

    def cache_put(self, key, entry):
        self.orig_cache[key] = entry
        self.orig_cache.move_to_end(key)
        while len(self.orig_cache) > 1 and (
                len(self.orig_cache) > 6 or sum(e[0].width * e[0].height * 3 for e in self.orig_cache.values()) > 200e6):
            self.orig_cache.popitem(last=False)

    def on_preview_done(self, sid, req_id, page, dpi, entry, proc):
        if sid != self.sid:
            return
        self.cache_put((page, dpi), entry)
        if proc is None or req_id != self.req_id or page != self.page:
            return
        img, wpt, hpt = entry
        refit, self._refit = self._refit, False
        self.shown_dpi = dpi
        self.view.busy = False
        k = engine.REF_DPI / 72.0
        self.view.set_images(pil_to_qimage(img), pil_to_qimage(proc), (wpt * k, hpt * k), refit=refit)

    def on_preview_failed(self, sid, req_id, msg):
        if sid == self.sid and req_id == self.req_id:
            self.view.busy = False
            self.view.update()
            QMessageBox.warning(self, APP_NAME, f"Lỗi tạo bản xem trước:\n{msg}")

    def on_zoom_changed(self, z):
        self.lbl_zoom.setText(f"{round(z * 100)}%")
        self.zoom_timer.start()

    def on_zoom_settled(self):
        """Sau khi dừng zoom: nếu đang xem to hơn độ phân giải ảnh thì dựng lại ở DPI cao hơn cho nét."""
        if self.session and not self.fast and self.view.has_image() and self.current_preview_dpi() != self.shown_dpi:
            self.schedule_preview(delay=0)

    # ---------------- Chuyển đổi ----------------
    def update_export_state(self):
        if not self.session:
            self.lbl_export.setText("")
            self.btn_convert.setEnabled(False)
            return
        pages, ok = check_range(self.ed_range.text(), self.total_pages)
        self.export_pages = pages
        self.lbl_export.setObjectName("subtitle" if ok else "warn")
        self.lbl_export.setStyleSheet("")      # buộc áp lại style theo objectName mới
        self.lbl_export.style().unpolish(self.lbl_export)
        self.lbl_export.style().polish(self.lbl_export)
        self.lbl_export.setText(f"Sẽ xuất {len(pages)} / {self.total_pages} trang" if ok
                                else "Dải trang không hợp lệ. Ví dụ: all hoặc 1-3, 5, 8-10")
        self.btn_convert.setEnabled(ok and self.convert_thread is None)

    def start_convert(self):
        if not self.session or self.convert_thread or not self.export_pages:
            return
        base = os.path.splitext(os.path.basename(self.pdf_path))[0]
        default = os.path.join(os.path.dirname(self.pdf_path), f"{base}_bw.pdf")
        dst, _ = QFileDialog.getSaveFileName(self, "Lưu file PDF đen trắng", default, "PDF (*.pdf)")
        if not dst:
            return
        if not dst.lower().endswith(".pdf"):
            dst += ".pdf"
        if os.path.normcase(os.path.abspath(dst)) == os.path.normcase(os.path.abspath(self.pdf_path)):
            QMessageBox.warning(self, APP_NAME, "Không thể ghi đè lên file gốc. Hãy chọn tên khác.")
            return
        self.persist_params()
        self.btn_convert.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.show()
        self.progress.setValue(0)
        self.progress.show()
        self.lbl_progress.setText(f"Đang xử lý 0/{len(self.export_pages)} trang…")
        self.lbl_progress.show()
        t = ConvertThread(self.pdf_path, dst, dict(self.params), list(self.export_pages), self.cb_dpi.currentData())
        t.progress.connect(self.on_progress)
        t.finished_ok.connect(self.on_convert_ok)
        t.failed.connect(self.on_convert_failed)
        t.cancelled.connect(self.on_convert_cancelled)
        self.convert_thread = t
        t.start()

    def cancel_convert(self):
        if self.convert_thread:
            self.convert_thread.cancel()
            self.btn_cancel.setEnabled(False)
            self.lbl_progress.setText("Đang hủy…")

    def on_progress(self, cur, total):
        self.progress.setValue(int(cur * 100 / max(1, total)))
        self.lbl_progress.setText(f"Đang xử lý {cur}/{total} trang…")

    def _finish_convert(self):
        t, self.convert_thread = self.convert_thread, None
        if t:
            t.wait(3000)
        self.btn_cancel.hide()
        self.progress.hide()
        self.lbl_progress.hide()
        self.update_export_state()

    def on_convert_ok(self, dst, size_mb, n_pages):
        self._finish_convert()
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Information)
        box.setText(f"Hoàn tất! Đã chuyển {n_pages} trang ({size_mb:.2f} MB).")
        box.setInformativeText(dst)
        b_open = box.addButton("Mở file", QMessageBox.AcceptRole)
        b_dir = box.addButton("Mở thư mục", QMessageBox.ActionRole)
        box.addButton("Đóng", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(dst))
        elif box.clickedButton() is b_dir:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(dst)])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(dst)))

    def on_convert_failed(self, msg):
        self._finish_convert()
        QMessageBox.critical(self, APP_NAME, f"Lỗi trong quá trình chuyển đổi:\n{msg}")

    def on_convert_cancelled(self):
        self._finish_convert()
        self.lbl_export.setText("Đã hủy chuyển đổi. Không có file nào được tạo.")

    def closeEvent(self, e):
        if self.convert_thread and self.convert_thread.isRunning():
            if QMessageBox.question(self, APP_NAME, "Đang chuyển đổi. Thoát sẽ hủy tiến trình. Thoát?") != QMessageBox.Yes:
                e.ignore()
                return
            self.convert_thread.cancel()
            self.convert_thread.wait(8000)
        self._stop_thumbs()
        self.persist_params()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter", self.splitter.saveState())
        self.preview_signals.latest = 10**9           # các việc xem trước đang chờ sẽ tự bỏ
        self.pool.waitForDone(3000)
        if self.session:
            self.session.close()
        update_ui.mark_boot_ok()
        logging.info("Thoát ứng dụng")
        e.accept()


def main():
    setup_logging()
    install_excepthooks()
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PDFBWStudio.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon(resource_path("icon.png")))
    ERROR_BUS.error.connect(lambda msg: QMessageBox.critical(
        None, APP_NAME, f"Đã xảy ra lỗi không mong muốn:\n{msg}\n\nChi tiết đã được ghi vào:\n{LOG_FILE}"))
    initial = next((a for a in sys.argv[1:] if a.lower().endswith(".pdf")), None)
    win = MainWindow(initial)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
