# PDF Black & White Studio — Bản Desktop 2.2.5 (native)

Ứng dụng desktop thật (PySide6/Qt), **không Flask, không trình duyệt, không máy chủ web**, chạy 100% offline.

## Tính năng
- Xem trước trực tiếp: so sánh Trước/Sau bằng thanh chia đôi, danh sách trang thu nhỏ, lật trang
- **Zoom luôn quanh tâm khung Preview** (con lăn chuột, nút +/−), không lệch theo vị trí con trỏ; kéo chuột để di chuyển
- **Đổi kích thước khung Preview:** kéo đường viền giữa bảng điều khiển và Preview, hoặc nút "Bảng" để ẩn/hiện bảng điều khiển (nhớ cho lần sau). Chế độ "Vừa" tự co giãn theo khung; đang zoom thì giữ nguyên điểm ở tâm
- 3 chế độ (Đen trắng tài liệu, Thang xám, Bán sắc), 5 preset + **preset riêng của bạn** (nút "Lưu…")
- Denoise, Image Detail, Contrast, Brightness, Threshold, cân bằng sáng cục bộ, độ dày nét, xử lý khối nền sẫm, đảo màu
- Xuất theo dải trang (`1-5, 8, 10-12`), 150/200/300 DPI, thanh tiến trình, **nút Hủy**
- **Xử lý hàng loạt** nhiều file PDF cùng lúc
- Kéo thả file PDF vào cửa sổ; Ctrl+O; PageUp/PageDown; giao diện Sáng/Tối; nhớ cài đặt & kích thước cửa sổ
- Mở được từ "Open with" của Windows
- **Cập nhật tự động bằng file vá nhỏ, có chữ ký số, tự rollback nếu lỗi** (nút "Cập nhật" ở góc phải trên)

## Cải tiến độ ổn định / hiệu năng (so với bản 2.0)
- File xuất được ghi ra tạm rồi mới đổi tên: không bao giờ để lại PDF hỏng, hủy giữa chừng không ảnh hưởng file cũ
- Xuất từng trang rồi ghi ngay, RAM không phình theo số trang; số luồng tự giảm với trang khổ lớn
- Mở file PDF một lần cho phần xem trước; kéo thanh trượt dùng độ nét thấp cho mượt, thả chuột mới vẽ nét đầy đủ; zoom to tự dựng lại nét hơn
- Bộ lọc được quy đổi theo DPI: bản xuất 200/300 DPI có nét chữ giống bản xem trước
- **Sửa lỗi thuật toán gốc:** ở chế độ "Cân bằng sáng cục bộ", ngưỡng từ 160 trở lên làm toàn bộ nền giấy thành đen (preset "Làm đậm chữ mờ" dính lỗi này)
- Ghi log lỗi vào `%LOCALAPPDATA%\PDFBWStudio\log.txt` và báo lỗi bằng tiếng Việt dễ hiểu
- Dải trang gõ sai sẽ bị báo lỗi (trước đây âm thầm xuất cả file)

## Kiến trúc: build một lần, cập nhật bằng file vá nhỏ
```
launcher.py + launcher_core.py + ed25519.py   <- "lõi" CỐ ĐỊNH, nằm trong .exe (chọn bản mã, xác minh chữ ký, tự rollback)
Python + PySide6 + PyMuPDF + numpy + Pillow   <- thư viện nặng, cố định trong bộ cài
bwapp/  (main.py, engine.py, update_ui.py...) <- MÃ ỨNG DỤNG: nơi bạn sửa, được thay bằng bản vá (~30 KB)
```
Khi chạy, lõi so sánh phiên bản của bwapp đi kèm bộ cài với bản vá đã cài (`%LOCALAPPDATA%\PDFBWStudio\updates`) và chạy bản **mới hơn**.

## Cách build bộ cài (làm ít khi, trên Windows)
1. Cài Python 3.10+ (tick "Add python.exe to PATH").
2. (Tùy chọn, để có Setup.exe) cài Inno Setup 6: https://jrsoftware.org/isdl.php
3. (Nếu dùng cập nhật tự động) mở `bwapp\version.py`, điền `UPDATE_MANIFEST_URL` (xem bên dưới).
4. Bấm đúp **`build.bat`**. **Lần đầu** nó tự tạo cặp khóa ký trong `keys\`: **sao lưu thư mục `keys\` ở nơi an toàn, không chia sẻ.**

Kết quả: `dist\PDF_BW_Studio\PDF_BW_Studio.exe` (chạy ngay) và `installer_output\PDF_BW_Studio_Setup.exe` (bộ cài).
Chạy thử không cần build: `run_dev.bat`.

## Phát hành một bản cập nhật (KHÔNG cần build lại)
1. Sửa code trong `bwapp\`.
2. Tăng `__version__` trong `bwapp\version.py` (ví dụ 2.2.0 → 2.2.1).
3. Bấm đúp **`publish_update.bat`**, nhập ghi chú phiên bản.
4. Tải **2 file** trong thư mục `release\` (`PDF_BW_Studio_2.2.1.bwpatch` và `version.json`) lên nơi lưu trữ.

Người dùng sẽ thấy nút **"Có bản mới 2.2.1"** (app tự kiểm tra khi mở, tối đa 12 giờ/lần); bấm → tải, xác minh, cài → "Khởi động lại ngay". Nếu không dùng máy chủ, gửi thẳng file `.bwpatch` cho họ: menu **Cập nhật → Cài bản cập nhật từ file…**.

### Nơi lưu trữ file cập nhật
Bất kỳ chỗ nào phục vụ file tĩnh qua https. Ví dụ với GitHub Releases (miễn phí): mỗi lần phát hành, tạo một Release mới và đính kèm 2 file trên; đặt
`UPDATE_MANIFEST_URL = "https://github.com/<tên>/<repo>/releases/latest/download/version.json"`.
Địa chỉ này luôn trỏ tới bản mới nhất. (App tải file ẩn danh nên repo phải public; nếu cần riêng tư, dùng hosting khác.)

### Khi nào vẫn phải build lại + phát hành Setup.exe mới
- Thêm thư viện bên ngoài mới (`pip install ...`), hoặc nâng Python / PySide6 / PyMuPDF.
- Dùng module Qt ngoài danh sách đã có (QtSvg, QtNetwork, QtPrintSupport có sẵn dự phòng).
- Đổi icon, trình cài đặt, hoặc sửa lõi (`launcher*.py`, `ed25519.py`).
Thư viện chuẩn của Python thì dùng thoải mái trong bản vá. Nếu một bản vá cần lõi mới, đặt `--requires-api N` khi `pack`; app cũ sẽ báo người dùng tải bộ cài đầy đủ thay vì cài hỏng.

### An toàn
- **Chữ ký số Ed25519**: lõi từ chối mọi file `.bwpatch` không do khóa của bạn ký hoặc đã bị sửa dù 1 byte. Mất khóa bí mật = không phát hành được bản vá cho các bản cài cũ.
- Gói vá chỉ được chứa thư mục `bwapp/`, bị từ chối nếu có đường dẫn lạ; mọi file `.py` được kiểm tra cú pháp **trước** khi kích hoạt.
- **Tự rollback**: nếu bản cập nhật làm app không mở được 2 lần liên tiếp, lần sau app tự quay về bản đi kèm bộ cài và đánh dấu bản lỗi (xem `log.txt`). Bản sửa lỗi phải có số phiên bản mới.
- Bản vá không bao giờ ghi đè file đang chạy; bản mới chỉ có hiệu lực ở lần khởi động kế tiếp.
- Vì file `.exe` không đổi giữa các bản vá, Windows SmartScreen / phần mềm diệt virus không phải đánh giá lại.

## Lưu ý
- PDF xuất ra là **ảnh thuần** nên không còn tìm kiếm văn bản, link, bookmark.
- File .exe chưa ký số có thể bị Windows SmartScreen cảnh báo: chọn "More info" → "Run anyway".
- Nếu app báo lỗi, gửi file `log.txt` ở đường dẫn trên.
