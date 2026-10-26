"""Иконка приложения Барсик.app из рендера морды.

    blender -b -P tools/3d/v3_cat.py -- icon <папка> 64 1
    python3 tools/app/make_icon.py <папка>/icon_fur.png

Кладёт tools/app/barsik.icns. Форма — как у иконок macOS: скруглённый
квадрат 824 из 1024 с полями, иначе в Доке Барсик выглядел бы крупнее соседей.
Нужен Pillow — это инструмент подготовки, приложению он не нужен.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

SRC = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "barsik.icns")
PREVIEW = os.path.join(HERE, "barsik-icon.png")

SIZE, TILE, RADIUS = 1024, 824, 185
TOP, BOTTOM = (226, 243, 252), (168, 214, 240)     # светло-голубой

render = Image.open(SRC).convert("RGBA")
# голова с ушами и немного груди; крупнее — уши срежутся краем плитки
head = render.crop((150, 210, 900, 960)).resize((TILE, TILE), Image.LANCZOS)

tile = Image.new("RGBA", (TILE, TILE))
grad = ImageDraw.Draw(tile)
for y in range(TILE):
    t = y / (TILE - 1)
    grad.line([(0, y), (TILE, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,))
tile.alpha_composite(head, (0, 40))

mask = Image.new("L", (TILE, TILE), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, TILE - 1, TILE - 1], RADIUS, fill=255)
tile.putalpha(mask)

icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
off = (SIZE - TILE) // 2
ImageDraw.Draw(shadow).rounded_rectangle([off, off + 12, off + TILE, off + TILE + 12],
                                         RADIUS, fill=(0, 0, 0, 70))
icon.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14)))
icon.alpha_composite(tile, (off, off))

icon.save(PREVIEW)
icon.save(OUT, sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
print("иконка:", OUT, os.path.getsize(OUT) // 1024, "КБ")
