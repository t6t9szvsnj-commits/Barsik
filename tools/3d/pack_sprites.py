"""Упаковка кадров Барсика для версии 3.

    python3 tools/3d/pack_sprites.py <папка рендера> [versions/sprites/barsik]

Берёт то, что выдал `v3_cat.py -- sprites`, обрезает все кадры по общей
рамке (у рендера много пустого места над головой, а окно на столе должно
быть минимальным), пересчитывает координаты в meta.json и пишет сжатые PNG.

Нужен Pillow — это инструмент подготовки, самой программе он не нужен.
"""
import json
import os
import sys

from PIL import Image

SRC = sys.argv[1]
DST = sys.argv[2] if len(sys.argv) > 2 else os.path.join("versions", "sprites", "barsik")
PAD = 2
# Хвост тени от лампы тянется почти прозрачным шумом до края кадра. На столе
# его не видно, а окно из-за него выходило во всю ширину рендера. Всё, что
# прозрачнее этого, стираем.
ALPHA_MIN = 10

with open(os.path.join(SRC, "meta.json"), encoding="utf-8") as f:
    meta = json.load(f)

frames = {}
box = None
for anim, infos in meta["anims"].items():
    for i in range(len(infos)):
        name = "%s_%02d.png" % (anim, i)
        im = Image.open(os.path.join(SRC, name)).convert("RGBA")
        alpha = im.getchannel("A").point(lambda a: a if a >= ALPHA_MIN else 0)
        im.putalpha(alpha)
        frames[name] = im
        b = alpha.getbbox()
        if b:
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]),
                                         max(box[2], b[2]), max(box[3], b[3]))

w, h = meta["size"]
left, top = max(0, box[0] - PAD), max(0, box[1] - PAD)
right, bottom = min(w, box[2] + PAD), min(h, box[3] + PAD)

os.makedirs(DST, exist_ok=True)
# убираем только старые кадры самого Барсика: гортензия (flower_*) лежит там
# же, но рендерится отдельно и перерисовывать её вместе с котом незачем
for old in os.listdir(DST):
    if old.endswith(".png") and old.split("_")[0] in meta["anims"]:
        os.remove(os.path.join(DST, old))
for name, im in frames.items():
    im.crop((left, top, right, bottom)).save(os.path.join(DST, name), optimize=True)


def shift(pt):
    return [round(pt[0] - left, 1), round(pt[1] - top, 1)]


meta["size"] = [right - left, bottom - top]
meta["anchor"] = shift(meta["anchor"])
for infos in meta["anims"].values():
    for info in infos:
        for key in ("mouth", "crown"):
            info[key] = shift(info[key])
with open(os.path.join(DST, "meta.json"), "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, indent=1)

total = sum(os.path.getsize(os.path.join(DST, n)) for n in frames)
print("кадров: %d, размер кадра %dx%d, всего %d КБ" % (
    len(frames), right - left, bottom - top, total // 1024))
