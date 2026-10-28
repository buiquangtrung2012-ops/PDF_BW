"""Tạo icon.png / icon.ico (tài liệu nửa đen nửa trắng)."""
from PIL import Image, ImageDraw

S = 512
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((20, 20, S - 20, S - 20), radius=90, fill=(27, 30, 37, 255))
# tờ giấy
x0, y0, x1, y1 = 120, 70, 392, 442
fold = 80
d.polygon([(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)], fill=(255, 255, 255, 255))
# nửa đen bên phải
d.polygon([((x0 + x1) // 2, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), ((x0 + x1) // 2, y1)], fill=(20, 20, 20, 255))
d.polygon([(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)], fill=(120, 130, 150, 255))
# dòng chữ
for i, y in enumerate(range(190, 400, 48)):
    d.rounded_rectangle((x0 + 28, y, (x0 + x1) // 2 - 8, y + 16), radius=8, fill=(20, 20, 20, 255))
    d.rounded_rectangle(((x0 + x1) // 2 + 8, y, x1 - 28, y + 16), radius=8, fill=(255, 255, 255, 255))
im.save("icon.png")
im.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("ok")
