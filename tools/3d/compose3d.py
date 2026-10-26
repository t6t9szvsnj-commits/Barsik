"""Склейка кадров 3D-кошки в гифки: ходьба «на дорожке» и облёт камерой.

    python3 tools/3d/compose3d.py [папка]     (по умолчанию render3d)
"""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = sys.argv[1] if len(sys.argv) > 1 else "render3d"
SRC = HERE
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)

# геометрия рендера — должна совпадать с cat3d.py
RENDER_W, ORTHO = 400, 2.85
STRIDE, WALK_FRAMES, CAM_AZ = 0.13, 24, 30
PX_PER_UNIT = RENDER_W / ORTHO

# Пол должен ехать ровно со скоростью, с которой опорная лапа уходит назад:
# 2 * шаг за полцикла. Иначе лапы скользят — и это сразу видно.
px_per_cycle = 2 * STRIDE * 2 * math.cos(math.radians(CAM_AZ)) * PX_PER_UNIT

TILE = 46
SCALE = TILE / px_per_cycle            # масштаб подобран, чтобы клетка = цикл
W, H = 520, 300
PINK, WHITE, MID = (246, 214, 222), (253, 246, 247), (250, 230, 235)


def gingham(offset):
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    for i in range(-2, W // TILE + 3):
        x0 = i * TILE - offset % (2 * TILE)
        for j in range(0, H // TILE + 2):
            y0 = j * TILE
            col = i % 2 == 0
            row = j % 2 == 0
            color = PINK if (col and row) else MID if (col or row) else WHITE
            d.rectangle([x0, y0, x0 + TILE - 1, y0 + TILE - 1], fill=color)
    return img


def sprite(name):
    im = Image.open(os.path.join(SRC, name)).convert("RGBA")
    return im.resize((round(im.width * SCALE), round(im.height * SCALE)),
                     Image.LANCZOS)


def label(img, text):
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([10, 10, 20 + d.textlength(text, font=FONT), 34],
                        radius=8, fill=(255, 255, 255))
    d.text((15, 13), text, font=FONT, fill=(90, 73, 58))


def save_gif(frames, path, ms):
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=200) for f in frames]
    pal[0].save(path, save_all=True, append_images=pal[1:], duration=ms,
                loop=0, disposal=2, optimize=True)
    print(os.path.basename(path), len(frames), "кадров,",
          round(os.path.getsize(path) / 1024), "КБ")


walk = [sprite("walk_%02d.png" % i) for i in range(WALK_FRAMES)]
frames = []
for f in range(WALK_FRAMES * 2):                    # два цикла = бесшовная петля
    bg = gingham(round(px_per_cycle * SCALE * f / WALK_FRAMES)).convert("RGBA")
    spr = walk[f % WALK_FRAMES]
    bg.alpha_composite(spr, ((W - spr.width) // 2, (H - spr.height) // 2 + 14))
    label(bg, "3D-кошка: ходьба")
    frames.append(bg.convert("RGB"))
save_gif(frames, os.path.join(HERE, "кошка3d-ходьба.gif"), 40)

turn = sorted(n for n in os.listdir(SRC) if n.startswith("turn_"))
frames = []
for name in turn:
    bg = gingham(0).convert("RGBA")
    spr = sprite(name)
    bg.alpha_composite(spr, ((W - spr.width) // 2, (H - spr.height) // 2 + 14))
    label(bg, "3D-кошка: облёт камерой")
    frames.append(bg.convert("RGB"))
save_gif(frames, os.path.join(HERE, "кошка3d-облёт.gif"), 70)
print("масштаб спрайта:", round(SCALE, 3), "| пол едет", round(px_per_cycle * SCALE / WALK_FRAMES, 2), "px/кадр")
