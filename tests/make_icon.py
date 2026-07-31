"""生成 dcwatch 图标：圆角方块 + 一个点和两道信号弧（= 在旁听）。
输出 dcwatch.ico（16/32/48/64/128/256 多尺寸）和 icon.svg（给网页 favicon 用）。
用法：python3 make_icon.py <输出目录>
"""
import io
import struct
import sys
from pathlib import Path
from PIL import Image, ImageDraw

ACCENT = (201, 100, 66, 255)      # #c96442，跟界面强调色一致
WHITE = (255, 255, 255, 255)
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
SS = 8                            # 超采样倍数，缩回去就有抗锯齿


def draw(size: int) -> Image.Image:
    s = size * SS
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=ACCENT)
    cx, cy = s * 0.33, s * 0.5                     # 圆点（信号源）偏左
    r = s * 0.093
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE)
    w = max(SS, int(s * 0.082))                    # 弧线粗细，小尺寸下不能太细
    for rad in (s * 0.255, s * 0.40):              # 两道向右张开的弧
        d.arc([cx - rad, cy - rad, cx + rad, cy + rad], start=-52, end=52, fill=WHITE, width=w)
    return im.resize((size, size), Image.LANCZOS)


def blob(im: Image.Image, size: int) -> bytes:
    """一个尺寸的图像数据。小尺寸用传统 DIB（兼容性最好，PNG 条目在某些 Windows
    界面上会显示空白），256 那一档按惯例用 PNG，否则光它一个就 256KB。"""
    buf = io.BytesIO()
    if size >= 256:
        im.save(buf, format="PNG")
        return buf.getvalue()
    im.save(buf, format="ICO", sizes=[(size, size)], bitmap_format="bmp")
    d = buf.getvalue()
    ln, off = struct.unpack("<II", d[6 + 8:6 + 16])       # 单条目 ICO：直接把那条抠出来
    return d[off:off + ln]


sizes = [16, 32, 48, 64, 256]
blobs = [(n, blob(draw(n), n)) for n in sizes]
ico = OUT / "dcwatch.ico"
head = struct.pack("<HHH", 0, 1, len(blobs))
off = 6 + 16 * len(blobs)
dirs, data = b"", b""
for n, b in blobs:
    dirs += struct.pack("<BBBBHHII", 0 if n >= 256 else n, 0 if n >= 256 else n,
                        0, 0, 1, 32, len(b), off)
    data += b
    off += len(b)
ico.write_bytes(head + dirs + data)
print("写出", ico, ico.stat().st_size, "字节")

svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\
<rect width="64" height="64" rx="14" fill="#c96442"/>\
<circle cx="21" cy="32" r="6" fill="#fff"/>\
<path d="M29.4 22.4a15.4 15.4 0 0 1 0 19.2" stroke="#fff" stroke-width="5.2" fill="none" stroke-linecap="round"/>\
<path d="M38.4 16.8a25.6 25.6 0 0 1 0 30.4" stroke="#fff" stroke-width="5.2" fill="none" stroke-linecap="round"/>\
</svg>"""
(OUT / "icon.svg").write_text(svg, encoding="utf-8")
print("写出", OUT / "icon.svg")
print("favicon 用的 data URI：")
from urllib.parse import quote
print("data:image/svg+xml," + quote(svg, safe="=:/\"' <>"))
