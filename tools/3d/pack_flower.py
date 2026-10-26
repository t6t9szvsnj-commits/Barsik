"""Упаковка гортензии для версии 3.

    python3 tools/3d/pack_flower.py <папка рендера> [versions/sprites/barsik]

Берёт flower_carry.png и flower_gift.png от `hydrangea.py`, уменьшает до
размера рядом с Барсиком, обрезает пустоту и пишет flower.json с точками уже в
координатах готовых картинок: grip — кончик стебля (его держит Барсик),
base — где цветок касается стола.

Нужен Pillow — это инструмент подготовки, самой программе он не нужен.
"""
import json
import os
import sys

from PIL import Image

SRC = sys.argv[1]
DST = sys.argv[2] if len(sys.argv) > 2 else os.path.join("versions", "sprites", "barsik")
PAD = 2
ALPHA_MIN = 10          # как в pack_sprites: почти прозрачный хвост тени не нужен
# Шапка цветка чуть меньше головы Барсика. В зубах — поменьше: впереди него
# в окне всего ~65 px, и цветок не должен вылезать за окно.
SCALE = {"carry": 0.62, "gift": 0.6}
POINT = {"carry": "grip", "gift": "base"}

out = {}
for mode, scale in SCALE.items():
    with open(os.path.join(SRC, "flower_%s.json" % mode), encoding="utf-8") as f:
        info = json.load(f)
    im = Image.open(os.path.join(SRC, "flower_%s.png" % mode)).convert("RGBA")
    w, h = round(im.width * scale), round(im.height * scale)
    im = im.resize((w, h), Image.LANCZOS)
    im.putalpha(im.getchannel("A").point(lambda a: a if a >= ALPHA_MIN else 0))
    box = im.getchannel("A").getbbox()
    left, top = max(0, box[0] - PAD), max(0, box[1] - PAD)
    right, bottom = min(w, box[2] + PAD), min(h, box[3] + PAD)
    im.crop((left, top, right, bottom)).save(os.path.join(DST, "flower_%s.png" % mode),
                                              optimize=True)
    x, y = info[POINT[mode]]
    out[mode] = {"size": [right - left, bottom - top],
                 POINT[mode]: [round(x * scale - left, 1), round(y * scale - top, 1)]}

with open(os.path.join(DST, "flower.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False))
