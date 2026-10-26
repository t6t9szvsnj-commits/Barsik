#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Барсик — рыжий кот, который живёт на рабочем столе. Версия 3.

Чем отличается от версии 2 (desktop_cat.py): там кошка нарисована
линиями, а здесь — настоящий Барсик, трёхмерная модель с шерстью, собранная
по его фотографиям и видео. Каждая поза заранее отрисована в кадры
(sprites/barsik), программа только подставляет нужный кадр.

Всё остальное — общее с версией 2 и не копируется, а наследуется: мозг кота
(куда идти, что нести, когда спать), окна поверх стола, панель «Ask
anything…», подтверждение, откат. А файлы двигает всё тот же движок из
версии 1 с теми же правилами безопасности:

  - трогаются только файлы внутри ALLOWED_ROOTS (Стол и Загрузки);
  - удаление = переезд в Корзину, никогда не насовсем;
  - перед работой кот показывает план и ждёт подтверждения;
  - «Отменить» возвращает всё на место.

Запуск:
    python3 desktop_barsik.py             обычный режим
    python3 desktop_barsik.py --demo      показательные выступления,
                                         файлы НЕ трогаются вообще
    python3 desktop_barsik.py --check     диагностика: что умеет этот Mac
                                         и на месте ли кадры Барсика
"""

import argparse
import dataclasses
import json
import math
import os
import struct
import sys
from pathlib import Path

import tkinter as tk

import desktop_cat as base
import macimages

CAT_NAME = os.environ.get("CAT_NAME", "Барсик")
# В собранном приложении Барсик.app данные лежат не рядом с кодом, а там, куда
# их положил PyInstaller (sys._MEIPASS).
_HERE = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)
SPRITES = _HERE / "sprites" / "barsik"

# Какие позы нужны программе. Кадры рендерит tools/3d/v3_cat.py.
ANIMS = ("walk", "stand", "sit", "blink", "sleep", "pick", "hop")

# За столько кадров проходит один цикл шага. Скорость ходьбы выводится из
# этого и из meta.json так, чтобы опорная лапа стояла на столе, а не ехала.
FRAMES_PER_STRIDE = 16
TOP_PAD = 46            # место над спрайтом: сердечки, вопрос, «z z z»

# Гортензия. Барсик иногда уходит за край экрана и возвращается с ней в зубах.
# Раз в полторы минуты безделья шанс подрастает на 5% и бросается жребий,
# после подарка — снова с нуля. В среднем выходит раз в ~8 минут безделья, а к двадцатому
# жребию, через полчаса, шанс доходит до 100%. За
# работой, пока он думает или ждёт «да/нет», часы стоят.
GIFT_EVERY_MS = 90 * 1000
GIFT_STEP = 0.05
AWAY_FRAMES = 75                # сколько он «ищет» цветок за краем экрана
PRESENT_FRAMES = base.DROP_FRAMES
FLOWER_AHEAD = 108              # где цветок ложится: столько px перед лапами
FLOWER_STAYS_MS = 15 * 60 * 1000    # потом «вянет» — окно исчезает само
FLOWER = "гортензия"            # что у него в зубах, вместо имени файла
AWAY = "away"                   # за краем экрана
PRESENT = "present"             # кладёт цветок перед собой

# Цвета: светло-голубой основной, вкрапления чёрного,
# винного, молочного и тёмно-тёмно-зелёного. Пары «текст — фон» проверены
# на читаемость тестом (контраст по WCAG).
BLUE = "#bfe2f4"            # светло-голубой — основной
BLACK = "#141414"
WINE = "#6e1f33"            # бордовый, ближе к винному
WINE_DARK = "#5a1426"       # винный для текста прямо на стекле
MILK = "#f6f0e4"            # молочный
GREEN = "#12301f"           # тёмно-тёмно-зелёный

PALETTE = base.Palette(
    bar_bg=BLUE, bar_edge=GREEN, bar_text=BLACK, bar_hint="#3f5e6e",
    # Панель — «матовое стекло»: просвечивает только фон, текст остаётся
    # чётким. Плотность 0.65 — с ней чёрный текст читается даже на чёрных
    # обоях; строка состояния поэтому тёмно-винная, а не просто винная.
    bar_status=WINE_DARK, bar_alpha=1.0, bar_glass=0.65,
    yes_bg=GREEN, yes_text=MILK, no_bg=WINE, no_text=MILK,
    bubble_bg=MILK, bubble_ink=BLACK, bubble_edge=GREEN,
    ink=BLACK, paper=MILK, paper_line="#9fcbe4",
    folder="#9fd3f0", folder_dark="#6fb3dc",
    spark="#8ed0f2", heart="#8e2440", dust="#d9d0bf",
)

# Прежняя панель — без стекла, вся полупрозрачная через -alpha. Осталась
# переключателем (--no-glass или BARSIK_PANEL=plain), а не копией файла: так
# обе версии получают все остальные правки.
PALETTE_PLAIN = dataclasses.replace(PALETTE, bar_alpha=0.75, bar_glass=0.0, bar_status=WINE)
PLAIN_WORDS = ("plain", "old", "обычная", "старая", "0", "no", "нет")


def wants_glass(no_glass_flag=False, environ=None, settings_file=None):
    """Стекло или прежняя панель. Флаг из Терминала важнее переменной
    окружения, она — важнее строки BARSIK_PANEL в ~/.desktop_assistant_ai.
    Барсик.app запускают не из Терминала, поэтому файл тут главный путь."""
    if no_glass_flag:
        return False
    environ = os.environ if environ is None else environ
    value = environ.get("BARSIK_PANEL") or base.engine.read_ai_settings(
        settings_file or base.engine.AI_SETTINGS_FILE, ("BARSIK_PANEL",)).get("BARSIK_PANEL", "")
    return value.strip().lower() not in PLAIN_WORDS


# Мозг у Барсика тот же, фразы свои — он кот, а не кошка.
PHRASES = dict(base.PHRASES)
PHRASES.update({
    "idle": ["мр", "я тут", "скучно…", "почешешь?", "мрр", "поиграем?"],
    "confused": ["не понял", "чего?", "мр?"],
    "undone": ["вернул {} шт."],
    "bad_command": ["не понял команду"],
    "fetch": ["я щас", "подожди-ка", "я скоро"],
    "gift": ["это тебе ♥", "нашёл тебе цветок", "держи, это тебе"],
    "thanks": ["мррр ♥", "понравилось?", "мур ♥"],
})


# ---------------------------------------------------------------------------
# Кадры и их описание
# ---------------------------------------------------------------------------

def png_size(path):
    """Размер PNG без сторонних библиотек: ширина и высота лежат в IHDR."""
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def frame_name(anim, i):
    return "%s_%02d.png" % (anim, i)


def load_meta(folder=None):
    """Описание кадров или None, если его нет или оно битое."""
    try:
        with open(Path(folder or SPRITES) / "meta.json", encoding="utf-8") as f:
            meta = json.load(f)
        meta["size"], meta["anchor"], meta["cycle_px"], meta["anims"]
        return meta
    except (OSError, ValueError, KeyError, TypeError):
        return None


def sprite_problems(folder=None):
    """Список того, чего не хватает, простыми словами. Пустой — всё на месте."""
    folder = Path(folder or SPRITES)
    meta = load_meta(folder)
    if meta is None:
        return ["нет описания кадров: {}".format(folder / "meta.json")]
    problems = []
    size = tuple(meta["size"])
    for anim in ANIMS:
        frames = meta["anims"].get(anim)
        if not frames:
            problems.append("нет позы «{}»".format(anim))
            continue
        for i in range(len(frames)):
            path = folder / frame_name(anim, i)
            real = png_size(path) if path.exists() else None
            if real is None:
                problems.append("нет кадра {}".format(path.name))
            elif real != size:
                problems.append("кадр {} размером {}x{}, а не {}x{}".format(
                    path.name, real[0], real[1], *size))
    return problems


def load_flower(folder=None):
    """Описание картинок гортензии или None, если их нет: тогда Барсик просто
    не носит цветы, а всё остальное работает как раньше."""
    folder = Path(folder or SPRITES)
    try:
        with open(folder / "flower.json", encoding="utf-8") as f:
            flower = json.load(f)
        for kind, point in (("carry", "grip"), ("gift", "base")):
            flower[kind][point]
            if png_size(folder / "flower_{}.png".format(kind)) != tuple(flower[kind]["size"]):
                return None
        return flower
    except (OSError, ValueError, KeyError, TypeError):
        return None


def window_size(meta):
    """Окно шире спрайта: точка между лапами должна быть ровно посередине
    окна (так его двигает версия 2), а зеркальный кадр — тоже влезать."""
    sw, sh = meta["size"]
    ax, ay = meta["anchor"]
    half = math.ceil(max(ax, sw - ax))
    return 2 * half, TOP_PAD + sh, TOP_PAD + ay


# ---------------------------------------------------------------------------
# Мозг: тот же, что у версии 2, плюс своя походка и фразы
# ---------------------------------------------------------------------------

class GiftChance:
    """Пора ли нести гортензию. Раз в every кадров шанс подрастает на step
    и бросается жребий; после удачи шанс снова с нуля. Так цветок приходит
    не по расписанию и не слишком часто, но и не пропадает надолго: к
    двадцатому жребию шанс доходит до 100%."""

    def __init__(self, rng, every, step=GIFT_STEP):
        self.rng = rng
        self.every = max(1, int(every))
        self.step = step
        self.chance = 0.0
        self.clock = 0

    def tick(self):
        self.clock += 1
        if self.clock < self.every:
            return False
        self.clock = 0
        self.chance = min(1.0, self.chance + self.step)
        if self.rng.random() < self.chance:
            self.chance = 0.0
            return True
        return False


class BarsikBrain(base.CatBrain):
    phrases = PHRASES
    phase_step = 2 * math.pi / FRAMES_PER_STRIDE
    gifts = False               # есть ли картинки гортензии

    def __init__(self, screen, rng=None):
        super().__init__(screen, rng)
        self.hold = False       # приложение занято: думает или ждёт «да/нет»
        self.gift_due = False
        # часы в кадрах, а кадр берётся на момент запуска: сквозная проверка
        # ускоряет время, и цветок не должен сыпаться ей под ноги
        self.gift_chance = GiftChance(self.rng, GIFT_EVERY_MS / base.FRAME_MS)
        self._dropped = []

    @property
    def busy(self):
        return super().busy or self.state in (AWAY, PRESENT)

    # --- гортензия ----------------------------------------------------------

    def bring_flower(self):
        """Уйти за ближний край экрана — туда, где его не видно."""
        self.gift_due = False
        away = self.screen.cat_w
        edge = -away if self.x < self.screen.w / 2 else self.screen.w + away
        self.target = (edge, self.y)        # мимо clamp: он и правда уходит
        self.purpose = "fetch"
        self._set_state(base.WALK)
        self.speak(self._phrase("fetch"), frames=60)

    def _let_go(self):
        """Положить цветок перед собой. Событие уйдёт наружу на ближайшем
        кадре: сюда можно попасть и между кадрами — когда приложение зовёт
        работать, а он как раз несёт цветок."""
        if self.carrying != FLOWER:
            return
        self.carrying = None
        self._dropped.append(("gift", (self.x + self.facing * FLOWER_AHEAD, self.y,
                                       self.facing)))

    def _go(self, target, purpose):
        if purpose != "present":
            self._let_go()          # позвали работать — кладёт цветок, где стоит
        super()._go(target, purpose)

    def start_job(self, trips, leftover=None):
        self._let_go()
        super().start_job(trips, leftover)

    def be_confused(self):
        self._let_go()
        super().be_confused()

    def tick(self):
        if self.gifts and not self.hold and not self.busy:
            if self.gift_chance.tick():
                self.gift_due = True
            if self.gift_due and self.state in (base.SIT, base.SLEEP):
                self.bring_flower()
        events = super().tick()
        events[:0], self._dropped = self._dropped, []
        # Сесть за краем экрана он не должен ни при каких поворотах: если
        # его отвлекли, пока он там, — домой.
        if self.state in (base.SIT, base.SLEEP) and not 0 <= self.x <= self.screen.w:
            self._go(self.screen.perch(), "home")
        return events

    def _arrive(self):
        if self.purpose == "fetch":
            self._set_state(AWAY)
        elif self.purpose == "present":
            self._set_state(PRESENT)
        else:
            super()._arrive()

    def _tick_away(self):
        if self.frame >= AWAY_FRAMES:
            self.carrying = FLOWER
            self._go(self.screen.perch(), "present")

    def _tick_present(self):
        if self.frame == PRESENT_FRAMES // 2:       # шапка коснулась стола
            self._let_go()
        if self.frame >= PRESENT_FRAMES:
            self.speak(self._phrase("gift"), frames=110)
            self._set_state(base.PET)               # жмурится, сердечки

    def thank(self):
        """Цветок взяли."""
        self.speak(self._phrase("thanks"), frames=70)
        if not self.busy:
            self._set_state(base.PET)


def brain_for(meta, flower=None):
    """Мозг, у которого шаг совпадает с кадрами: за цикл шага кот проходит
    ровно столько пикселей, на сколько в кадрах уезжает опорная лапа."""
    return type("BarsikBrain", (BarsikBrain,),
                {"walk_speed": meta["cycle_px"] / FRAMES_PER_STRIDE,
                 "gifts": flower is not None})


def choose_frame(brain, meta):
    """Какую позу и какой её кадр показать сейчас."""
    def count(anim):
        return len(meta["anims"][anim])

    state, frame, t = brain.state, brain.frame, brain.t
    if state in (base.WALK, base.CARRY):
        n = count("walk")
        return "walk", int(brain.phase / (2 * math.pi) * n) % n
    if state in (base.PICK, base.DROP, PRESENT):
        total = {base.PICK: base.PICK_FRAMES, base.DROP: base.DROP_FRAMES,
                 PRESENT: PRESENT_FRAMES}[state]
        n = count("pick")
        return "pick", min(n - 1, max(0, frame) * n // max(1, total))
    if state == base.SLEEP:
        return "sleep", (t // 6) % count("sleep")
    if state == base.SWEEP:
        return "stand", frame % count("stand")
    if state == base.HAPPY:
        return "hop", (frame // 2) % count("hop")
    if state == base.PET:
        return "blink", 0                      # жмурится от удовольствия
    if (t % 130) < 5:
        return "blink", 0
    return "sit", (t // 4) % count("sit")


# ---------------------------------------------------------------------------
# Рисование: кадр Барсика и мелочи поверх (бумажка, папка, сердечки…)
# ---------------------------------------------------------------------------

class BarsikArtist(base.CatArtist):
    """Кладёт кадр так, чтобы точка между лапами пришлась в (ox, oy), и
    дорисовывает предметы. Локальные координаты те же, что у версии 2:
    (0,0) — пол между лапами, вверх — минус, вперёд — плюс."""

    def __init__(self, canvas, meta, images, flower=None):
        super().__init__(canvas)
        self.meta = meta
        self.images = images          # images(anim, i, facing) -> картинка
        self.flower = flower
        self.native = None            # на Маке: картинки рисует сам macOS

    def _local(self, point):
        ax, ay = self.meta["anchor"]
        return point[0] - ax, point[1] - ay

    def draw(self, brain, ox, oy):
        self.ox, self.oy, self.f = ox, oy, brain.facing
        anim, i = choose_frame(brain, self.meta)
        info = self.meta["anims"][anim][i]
        sw, sh = self.meta["size"]
        ax, ay = self.meta["anchor"]
        left = ox - ax if brain.facing > 0 else ox - (sw - ax)
        if self.native:
            self.native.begin()
        self._picture("cat", anim, i, left, oy - ay, sw, sh)

        mx, my = self._local(info["mouth"])
        cx, cy = self._local(info["crown"])
        state, frame = brain.state, brain.frame
        with_flower = brain.carrying == FLOWER and state in (base.CARRY, PRESENT)
        if with_flower:
            self._flower_in_mouth(mx, my)
        elif self.native:
            self.native.hide("flower")
        if self.native:
            self.native.commit()

        if state == base.PICK:
            self._paper(mx + 14, -8, small=True)
        elif state == base.DROP:
            prog = frame / max(1, base.DROP_FRAMES)
            half = self.c.winfo_width() * 0.5
            # папка стоит чуть выше пола: под лапами в окне всего ~10 px,
            # и нижний край папки срезался
            self._folder(min(mx + 34, half - 14), -12)
            if prog < 0.6:
                self._paper(mx + 6, my + 12, small=True)
        elif state == base.CARRY and brain.carrying and not with_flower:
            self._paper(mx + 6, my + 14)

        if state == base.HAPPY:
            self._sparkles_at(cx, cy, frame)
        elif state == base.PET:
            self._hearts_at(cx, cy, frame)
        elif state == base.CONFUSED:
            self._question_at(cx, cy, frame)
        elif state == base.SLEEP:
            self._zzz_at(cx, cy, brain.t)
        elif state == base.SWEEP:
            self._dust(frame)

    def _flower_in_mouth(self, mx, my):
        """Кончик стебля в пасти, шапка свисает перед грудью."""
        if not self.flower:
            return
        w, h = self.flower["carry"]["size"]
        gx, gy = self.flower["carry"]["grip"]
        x, y = self._pt(mx, my)
        self._picture("flower", "flower_carry", 0, x - (gx if self.f > 0 else w - gx), y - gy, w, h)

    def _picture(self, slot, anim, i, left, top, w, h):
        """Кадр с левым верхним углом в (left, top) — через Tk или macOS."""
        if self.native:
            self.native.put(slot, anim, i, self.f, left, top, w, h)
        else:
            self.c.create_image(left, top, image=self.images(anim, i, self.f), anchor="nw")

    # --- мелочи возле головы -------------------------------------------------

    def _hearts_at(self, cx, cy, frame):
        for i in range(3):
            k = (frame * 1.1 + i * 13) % 40
            s = 4 + i
            x, y = cx - 12 + i * 12, cy - 4 - k * 0.9
            self._oval(x - s, y - s, x, y + s * 0.4, fill=self.p.heart, outline="")
            self._oval(x, y - s, x + s, y + s * 0.4, fill=self.p.heart, outline="")
            self._poly([(x - s, y), (x + s, y), (x, y + s * 1.5)],
                       fill=self.p.heart, outline="")

    def _sparkles_at(self, cx, cy, frame):
        for i in range(6):
            k = (frame * 1.4 + i * 9) % 40
            r = max(1.0, 6 - k * 0.12)
            a = i * 1.05
            x = cx + math.cos(a) * (22 + k * 0.8) * (0.6 if i % 2 else 1.0)
            y = cy + 14 - math.sin(a) * (14 + k * 0.6)
            self._poly([(x, y - r * 2), (x + r * 0.6, y - r * 0.6), (x + r * 2, y),
                        (x + r * 0.6, y + r * 0.6), (x, y + r * 2),
                        (x - r * 0.6, y + r * 0.6), (x - r * 2, y),
                        (x - r * 0.6, y - r * 0.6)], fill=self.p.spark, outline="")

    def _question_at(self, cx, cy, frame):
        y = cy - 20 - math.sin(frame / 7.0) * 4
        self.c.create_text(*self._pt(cx + 8, y), text="?", fill=self.p.ink,
                           font=("Helvetica", 26, "bold"))

    def _zzz_at(self, cx, cy, t):
        for i in range(3):
            k = (t / 18.0 + i) % 3
            self.c.create_text(*self._pt(cx + 10 + k * 9, cy - 4 - k * 12),
                               text="z", fill=self.p.ink,
                               font=("Helvetica", int(10 + k * 4), "bold"))

    def _dust(self, frame):
        """Пыль из-под хвоста — позади кота, у пола."""
        half = self.c.winfo_width() * 0.5
        for i in range(5):
            k = (frame * 0.9 + i * 7) % 26
            r = 2 + k * 0.3
            x = -half * 0.45 - k * 1.5
            y = -14 - k * 1.4
            self._oval(x, y - r, x + r * 2, y + r,
                       fill=self.p.dust, outline="", stipple="gray50")


class SpriteSet:
    """Все кадры в памяти: и как есть (вправо), и отражённые (влево).
    Отражение делает сам Tk (copy -subsample -1 1), поэтому в репозитории
    лежат только кадры вправо."""

    def __init__(self, root, meta, folder=None, flower=None):
        folder = Path(folder or SPRITES)
        self.right, self.left, self.files = {}, {}, {}
        names = [(anim, i, frame_name(anim, i))
                 for anim in ANIMS for i in range(len(meta["anims"][anim]))]
        if flower:
            names += [("flower_" + kind, 0, "flower_{}.png".format(kind))
                      for kind in ("carry", "gift")]
        for anim, i, name in names:
            self.files[anim, i] = folder / name
            img = tk.PhotoImage(master=root, file=str(folder / name))
            flipped = tk.PhotoImage(master=root)
            root.tk.call(flipped, "copy", img, "-subsample", -1, 1)
            self.right[anim, i] = img
            self.left[anim, i] = flipped

    def __call__(self, anim, i, facing):
        return (self.right if facing > 0 else self.left)[anim, i]

    def path(self, anim, i):
        return self.files[anim, i]


class NativeSprites:
    """Кадры, которые рисует сам macOS слоями под окном (см. macimages).
    То же, что create_image на canvas, только видно и на прозрачном фоне."""

    def __init__(self, layers, sprites):
        self.layers = layers
        self.sprites = sprites

    def begin(self):
        self.layers.begin()

    def commit(self):
        self.layers.commit()

    def put(self, slot, anim, i, facing, left, top, w, h):
        img = macimages.image(("sprite", anim, i), path=self.sprites.path(anim, i))
        self.layers.show(slot, img, left, top, w, h, mirror=facing < 0)

    def hide(self, slot):
        self.layers.hide(slot)


def native_sprites(win, w, h, sprites, transparent):
    """Слои macOS для окна — или None, и тогда рисует Tk, как раньше."""
    if not transparent or not macimages.available():
        return None
    layers = macimages.Layers(win, w, h, title=CAT_NAME)
    return NativeSprites(layers, sprites) if layers.ok else None


def flower_spot(x, y, facing, flower, screen):
    """Левый верхний угол окна с цветком, который лёг в точку (x, y).
    Стеблем цветок смотрит к Барсику. Окно целиком на экране."""
    w, h = flower["gift"]["size"]
    bx, by = flower["gift"]["base"]
    if facing > 0:                  # картинка отражена: стебель влево, к нему
        bx = w - bx
    left = min(max(0, x - bx), screen.w - w)
    top = min(max(screen.menubar, y - by), screen.h - h)
    return int(left), int(top)


class FlowerWindow:
    """Гортензия на столе. Щелчок — забрали; не забрали — сама исчезнет."""

    def __init__(self, root, sprites, facing, left, top, on_take):
        image = sprites("flower_gift", 0, facing)
        w, h = image.width(), image.height()
        self.win, transparent = base.make_overlay(root, w, h)
        canvas = tk.Canvas(self.win, width=w, height=h, highlightthickness=0, bd=0,
                           bg="systemTransparent" if transparent else base.BAR_BG)
        canvas.pack()
        canvas.create_image(0, 0, image=image, anchor="nw", tags="flower")
        canvas.bind("<Button-1>", lambda e: on_take())
        self.win.geometry("{}x{}+{}+{}".format(w, h, left, top))
        self.win.deiconify()
        self.win.lift()
        self.native = native_sprites(self.win, w, h, sprites, transparent)
        if self.native:
            canvas.delete("flower")
            self.native.begin()
            self.native.put("flower", "flower_gift", 0, facing, 0, 0, w, h)
            self.native.commit()
        self.fade = root.after(FLOWER_STAYS_MS, self.destroy)
        self.root = root

    def destroy(self):
        try:
            self.root.after_cancel(self.fade)
            self.win.destroy()
        except tk.TclError:
            pass


def window_for(meta, sprites, flower=None):
    w, h, foot = window_size(meta)

    class BarsikWindow(base.CatWindow):
        W, H, FOOT = w, h, foot
        # Барсик сидит на краю панели целиком: в кадре лапы стоят на полу, и
        # если утопить их, как у Мурки, панель срезает ему лапы и хвост.
        PERCH_SINK = 0
        # Облачко с репликой стоит над окном, а не в его верхней полосе: там
        # рисуются сердечки, вопрос и «z z z», и облачко «мррр» закрывало
        # сердечки ровно тогда, когда Барсика гладят.
        BUBBLE_LIFT = TOP_PAD
        # Лапы не выше, чем позволяет рост: иначе у верхнего края экрана
        # (там на Маке лежат иконки) уши и хвост уезжали под строку меню.
        HEAD_ROOM = meta["anchor"][1]

        def __init__(self, root, screen):
            super().__init__(root, screen)
            self.artist.native = native_sprites(self.win, w, h, sprites, self.transparent)

        def make_artist(self, canvas):
            return BarsikArtist(canvas, meta, sprites, flower)

    return BarsikWindow


class BarsikApp(base.CatApp):
    name = CAT_NAME
    palette = PALETTE

    def __init__(self, root, meta, demo=False, flower=None, flower_now=False, glass=True):
        self.palette = PALETTE if glass else PALETTE_PLAIN
        self.flower = flower
        self.sprites = SpriteSet(root, meta, flower=flower)
        self.gift = None                    # окно с принесённым цветком
        self.brain_class = brain_for(meta, flower)
        self.window_class = window_for(meta, self.sprites, flower)
        super().__init__(root, demo=demo)
        self.brain.gift_due = flower_now and flower is not None

    def tick(self):
        if self.alive:
            self.brain.hold = self.busy
        super().tick()

    def handle_event(self, event):
        if event[0] == "gift":
            self.lay_flower(*event[1])
        else:
            super().handle_event(event)

    def lay_flower(self, x, y, facing):
        self.take_flower(thank=False)       # новый цветок вместо старого
        left, top = flower_spot(x, y, facing, self.flower, self.screen)
        self.gift = FlowerWindow(self.root, self.sprites, facing, left, top, self.take_flower)

    def take_flower(self, thank=True):
        if self.gift is None:
            return
        self.gift.destroy()
        self.gift = None
        if thank:
            self.brain.thank()

    def quit(self):
        self.take_flower(thank=False)
        super().quit()


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

def run_check():
    code = base.run_check()
    problems = sprite_problems()
    if problems:
        print("кадры Барсика:     НЕ ХВАТАЕТ —", "; ".join(problems[:3]))
        print("                    обнови папку с программой: git pull")
        return 1
    meta = load_meta()
    count = sum(len(meta["anims"][a]) for a in ANIMS)
    print("кадры Барсика:     на месте ({} шт., {}x{})".format(count, *meta["size"]))
    print("гортензия:         ", "на месте" if load_flower() else
          "нет — Барсик просто не будет носить цветы")
    print("панель:            ", "стекло" if wants_glass() else
          "прежняя, без стекла (BARSIK_PANEL=plain)")
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description="Барсик на рабочем столе")
    parser.add_argument("--demo", action="store_true",
                        help="показательный режим, файлы не трогаются")
    parser.add_argument("--check", action="store_true",
                        help="проверить окружение и кадры и выйти")
    parser.add_argument("--flower", action="store_true",
                        help="сразу сходить за гортензией (посмотреть, как это выглядит)")
    parser.add_argument("--no-glass", action="store_true",
                        help="прежняя панель, без стекла")
    args = parser.parse_args(argv)

    if args.check:
        return run_check()

    problems = sprite_problems()
    if problems:
        print("Не нашёл кадры Барсика ({}).".format("; ".join(problems[:3])))
        print("Они лежат в папке sprites/barsik рядом с программой — обнови "
              "её командой git pull.")
        return 1

    if sys.platform != "darwin":
        print("Версия 3 рассчитана на macOS: прозрачные окна поверх рабочего "
              "стола на других системах ведут себя иначе.")

    root = tk.Tk()
    BarsikApp(root, load_meta(), demo=args.demo, flower=load_flower(),
             flower_now=args.flower, glass=wants_glass(args.no_glass))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
