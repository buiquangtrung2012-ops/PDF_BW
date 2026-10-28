"""
PDF Black & White Processing Engine (v2)

Thay đổi so với v1 (thuật toán lọc giữ nguyên):
- process_image(..., scale): các bộ lọc tính theo pixel (độ dày nét, khử nhiễu, cân bằng sáng cục bộ,
  độ sắc nét...) được quy đổi theo độ phân giải, nên bản xuất 200/300 DPI có nét chữ giống bản xem trước 140 DPI.
- convert_pdf: xử lý song song nhưng chèn trang theo đúng thứ tự ngay khi xong (không giữ toàn bộ ảnh trong RAM),
  mỗi luồng mở file PDF một lần, số luồng tự giảm theo kích thước trang, hỗ trợ hủy, ghi ra file tạm rồi mới đổi tên.
"""

import io
import os
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import pymupdf as fitz  # PyMuPDF
from PIL import Image, ImageFilter, ImageOps
import numpy as np

REF_DPI = 140                      # DPI chuẩn của bản xem trước; scale = 1.0 tại mức này
MAX_RENDER_PIXELS = 120_000_000    # chặn trang khổ quá lớn làm tràn RAM


class ConversionCancelled(Exception):
    """Người dùng đã hủy quá trình chuyển đổi."""


# ----------------------------------------------------------------------
# Tiện ích PDF
# ----------------------------------------------------------------------
def get_pdf_info(pdf_path: str):
    """Lấy thông tin tổng quan của file PDF (số trang, kích thước từng trang)."""
    with fitz.open(pdf_path) as doc:
        info = {"page_count": len(doc), "pages": []}
        for i, page in enumerate(doc):
            rect = page.rect
            info["pages"].append({
                "page_num": i + 1,
                "width": round(rect.width, 1),
                "height": round(rect.height, 1),
            })
    return info


def render_page_to_pil(doc, page_num: int, dpi: int = 150) -> Image.Image:
    """Kết xuất trang PDF sang ảnh PIL RGB với độ phân giải DPI chỉ định."""
    page_idx = page_num - 1
    if page_idx < 0 or page_idx >= len(doc):
        page_idx = 0
    page = doc[page_idx]
    zoom = dpi / 72.0
    rect = page.rect
    px = (rect.width * zoom) * (rect.height * zoom)
    if px > MAX_RENDER_PIXELS:                      # trang khổ rất lớn: hạ độ phân giải cho an toàn
        zoom *= (MAX_RENDER_PIXELS / px) ** 0.5
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    del pix
    return img


def compute_scale(img_width_px: int, page_width_pt: float) -> float:
    """Hệ số quy đổi bộ lọc so với bản xem trước chuẩn (REF_DPI), tính theo độ phân giải thực tế của ảnh."""
    ref_width = page_width_pt * REF_DPI / 72.0
    return max(0.3, img_width_px / ref_width) if ref_width > 0 else 1.0


def parse_page_range(range_str: str, total_pages: int):
    """Phân tích chuỗi dải trang (ví dụ: '1-3, 5, 8-10' hoặc 'all') thành danh sách số trang."""
    if not range_str or range_str.strip().lower() in ["all", "tat-ca", ""]:
        return list(range(1, total_pages + 1))

    pages = set()
    for part in range_str.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            try:
                start_s, end_s = part.split("-", 1)
                start = max(1, int(start_s))
                end = min(total_pages, int(end_s))
                if start <= end:
                    pages.update(range(start, end + 1))
            except ValueError:
                pass
        else:
            try:
                p = int(part)
                if 1 <= p <= total_pages:
                    pages.add(p)
            except ValueError:
                pass

    result = sorted(pages)
    return result if result else list(range(1, total_pages + 1))


# ----------------------------------------------------------------------
# Bộ lọc ảnh
# ----------------------------------------------------------------------
def _kernel(base_size: int, scale: float) -> int:
    """
    Kích thước cửa sổ MinFilter/MaxFilter quy đổi theo độ phân giải.
    Giữ nguyên "bán kính" tính theo pixel: base 3 (bán kính 1) và base 5 (bán kính 2) ở scale 1.0
    cho đúng 3 và 5 như v1; ở độ phân giải cao bán kính tăng tỉ lệ thuận.
    """
    half = max(1, round((base_size - 1) / 2.0 * scale))
    return 2 * half + 1


def apply_despeckle(binary_img: Image.Image, level: int = 2, scale: float = 1.0) -> Image.Image:
    """
    Khử nhiễu hạt trên ảnh nhị phân:
    - level 1: MedianFilter(3)
    - level 2: Morphological Opening (Max rồi Min)
    - level 3: lọc mật độ điểm ảnh + Opening
    """
    if level <= 0:
        return binary_img
    if level == 1:
        return binary_img.filter(ImageFilter.MedianFilter(size=3))

    k = _kernel(3, scale)
    if level == 2:
        return binary_img.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))

    arr = np.array(binary_img) < 128
    mask_im = Image.fromarray((arr * 255).astype(np.uint8))
    density = np.array(mask_im.filter(ImageFilter.BoxBlur(radius=max(1, round(2 * scale)))))
    is_speckle = arr & (density < 50)
    cleaned_arr = np.array(binary_img)
    cleaned_arr[is_speckle] = 255
    temp = Image.fromarray(cleaned_arr, mode="L")
    return temp.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))


def enhance_image_detail(gray_img: Image.Image, detail_level: int = 0, scale: float = 1.0) -> Image.Image:
    """Tăng cường chi tiết ảnh (Local Tone Mapping / Clarity). detail_level: 0 (tắt) đến 100."""
    if detail_level <= 0:
        return gray_img

    enhanced = gray_img.filter(ImageFilter.UnsharpMask(
        radius=25 * scale, percent=int(detail_level * 2), threshold=3
    ))
    if detail_level >= 40:
        cutoff = max(0.5, 2.0 - detail_level / 50.0)
        enhanced = ImageOps.autocontrast(enhanced, cutoff=cutoff)
    return enhanced


def process_image(img: Image.Image, params: dict, scale: float = 1.0) -> Image.Image:
    """
    Áp dụng các bộ lọc đen trắng, độ đậm/nhạt và khử nhiễu theo tham số đầu vào.
    scale: tỉ lệ độ phân giải so với bản xem trước chuẩn (xem compute_scale).
    """
    s = max(0.3, float(scale))
    mode = params.get("mode", "binarize")
    brightness = int(params.get("brightness", 0))
    contrast = int(params.get("contrast", 0))
    darkness = int(params.get("darkness", 65))
    sharpness = int(params.get("sharpness", 0))
    threshold = int(params.get("threshold", 145))
    adaptive = bool(params.get("adaptive", True))
    adaptive_block_size = int(params.get("adaptive_block_size", 25))
    adaptive_c = int(params.get("adaptive_c", 8))
    clean_bg = bool(params.get("clean_bg", True))
    stroke_boldness = int(params.get("stroke_boldness", 0))
    denoise = int(params.get("denoise", 0))
    detail = int(params.get("detail", 0))
    dark_mode = params.get("dark_mode", "solid")
    invert = bool(params.get("invert", False))

    gray = img.convert("L")

    # 1. LUT cho Brightness, Contrast và Darkness (Gamma)
    if darkness >= 50:
        effective_gamma = 1.0 - ((darkness - 50) / 50.0) * 0.65
    else:
        effective_gamma = 1.0 + ((50 - darkness) / 50.0) * 1.2

    if contrast >= 255:
        contrast = 254
    elif contrast <= -255:
        contrast = -254
    factor = (259.0 * (contrast + 255.0)) / (255.0 * (259.0 - contrast))

    idx = np.arange(256, dtype=np.float64)
    vals = 255.0 * ((idx / 255.0) ** (1.0 / effective_gamma))
    vals = factor * (vals - 128.0) + 128.0 + brightness
    lut = np.clip(vals, 0, 255).astype(np.uint8)
    gray = gray.point(lut.tolist())

    # 1.5. Tăng cường chi tiết ảnh
    if detail > 0:
        gray = enhance_image_detail(gray, detail, s)

    # 2. Xử lý theo chế độ
    if mode == "grayscale":
        if denoise > 0:
            gray = gray.filter(ImageFilter.MedianFilter(size=3))
        if clean_bg:
            gray = gray.point([i if i < 235 else int(np.clip(235 + (i - 235) * 2, 0, 255)) for i in range(256)])
        if sharpness > 0:
            gray = gray.filter(ImageFilter.UnsharpMask(radius=1.5 * s, percent=int(sharpness * 1.5), threshold=3))
        result_img = gray

    elif mode == "dither":
        if denoise >= 1:
            gray = gray.filter(ImageFilter.MedianFilter(size=3))
        if clean_bg:
            gray = gray.point([i if i < 235 else int(np.clip(235 + (i - 235) * 2, 0, 255)) for i in range(256)])
        if sharpness > 0:
            gray = gray.filter(ImageFilter.UnsharpMask(radius=1.5 * s, percent=int(sharpness * 1.5), threshold=3))
        result_img = gray.convert("1").convert("L")

    else:
        pre = gray
        if denoise >= 2:
            pre = gray.filter(ImageFilter.GaussianBlur(radius=0.5 * s))

        if adaptive:
            base_radius = max(3, (adaptive_block_size // 2) * 2 + 1)
            radius = max(3, round(base_radius * s))
            blurred = pre.filter(ImageFilter.BoxBlur(radius=radius))

            arr = np.array(pre, dtype=np.float32)
            blur_arr = np.array(blurred, dtype=np.float32)
            thresh_offset = (threshold - 128) * 0.25
            # Sửa lỗi v1: khi độ lệch >= adaptive_c (ngưỡng >= 160), vùng nền phẳng cũng thỏa điều kiện
            # 'tối hơn trung bình cục bộ' nên toàn bộ nền giấy bị chuyển thành đen. Giới hạn để nền luôn trắng.
            thresh_offset = min(thresh_offset, adaptive_c - 1)

            if dark_mode in ("solid", "invert"):
                macro_radius = max(round(20 * s), radius * 2)
                macro_arr = np.array(pre.filter(ImageFilter.BoxBlur(radius=macro_radius)), dtype=np.float32)

                is_dark_bg = (macro_arr < 115) & (blur_arr < 130)
                res_arr = np.full_like(arr, 255, dtype=np.uint8)

                is_light_bg = ~is_dark_bg
                diff_light = arr - (blur_arr - adaptive_c + thresh_offset)
                res_arr[is_light_bg & (diff_light <= 0)] = 0

                is_white_text = is_dark_bg & ((arr >= blur_arr + 12 - thresh_offset * 0.5) | (arr > 160))

                if dark_mode == "solid":
                    res_arr[is_dark_bg] = 0
                    res_arr[is_white_text] = 255
                else:  # invert
                    res_arr[is_white_text] = 0
                    dark_mask = Image.fromarray((is_dark_bg * 255).astype(np.uint8))
                    k = _kernel(3, s)
                    dilated = np.array(dark_mask.filter(ImageFilter.MaxFilter(k))) > 128
                    eroded = np.array(dark_mask.filter(ImageFilter.MinFilter(k))) > 128
                    res_arr[dilated & (~eroded)] = 0

                result_img = Image.fromarray(res_arr, mode="L")
            else:
                diff = arr - (blur_arr - adaptive_c + thresh_offset)
                result_img = Image.fromarray(np.where(diff <= 0, 0, 255).astype(np.uint8), mode="L")
        else:
            result_img = pre.point([0 if i < threshold else 255 for i in range(256)])

        # 3. Khử nhiễu hạt
        if denoise > 0:
            result_img = apply_despeckle(result_img, level=denoise, scale=s)

        # 4. Độ dày nét chữ
        if stroke_boldness > 0:
            result_img = result_img.filter(ImageFilter.MinFilter(_kernel(3 if stroke_boldness == 1 else 5, s)))
        elif stroke_boldness < 0:
            result_img = result_img.filter(ImageFilter.MaxFilter(_kernel(3 if stroke_boldness == -1 else 5, s)))

    if invert:
        result_img = ImageOps.invert(result_img)

    return result_img


# ----------------------------------------------------------------------
# Chuyển đổi cả file
# ----------------------------------------------------------------------
def _encode_page(proc_img: Image.Image, mode: str) -> bytes:
    buf = io.BytesIO()
    if mode in ("binarize", "dither"):
        proc_img.convert("1").save(buf, format="PNG", optimize=False, compress_level=4)
    else:
        proc_img.save(buf, format="JPEG", quality=88, optimize=False)
    return buf.getvalue()


def _choose_workers(page_w_pt: float, page_h_pt: float, dpi: int) -> int:
    """Số luồng song song: giảm khi trang lớn để không tràn RAM (ước lượng ~40 byte / pixel / luồng)."""
    pixels = page_w_pt * page_h_pt * (dpi / 72.0) ** 2
    by_ram = int(1.2e9 / max(1.0, pixels * 40))
    return max(1, min(os.cpu_count() or 4, 4, by_ram))


def convert_pdf(
    input_path: str,
    output_path: str,
    params: dict,
    page_numbers: list = None,
    dpi: int = 200,
    progress_callback=None,
    cancel_check=None,
):
    """
    Chuyển đổi PDF sang đen trắng.
    - Chèn trang theo đúng thứ tự ngay khi xong, RAM không phình theo số trang.
    - Ghi ra "<output>.part" rồi os.replace → không bao giờ để lại file PDF hỏng.
    - cancel_check(): trả True để hủy; ném ConversionCancelled.
    """
    with fitz.open(input_path) as src:
        total_in_doc = len(src)
        if not page_numbers:
            page_numbers = list(range(1, total_in_doc + 1))
        targets = [p for p in page_numbers if 1 <= p <= total_in_doc]
        if not targets:
            raise ValueError("Không có trang hợp lệ để xuất.")
        r0 = src[targets[0] - 1].rect

    workers = _choose_workers(r0.width, r0.height, dpi)
    mode = params.get("mode", "binarize")
    tmp_path = output_path + ".part"

    local = threading.local()
    handles, hlock = [], threading.Lock()

    def get_doc():
        d = getattr(local, "doc", None)
        if d is None:
            d = fitz.open(input_path)
            local.doc = d
            with hlock:
                handles.append(d)
        return d

    def work(p_num):
        doc = get_doc()
        rect = doc[p_num - 1].rect
        img = render_page_to_pil(doc, p_num, dpi=dpi)
        scale = compute_scale(img.width, rect.width)
        proc = process_image(img, params, scale)
        del img
        return rect.width, rect.height, _encode_page(proc, mode)

    out_doc = fitz.open()
    pool = ThreadPoolExecutor(max_workers=workers)
    window = deque()
    it = iter(targets)
    done = 0
    total = len(targets)

    def submit_next():
        p = next(it, None)
        if p is not None:
            window.append(pool.submit(work, p))

    try:
        for _ in range(workers * 2):
            submit_next()
        while window:
            fut = window.popleft()
            while True:
                if cancel_check and cancel_check():
                    raise ConversionCancelled()
                try:
                    w, h, data = fut.result(timeout=0.2)
                    break
                except FuturesTimeout:
                    continue
            page = out_doc.new_page(width=w, height=h)
            page.insert_image(fitz.Rect(0, 0, w, h), stream=data)
            done += 1
            if progress_callback:
                progress_callback(done, total)
            submit_next()

        if cancel_check and cancel_check():
            raise ConversionCancelled()
        out_doc.save(tmp_path, garbage=3, deflate=True)
        out_doc.close()
        out_doc = None
        os.replace(tmp_path, output_path)
    except BaseException:
        for f in window:
            f.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        for d in handles:
            try:
                d.close()
            except Exception:
                pass
        if out_doc is not None:
            out_doc.close()
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    return output_path
