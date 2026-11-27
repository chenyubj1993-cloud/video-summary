"""把框图 PNG 裁到内容边界并留白（Chrome 截图带大量底部空白）。"""

import pathlib

from PIL import Image, ImageChops

D = pathlib.Path(r"D:\video_summary\docs\figures")

for p in sorted(D.glob("*.png")):
    im = Image.open(p).convert("RGB")
    diff = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255)))
    box = diff.getbbox()
    if not box:
        print(f"{p.name}: 全白，跳过")
        continue
    pad = 24
    box = (max(0, box[0] - pad), max(0, box[1] - pad),
           min(im.width, box[2] + pad), min(im.height, box[3] + pad))
    im.crop(box).save(p, optimize=True)
    print(f"{p.name}: {im.width}x{im.height} -> {box[2]-box[0]}x{box[3]-box[1]}")
