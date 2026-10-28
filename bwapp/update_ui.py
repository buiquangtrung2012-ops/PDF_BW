"""Giao diện cập nhật: nút "Cập nhật" ở header, kiểm tra tự động, tải & cài, cài từ file, khởi động lại."""
import logging
import os
import subprocess
import sys
import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QFileDialog, QMenu, QMessageBox, QProgressDialog, QPushButton

from . import updater
from . import version

try:
    import launcher_core as _core          # lõi cố định, có sẵn khi chạy qua launcher
except ImportError:                        # chạy thẳng bwapp.main không qua launcher (hiếm)
    _core = None

AUTO_CHECK_EVERY = 12 * 3600               # giây


def boot_log():
    return list(_core.BOOT_LOG) if _core else []


def mark_boot_ok():
    if _core:
        _core.mark_boot_ok()


class _Worker(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            self.done.emit(self.fn(self))
        except Exception as e:
            logging.exception("Lỗi cập nhật")
            self.failed.emit(str(e) or e.__class__.__name__)


class UpdateController(QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.available = None              # manifest của bản mới (nếu có)
        self._worker = None
        self.button = QPushButton("Cập nhật")
        self.button.setToolTip(f"Phiên bản hiện tại: {version.__version__}")
        menu = QMenu(self.button)
        menu.addAction("Kiểm tra cập nhật", lambda: self.check(manual=True))
        menu.addAction("Cài bản cập nhật từ file…", self.install_from_file)
        self.act_auto = menu.addAction("Tự động kiểm tra khi mở app")
        self.act_auto.setCheckable(True)
        self.act_auto.setChecked(str(win.settings.value("auto_update", "true")).lower() != "false")
        self.act_auto.toggled.connect(lambda v: win.settings.setValue("auto_update", "true" if v else "false"))
        menu.addSeparator()
        menu.addAction("Thông tin phiên bản", self.show_about)
        self.button.setMenu(menu)

    # ---------------- kiểm tra ----------------
    def start_auto_check(self):
        if not (self.act_auto.isChecked() and version.UPDATE_MANIFEST_URL):
            return
        try:
            last = float(self.win.settings.value("last_update_check", 0))
        except (TypeError, ValueError):
            last = 0
        if time.time() - last >= AUTO_CHECK_EVERY:
            QTimer.singleShot(5000, lambda: self.check(manual=False))

    def check(self, manual):
        if self._busy():
            return
        url = version.UPDATE_MANIFEST_URL
        if not url:
            if manual:
                QMessageBox.information(self.win, "Cập nhật",
                    "Bản này chưa cấu hình địa chỉ kiểm tra cập nhật.\n"
                    "Bạn vẫn có thể cài bản cập nhật bằng cách chọn \"Cài bản cập nhật từ file…\".")
            return
        w = self._start(lambda _w: updater.fetch_manifest(url))
        w.done.connect(lambda m: self._on_manifest(m, manual))
        w.failed.connect(lambda msg: QMessageBox.warning(self.win, "Cập nhật", msg) if manual else logging.info("Kiểm tra cập nhật: %s", msg))
        w.start()

    def _on_manifest(self, m, manual):
        self.win.settings.setValue("last_update_check", time.time())
        cur = _core.installed_version() if _core else version.__version__
        key = _core.vkey if _core else (lambda v: tuple(int(x) for x in str(v).split(".")))
        if key(m["version"]) <= key(cur):
            self.available = None
            self.button.setText("Cập nhật")
            if manual:
                QMessageBox.information(self.win, "Cập nhật", f"Bạn đang dùng bản mới nhất ({cur}).")
            return
        self.available = m
        self.button.setText(f"Có bản mới {m['version']}")
        if _core and m["requires_api"] > _core.LAUNCHER_API:
            if manual:
                QMessageBox.information(self.win, "Cập nhật",
                    f"Bản {m['version']} cần bộ cài đầy đủ mới. Hãy tải và cài lại Setup.exe một lần.")
            return
        if manual:                         # kiểm tra tự động chỉ đổi chữ trên nút, không làm phiền
            self.download_and_install(m)

    def download_and_install(self, m=None):
        m = m or self.available
        if not m or self._busy():
            return
        notes = f"\n\nCó gì mới:\n{m['notes']}" if m.get("notes") else ""
        if QMessageBox.question(self.win, "Cập nhật", f"Tải và cài bản {m['version']}?{notes}") != QMessageBox.Yes:
            return
        dlg = QProgressDialog("Đang tải bản cập nhật…", "Hủy", 0, 100, self.win)
        dlg.setWindowTitle("Cập nhật")
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)

        def job(w):
            path = updater.download(m["url"], progress=lambda d, t: w.progress.emit(d, t), cancelled=lambda: w._cancel)
            try:
                if _core is None:
                    raise RuntimeError("Không tìm thấy lõi cập nhật.")
                return _core.install_patch(path)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

        w = self._start(job)
        dlg.canceled.connect(w.cancel)
        w.progress.connect(lambda d, t: dlg.setValue(int(d * 100 / t)) if t else dlg.setRange(0, 0))
        w.done.connect(lambda r: (dlg.close(), self._installed(r)))
        w.failed.connect(lambda msg: (dlg.close(), None if w._cancel else QMessageBox.warning(self.win, "Cập nhật", msg)))
        dlg.show()
        w.start()

    # ---------------- cài từ file ----------------
    def install_from_file(self):
        if self._busy():
            return
        path, _ = QFileDialog.getOpenFileName(self.win, "Chọn file cập nhật", "", "Bản cập nhật (*.bwpatch)")
        if not path:
            return
        w = self._start(lambda _w: _core.install_patch(path))
        w.done.connect(self._installed)
        w.failed.connect(lambda msg: QMessageBox.warning(self.win, "Cập nhật", msg))
        w.start()

    # ---------------- sau khi cài ----------------
    def _installed(self, r):
        self.available = None
        self.button.setText("Khởi động lại để cập nhật")
        notes = f"\n\n{r['notes']}" if r.get("notes") else ""
        box = QMessageBox(self.win)
        box.setWindowTitle("Cập nhật")
        box.setIcon(QMessageBox.Information)
        box.setText(f"Đã cài bản {r['version']}. Khởi động lại ứng dụng để dùng bản mới.{notes}")
        now = box.addButton("Khởi động lại ngay", QMessageBox.AcceptRole)
        box.addButton("Để sau", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is now:
            self.restart()

    def restart(self):
        t = self.win.convert_thread
        if t and t.isRunning():
            QMessageBox.information(self.win, "Cập nhật", "Đang chuyển đổi file. Hãy khởi động lại sau khi xong.")
            return
        args = [sys.executable] + ([] if getattr(sys, "frozen", False) else [sys.argv[0]])
        if self.win.pdf_path:
            args.append(self.win.pdf_path)
        try:
            subprocess.Popen(args, close_fds=True)
        except Exception as e:
            QMessageBox.warning(self.win, "Cập nhật", f"Không tự mở lại được ({e}). Hãy mở lại ứng dụng thủ công.")
            return
        self.win.close()

    # ---------------- thông tin ----------------
    def show_about(self):
        info = _core.active_info() if _core else {}
        src = "bản cập nhật" if info.get("is_update") else "bản đi kèm bộ cài"
        QMessageBox.information(self.win, "Thông tin phiên bản",
            f"Phiên bản ứng dụng: {version.__version__} ({src})\n"
            f"Phiên bản lõi: {info.get('api', '?')}\n"
            f"Địa chỉ cập nhật: {version.UPDATE_MANIFEST_URL or '(chưa cấu hình)'}")

    # ---------------- nội bộ ----------------
    def _busy(self):
        return self._worker is not None and self._worker.isRunning()

    def _start(self, fn):
        w = _Worker(fn)
        self._worker = w
        return w                           # người gọi nối tín hiệu xong rồi mới w.start()
