#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Мурка — кошка, которая живёт прямо на рабочем столе. Версия 2 (тестовая).

Чем отличается от версии 1 (desktop_assistant.py):

  v1 — обычное окно с нарисованным котом внутри: пишешь команду, кот
       машет метлой «за стеклом», файлы разбираются в фоне.

  v2 — кота нет в окне, кот НА рабочем столе. Прозрачное окно без рамки,
       поверх всех остальных, кошка ходит по обоям, подходит к реальному
       файлу, берёт его в зубы, несёт к папке и заталкивает внутрь — и
       ровно в этот момент файл по-настоящему переезжает. Снизу висит
       плавающая панель «Ask anything…», как в тех рилсах.

Движок (что и куда можно двигать, Корзина вместо удаления, журнал отката,
обработка отказов в доступе) целиком взят из версии 1 — здесь только
новое «лицо». Поэтому все правила безопасности те же:

  - трогаются только файлы внутри ALLOWED_ROOTS (Стол и Загрузки);
  - удаление = переезд в Корзину, никогда не насовсем;
  - перед работой кошка показывает план и ждёт подтверждения;
  - «Отменить» возвращает всё на место.

Запуск:
    python3 desktop_cat.py             обычный режим
    python3 desktop_cat.py --demo      показательные выступления,
                                       файлы НЕ трогаются вообще
    python3 desktop_cat.py --check     диагностика окружения (что умеет
                                       этот конкретный Mac), ничего не
                                       запускает
"""

import argparse
import base64
import math
import os
import queue
import random
import subprocess
import sys
import struct
import threading
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path

import tkinter as tk

import desktop_assistant as engine
import macimages

# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

CAT_NAME = os.environ.get("CAT_NAME", "Мурка")

CAT_W, CAT_H = 210, 170          # окно-спрайт вокруг кошки
FOOT_Y = CAT_H - 22              # где внутри окна «пол» под лапами
FRAME_MS = 40                    # ~25 кадров в секунду
WALK_SPEED = 3.4                 # пикселей за кадр
ARRIVE_EPS = 7.0                 # с какого расстояния считаем, что дошли
PHASE_STEP = 0.33                # на сколько проворачивается шаг за кадр

VISUAL_TRIPS = 8                 # столько файлов кошка несёт «руками»
SPEED_RAMP = 1.5                 # дальше рейсы идут быстрее
QUICK_AFTER = 3                  # с какого рейса включается ускорение

ICON_SIZE = 64                   # размер иконки на рабочем столе
MENUBAR_H = 25                   # высота строки меню macOS
DOCK_H = 90                      # примерная высота дока

HINT = "Ask anything…"           # подсказка в пустой строке
BAR_W, BAR_H = 720, 116          # плавающая панель ввода
BAR_BOTTOM_GAP = 26              # отступ панели от дока

# Шерсть и всё остальное
FUR = "#d3a163"
FUR_DARK = "#a9743a"
FUR_LIGHT = "#eccb9a"
BELLY = "#f7e8d2"
INK = "#4a3728"
EAR = "#e8a9a0"
NOSE = "#e0736e"
EYE = "#8fbf5a"
PUPIL = "#241d15"
WHITE = "#ffffff"
PAPER = "#fcfcfd"
PAPER_LINE = "#c9cdd4"
FOLDER = "#66b2f0"
FOLDER_DARK = "#3f8fd0"
SHADOW = "#5a5a5a"
SPARK = "#ffd166"
HEART = "#ff6b8b"
DUST = "#cfc6b8"

BAR_BG = "#1f1f22"
BAR_EDGE = "#3a3a40"
BAR_TEXT = "#f2f2f7"
BAR_HINT = "#8b8b93"
BUBBLE_BG = "#ffffff"
BUBBLE_INK = "#3b2f26"



@dataclass(frozen=True)
class Palette:
    """Цвета окон и предметов (бумажка, папка, сердечки…).

    По умолчанию — как у Мурки. Версия 3 подставляет свою палитру целиком,
    поэтому цвета здесь, а не константами в каждом окне."""

    bar_bg: str = BAR_BG
    bar_edge: str = BAR_EDGE
    bar_text: str = BAR_TEXT
    bar_hint: str = BAR_HINT
    bar_status: str = BAR_HINT
    bar_alpha: float = 0.96             # 1 — непрозрачная панель
    # «Матовое стекло»: полупрозрачный только фон панели, а текст, кнопки и
    # поле ввода — чёткие. Это плотность фона (0 — стекла нет, панель как
    # раньше). -alpha так не умеет: он просвечивает всё окно вместе с буквами.
    bar_glass: float = 0.0
    yes_bg: str = "#2f7d4f"
    yes_text: str = WHITE
    no_bg: str = "#3a3a40"
    no_text: str = BAR_TEXT
    bubble_bg: str = BUBBLE_BG
    bubble_ink: str = BUBBLE_INK
    bubble_edge: str = BUBBLE_INK
    ink: str = INK                      # вопрос, «z z z», обводка предметов
    paper: str = PAPER
    paper_line: str = PAPER_LINE
    folder: str = FOLDER
    folder_dark: str = FOLDER_DARK
    spark: str = SPARK
    heart: str = HEART
    dust: str = DUST


PALETTE = Palette()

# Что кошка говорит
PHRASES = {
    "idle": ["мур", "я тут", "скучно…", "почешешь?", "мрр"],
    "sleep": ["z z z", "сплю", "мур… z z"],
    "start": ["так, где бардак", "погнали", "щас разгребу", "ну поехали"],
    "carry": ["несу", "тяжёлый какой", "ух", "держу"],
    "done": ["готово!", "красота!", "хвали меня", "всё, мур"],
    "nothing": ["тут и так чисто", "нечего делать, мур", "и так порядок"],
    "confused": ["не поняла", "чего?", "мур?"],
    "pet": ["мррр", "ещё!", "мур-мур", "♥"],
    # единственные фразы с родом — у кота из версии 3 они свои
    "undone": ["вернула {} шт."],
    "bad_command": ["не поняла команду"],
}

# ---------------------------------------------------------------------------
# Экран
# ---------------------------------------------------------------------------


@dataclass
class Screen:
    """Геометрия рабочего стола. Отдельный объект, чтобы поведение кошки
    можно было гонять в тестах без всякого Tk."""

    w: int = 1440
    h: int = 900
    menubar: int = MENUBAR_H
    dock: int = DOCK_H
    cat_w: int = CAT_W          # у кота из версии 3 окно крупнее
    cat_h: int = CAT_H
    perch_sink: int = 8         # насколько лапы уходят за край панели
    head_room: float = 0.0      # сколько места от лап до макушки; 0 — по-старому

    def clamp(self, x, y):
        """Не дать кошке уйти за край экрана."""
        half = self.cat_w * 0.5
        x = min(max(x, half), max(half, self.w - half))
        top = self.menubar + (self.head_room or self.cat_h * 0.55)
        bottom = max(top, self.h - 10)
        return x, min(max(y, top), bottom)

    def bar_origin(self):
        return (self.w - BAR_W) // 2, self.h - self.dock - BAR_H - BAR_BOTTOM_GAP

    def perch(self):
        """Любимое место: сидит на верхнем краю панели, свесив лапы."""
        bx, by = self.bar_origin()
        return self.clamp(bx + BAR_W * 0.64, by + self.perch_sink)

    def beside_confirm(self, confirm_w):
        """Куда отойти, пока висит подтверждение. Оно встаёт над панелью
        ровно там, где кот сидит, и закрывало его целиком — торчали одни уши.
        Справа от окна подтверждения, на высоте панели, так чтобы окно кота
        его не задевало."""
        bx, by = self.bar_origin()
        right = bx + (BAR_W + confirm_w) / 2
        return self.clamp(right + self.cat_w * 0.55, by + self.perch_sink)

    def trash(self):
        """Куда нести то, что отправляется в Корзину — правый край дока."""
        return self.clamp(self.w - 58, self.h - self.dock * 0.5)

    def folder_spot(self, rng):
        """Запасная точка для папки, если её иконку найти не удалось."""
        return self.clamp(self.w * rng.uniform(0.66, 0.82),
                          self.menubar + self.h * rng.uniform(0.28, 0.5))

    def random_spot(self, rng):
        return self.clamp(self.w * rng.uniform(0.12, 0.88),
                          self.menubar + 170 + rng.uniform(
                              0, max(1.0, self.h - self.menubar - self.dock - 260)))


# ---------------------------------------------------------------------------
# Где на самом деле лежат иконки (через Finder)
# ---------------------------------------------------------------------------

ICON_SCRIPT = '''tell application "Finder"
    set out to ""
    repeat with itm in (get items of desktop)
        try
            set p to desktop position of itm
            set out to out & (name of itm) & tab & (item 1 of p) & tab & (item 2 of p) & linefeed
        end try
    end repeat
    return out
end tell'''


def parse_icon_positions(raw):
    """Разобрать вывод AppleScript: строки вида «имя<TAB>x<TAB>y».

    Имя файла само может содержать таб, поэтому режем справа.
    """
    positions = {}
    for line in (raw or "").splitlines():
        line = line.rstrip("\r")
        if not line.strip():
            continue
        parts = line.rsplit("\t", 2)
        if len(parts) != 3:
            continue
        name, sx, sy = parts
        try:
            positions[name] = (int(float(sx)), int(float(sy)))
        except ValueError:
            continue
    return positions


def read_desktop_icons(timeout=10):
    """Спросить у Finder, где лежат иконки.

    Требует разрешения «Автоматизация → Finder». Если его нет (или это не
    macOS) — просто вернём пустой словарь, кошка будет ходить к случайным
    точкам. Ломаться из-за этого программа не должна.
    """
    try:
        out = subprocess.run(["osascript", "-e", ICON_SCRIPT],
                             capture_output=True, text=True, timeout=timeout)
    except Exception:
        return {}
    if out.returncode != 0:
        return {}
    return parse_icon_positions(out.stdout)


def icon_point(name, positions, screen):
    """Экранная точка, к которой кошке идти за этой иконкой."""
    p = positions.get(name)
    if p is None:
        return None
    x = p[0] + ICON_SIZE * 0.5
    y = p[1] + screen.menubar + ICON_SIZE * 0.95
    return screen.clamp(x, y)


# ---------------------------------------------------------------------------
# Рейсы: план → маршрут кошки
# ---------------------------------------------------------------------------


@dataclass
class Trip:
    step: object            # engine.Step или None в демо-режиме
    pick: tuple             # откуда забрать
    drop: tuple             # куда отнести
    label: str              # имя файла — показать в облачке
    quick: bool = False     # ускоренный рейс


def plan_to_trips(plan, positions, screen, rng, max_visual=VISUAL_TRIPS):
    """Разложить план на то, что кошка понесёт лапами, и на хвост,
    который доделается пачкой.

    Возвращает (папки_создать, рейсы, остаток_шагов).
    """
    mkdirs = [s for s in plan.steps if s.op == "mkdir"]
    file_steps = [s for s in plan.steps if s.op in ("move", "trash")]

    trips, leftover = [], []
    for i, st in enumerate(file_steps):
        if i >= max_visual:
            leftover.append(st)
            continue
        name = Path(st.src).name
        pick = icon_point(name, positions, screen) or screen.random_spot(rng)
        if st.op == "trash":
            drop = screen.trash()
        else:
            folder = Path(st.dst).parent.name
            drop = icon_point(folder, positions, screen) or screen.folder_spot(rng)
        trips.append(Trip(st, pick, drop, name, quick=i >= QUICK_AFTER))
    return mkdirs, trips, leftover


def preview_lines(plan, limit=2):
    """Пара строк для окна подтверждения: что именно сейчас произойдёт."""
    lines = []
    for step in plan.steps:
        if step.op not in ("move", "trash"):
            continue
        name = Path(step.src).name
        if step.op == "trash":
            lines.append("в Корзину: " + name)
        else:
            lines.append(name + "  →  " + Path(step.dst).parent.name)
        if len(lines) >= limit:
            break
    extra = plan.count_files() - len(lines)
    if extra > 0:
        lines.append("и ещё {}".format(extra))
    return lines


def demo_trips(screen, rng, count=5):
    """Рейсы понарошку: настоящие файлы не трогаются."""
    names = ["Снимок экрана.png", "договор.pdf", "котик.jpg",
             "отчёт.xlsx", "мем.gif", "заметки.txt"]
    trips = []
    for i in range(count):
        trips.append(Trip(None, screen.random_spot(rng), screen.folder_spot(rng),
                          names[i % len(names)], quick=i >= QUICK_AFTER))
    return trips


# ---------------------------------------------------------------------------
# Мозги кошки — чистый конечный автомат, без единого обращения к Tk
# ---------------------------------------------------------------------------

SIT = "sit"
SLEEP = "sleep"
WALK = "walk"
PICK = "pick"
CARRY = "carry"
DROP = "drop"
SWEEP = "sweep"
HAPPY = "happy"
CONFUSED = "confused"
PET = "pet"

PICK_FRAMES = 18
DROP_FRAMES = 18
HAPPY_FRAMES = 48
SWEEP_FRAMES = 55
CONFUSED_FRAMES = 45
PET_FRAMES = 40


class CatBrain:
    """Где кошка, что делает и что собирается сделать дальше.

    tick() двигает всё на один кадр и возвращает список событий для
    внешнего мира, например ("apply", step) — «файл донесён, переноси
    по-настоящему». Сама она ничего не рисует и файлов не трогает.

    Характер (фразы) и походка — атрибуты класса: версия 3 наследует мозг
    целиком и меняет только их.
    """

    phrases = PHRASES
    walk_speed = WALK_SPEED
    phase_step = PHASE_STEP     # поворот цикла шага за кадр на walk_speed

    def __init__(self, screen, rng=None):
        self.screen = screen
        self.rng = rng or random.Random()
        self.x, self.y = screen.perch()
        self.facing = -1
        self.state = SIT
        self.frame = 0
        self.t = 0
        self.phase = 0.0
        self.target = None
        self.purpose = None
        self.look_x = None          # к чему повернуться, отойдя в сторону
        self.carrying = None
        self.trips = []
        self.leftover = []
        self.current = None
        self.speed = self.walk_speed
        self.done_count = 0
        self.total_count = 0
        self.say = ""
        self.say_left = 0
        self.events = []
        self.idle_left = self.rng.randint(90, 220)

    # --- вспомогательное ------------------------------------------------

    def _phrase(self, kind):
        return self.rng.choice(self.phrases[kind])

    def speak(self, text, frames=70):
        self.say = text
        self.say_left = frames

    def _set_state(self, state):
        self.state = state
        self.frame = 0

    def _go(self, target, purpose):
        self.target = self.screen.clamp(*target)
        self.purpose = purpose
        self._set_state(CARRY if self.carrying else WALK)

    @property
    def busy(self):
        """Занята работой — не отвлекать блужданиями."""
        return self.state in (WALK, PICK, CARRY, DROP, SWEEP) or bool(self.trips)

    # --- команды снаружи -------------------------------------------------

    def start_job(self, trips, leftover=None):
        self.trips = list(trips)
        self.leftover = list(leftover or [])
        self.done_count = 0
        self.total_count = len(self.trips) + len(self.leftover)
        self.carrying = None
        self.speak(self._phrase("start"))
        self._next_trip()

    def nothing_to_do(self):
        self.speak(self._phrase("nothing"), frames=90)
        self._go(self.screen.perch(), "home")

    def step_aside(self, spot, look_x):
        """Отойти и сесть, повернувшись к тому, что на x = look_x."""
        self.look_x = look_x
        self._go(spot, "aside")

    def be_confused(self):
        self.speak(self._phrase("confused"), frames=90)
        self._set_state(CONFUSED)

    def be_petted(self):
        if self.busy:
            return
        self.speak(self._phrase("pet"), frames=60)
        self._set_state(PET)

    def wake(self):
        if self.state == SLEEP:
            self._set_state(SIT)
            self.idle_left = self.rng.randint(90, 220)

    # --- один кадр --------------------------------------------------------

    def tick(self):
        self.t += 1
        self.frame += 1
        self.events = []

        if self.say_left > 0:
            self.say_left -= 1
            if self.say_left == 0:
                self.say = ""

        handler = getattr(self, "_tick_" + self.state, None)
        if handler is not None:
            handler()
        return self.events

    def _move_toward(self, target, speed):
        tx, ty = target
        dx, dy = tx - self.x, ty - self.y
        dist = math.hypot(dx, dy)
        if dist <= max(speed, ARRIVE_EPS):
            self.x, self.y = tx, ty
            return True
        self.x += dx / dist * speed
        self.y += dy / dist * speed
        if abs(dx) > 1.5:
            self.facing = 1 if dx > 0 else -1
        self.phase += self.phase_step * (speed / self.walk_speed)
        return False

    # --- состояния --------------------------------------------------------

    def _tick_sit(self):
        self.idle_left -= 1
        if self.idle_left > 0:
            return
        roll = self.rng.random()
        if roll < 0.32:
            self._set_state(SLEEP)
            self.speak(self._phrase("sleep"), frames=120)
        elif roll < 0.75:
            self._go(self.screen.random_spot(self.rng), "wander")
        else:
            self.speak(self._phrase("idle"))
        self.idle_left = self.rng.randint(120, 300)

    def _tick_sleep(self):
        if self.frame % 90 == 0:
            self.speak(self._phrase("sleep"), frames=80)
        if self.frame > 900:
            self._set_state(SIT)
            self.idle_left = self.rng.randint(120, 300)

    def _tick_walk(self):
        if self._move_toward(self.target, self.speed):
            self._arrive()

    _tick_carry = _tick_walk

    def _arrive(self):
        if self.purpose == "pick":
            self._set_state(PICK)
        elif self.purpose == "drop":
            self._set_state(DROP)
        else:
            if self.purpose == "aside":
                self.facing = 1 if self.look_x > self.x else -1
            self._set_state(SIT)
            self.idle_left = self.rng.randint(120, 300)

    def _tick_pick(self):
        if self.frame < PICK_FRAMES:
            return
        trip = self.current
        self.carrying = trip.label if trip else "файл"
        self.speak(self._phrase("carry"), frames=60)
        self.events.append(("picked", trip))
        self._go(trip.drop, "drop")

    def _tick_drop(self):
        if self.frame < DROP_FRAMES:
            return
        trip = self.current
        self.carrying = None
        self.done_count += 1
        self.events.append(("apply", trip))
        self._next_trip()

    def _tick_sweep(self):
        if self.frame == 1:
            self.events.append(("apply_batch", list(self.leftover)))
            self.done_count += len(self.leftover)
            self.leftover = []
        if self.frame >= SWEEP_FRAMES:
            self._set_state(HAPPY)

    def _tick_happy(self):
        if self.frame == 1:
            self.speak(self._phrase("done"), frames=100)
        if self.frame >= HAPPY_FRAMES:
            self.events.append(("finished", self.done_count))
            self._go(self.screen.perch(), "home")

    def _tick_confused(self):
        if self.frame >= CONFUSED_FRAMES:
            self._set_state(SIT)
            self.idle_left = self.rng.randint(120, 300)

    def _tick_pet(self):
        if self.frame >= PET_FRAMES:
            self._set_state(SIT)
            self.idle_left = self.rng.randint(120, 300)

    def _next_trip(self):
        if self.trips:
            self.current = self.trips.pop(0)
            self.speed = self.walk_speed * (SPEED_RAMP if self.current.quick else 1.0)
            self._go(self.current.pick, "pick")
        elif self.leftover:
            self.current = None
            self._set_state(SWEEP)
        else:
            self.current = None
            self._set_state(HAPPY)


# ---------------------------------------------------------------------------
# Рисование
# ---------------------------------------------------------------------------


def paw_position(hip_x, phase, stride, lift_height):
    """Где лапа в этой фазе шага, относительно точки между лапами.

    Опорная фаза (лапа на земле) — лапа едет НАЗАД относительно тела и
    этим толкает кошку вперёд. Маховая (лапа поднята) — идёт ВПЕРЁД по
    воздуху, чтобы переставиться.

    Если перепутать знак, получается лунная походка: опорная лапа скользит
    вперёд, а поднятая уносится назад. Ровно так и было.
    """
    lift = max(0.0, math.sin(phase))
    return hip_x - math.cos(phase) * stride, -lift * lift_height


BUTTON_PAD = 14                 # поля кнопки слева и справа от текста


def text_right(canvas, item, fallback):
    """Правый край уже нарисованного текста — как его нарисовал настоящий
    шрифт этой системы. fallback — если canvas не может ответить."""
    try:
        box = canvas.bbox(item)
    except tk.TclError:
        box = None
    return box[2] if box else fallback


def hex_rgb(colour):
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def glass_pixels(w, h, r, tint, density, edge):
    """Фон панели-стекла: RGBA построчно. Скруглённые углы сглажены, сверху
    светлый блик — по нему глаз и читает «стекло», а не просто цветную
    дымку. Размытия под стеклом не будет: его умеет только сам macOS
    (NSVisualEffectView), а из Tk до него не дотянуться."""
    tr, tg, tb = hex_rgb(tint)
    er, eg, eb = hex_rgb(edge)
    rows = []
    for y in range(h):
        row = bytearray()
        sheen = max(0.0, 1 - y / (h * 0.45)) * 0.35      # блик в верхней части
        for x in range(w):
            # расстояние до края скруглённого прямоугольника (минус — внутри)
            qx = max(abs(x + 0.5 - w / 2) - (w / 2 - r), 0.0)
            qy = max(abs(y + 0.5 - h / 2) - (h / 2 - r), 0.0)
            inside = math.hypot(qx, qy) - r
            cover = min(1.0, max(0.0, 0.5 - inside))
            if cover == 0.0:
                row += b"\0\0\0\0"
                continue
            line = min(1.0, max(0.0, 1.5 - abs(inside + 1)))   # рамка в 1 px
            cr = tr + (255 - tr) * sheen
            cg = tg + (255 - tg) * sheen
            cb = tb + (255 - tb) * sheen
            a = density + (1 - density) * sheen * 0.5
            cr, cg, cb = (cr + (er - cr) * line, cg + (eg - cg) * line, cb + (eb - cb) * line)
            a = (a + (0.9 - a) * line) * cover
            row += bytes((int(cr), int(cg), int(cb), int(a * 255 + 0.5)))
        rows.append(bytes(row))
    return rows


def png_bytes(w, h, rows):
    """Картинка PNG из строк RGBA — без сторонних библиотек."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    raw = b"".join(b"\0" + row for row in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def round_rect(c, x1, y1, x2, y2, r, **kw):
    """Прямоугольник со скруглёнными углами (в Tk такого нет из коробки)."""
    pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1,
           x2, y1 + r, x2, y1 + r, x2, y2 - r, x2, y2 - r, x2, y2,
           x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2,
           x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class CatArtist:
    """Рисует кошку в локальных координатах: (0,0) — точка между лапами
    на «полу», вверх — минус по Y, вперёд — плюс по X (потом зеркалится)."""

    p = PALETTE             # CatApp подставляет палитру своей версии

    def __init__(self, canvas):
        self.c = canvas

    # --- мелкие помощники ---------------------------------------------

    def _pt(self, dx, dy):
        return self.ox + self.f * dx, self.oy + dy

    def _oval(self, x1, y1, x2, y2, **kw):
        a = self._pt(x1, y1)
        b = self._pt(x2, y2)
        self.c.create_oval(min(a[0], b[0]), min(a[1], b[1]),
                           max(a[0], b[0]), max(a[1], b[1]), **kw)

    def _poly(self, pts, **kw):
        flat = []
        for dx, dy in pts:
            x, y = self._pt(dx, dy)
            flat += [x, y]
        self.c.create_polygon(flat, **kw)

    def _line(self, pts, **kw):
        flat = []
        for dx, dy in pts:
            x, y = self._pt(dx, dy)
            flat += [x, y]
        self.c.create_line(flat, **kw)

    def _leg(self, hip, paw, width, bend, color=FUR):
        hx, hy = hip
        px, py = paw
        mx = (hx + px) / 2 + bend
        my = (hy + py) / 2
        self._line([(hx, hy), (mx, my), (px, py)], smooth=True, width=width,
                   fill=color, capstyle="round")
        self._oval(px - 6, py - 5, px + 6, py + 2, fill=BELLY, outline=INK, width=1.4)

    # --- главный вход ---------------------------------------------------

    def draw(self, brain, ox, oy):
        self.ox, self.oy, self.f = ox, oy, brain.facing
        state, t, frame = brain.state, brain.t, brain.frame

        lift = 0.0
        if state == HAPPY:
            # выше нельзя: в верхней точке уши срезались краем окна
            lift = -abs(math.sin(frame / 4.6)) * 13
        elif state == PET:
            lift = -abs(math.sin(frame / 5.5)) * 6

        ground = self.oy          # тень остаётся на полу, даже когда кошка прыгает
        self.oy += lift
        self._shadow(ground, lift)

        if state == SLEEP:
            self._draw_sleeping(t)
        elif state in (WALK, CARRY):
            self._draw_walking(brain)
        elif state == PICK:
            self._draw_pouncing(frame / max(1, PICK_FRAMES))
        elif state == DROP:
            self._draw_pushing(frame / max(1, DROP_FRAMES))
        elif state == SWEEP:
            self._draw_sweeping(frame)
        else:
            self._draw_sitting(brain, t)

        if brain.carrying and state in (CARRY, DROP):
            self._carried_paper()

        if state == HAPPY:
            self._sparkles(frame)
        if state == PET:
            self._hearts(frame)
        if state == CONFUSED:
            self._question(frame)

    # --- части тела ------------------------------------------------------

    def _shadow(self, ground, lift):
        # чем выше прыжок, тем меньше и собраннее тень под лапами
        shrink = max(0.45, 1.0 - abs(lift) / 40.0)
        self.c.create_oval(self.ox - 46 * shrink, ground - 7 * shrink,
                           self.ox + 46 * shrink, ground + 7 * shrink,
                           fill=SHADOW, outline="", stipple="gray25")

    def _tail(self, sway, base=(-52, -46), curl=False):
        bx, by = base
        if curl:
            self._line([(bx, by), (bx + 18, by + 30), (bx + 52, by + 26),
                        (bx + 74, by + 6)],
                       smooth=True, width=11, fill=FUR_DARK, capstyle="round")
            return
        self._line([(bx, by), (bx - 20, by - 10 + sway * 0.4),
                    (bx - 34, by - 40 + sway), (bx - 26, by - 66 + sway * 1.4)],
                   smooth=True, width=11, fill=FUR_DARK, capstyle="round")
        for i in range(1, 4):
            k = i / 4.0
            # хвост рисуется сглаженной кривой, которая идёт внутри своих
            # опорных точек — полоски считаем по тем же долям, но ближе
            x = bx - 13 * k - 9 * k * k
            y = by - 6 - 38 * k + sway * k * 0.7
            self._line([(x - 5, y), (x + 5, y - 2)], width=3, fill=FUR, capstyle="round")

    def _body(self, back_y=-62, belly_y=-24):
        self._poly([(-58, -46), (-50, back_y - 2), (-18, back_y - 6), (16, back_y - 4),
                    (38, -54), (44, -32), (30, belly_y + 2), (-10, belly_y + 6),
                    (-44, belly_y + 2), (-60, -30)],
                   smooth=True, fill=FUR, outline=INK, width=2)
        self._poly([(-34, belly_y + 2), (0, belly_y + 10), (28, belly_y + 2),
                    (24, belly_y - 10), (-8, belly_y - 6), (-32, belly_y - 8)],
                   smooth=True, fill=BELLY, outline="")
        for dx in (-34, -18, -2, 14):
            self._line([(dx, back_y + 6), (dx + 6, back_y + 20)],
                       width=4, fill=FUR_DARK, capstyle="round")

    def _neck(self, hx, hy, top=-60, bottom=-30):
        """Шея. Без неё голова висит отдельным шариком: сглаживание Tk
        стягивает контур тела внутрь от опорных точек, и между телом и
        головой открывается щель."""
        self._poly([(hx - 30, top + 2), (hx - 2, top - 2), (hx + 2, bottom),
                    (hx - 32, bottom + 2)],
                   smooth=True, fill=FUR, outline="")

    def _head(self, hx=42, hy=-76, blink=False, closed=False, tilt=0.0):
        # уши
        for dx, dy in ((-12, -20), (12, -22)):
            self._poly([(hx + dx - 9, hy + dy + 4), (hx + dx, hy + dy - 16),
                        (hx + dx + 10, hy + dy + 2)],
                       fill=FUR, outline=INK, width=2)
            self._poly([(hx + dx - 5, hy + dy + 2), (hx + dx, hy + dy - 10),
                        (hx + dx + 6, hy + dy + 1)], fill=EAR, outline="")
        # голова
        self._oval(hx - 25, hy - 22, hx + 25, hy + 22, fill=FUR, outline=INK, width=2)
        # полоски на лбу
        for dx in (-9, 0, 9):
            self._line([(hx + dx - 2, hy - 20), (hx + dx + 1, hy - 11)],
                       width=3, fill=FUR_DARK, capstyle="round")
        # щёки
        self._oval(hx - 20, hy + 2, hx - 1, hy + 18, fill=FUR_LIGHT, outline="")
        self._oval(hx + 1, hy + 2, hx + 20, hy + 18, fill=FUR_LIGHT, outline="")
        # глаза
        ey = hy - 3 + tilt
        for dx in (-11, 10):
            if blink or closed:
                self._line([(hx + dx - 6, ey), (hx + dx, ey + 3), (hx + dx + 6, ey)],
                           smooth=True, width=2, fill=INK, capstyle="round")
            else:
                self._oval(hx + dx - 7, ey - 8, hx + dx + 7, ey + 8,
                           fill=EYE, outline=INK, width=1.6)
                self._oval(hx + dx - 3, ey - 6, hx + dx + 3, ey + 6,
                           fill=PUPIL, outline="")
                self._oval(hx + dx - 1, ey - 5, hx + dx + 3, ey - 1,
                           fill=WHITE, outline="")
        # нос и рот
        ny = hy + 10
        self._poly([(hx - 4, ny), (hx + 4, ny), (hx, ny + 4)], fill=NOSE, outline="")
        self._line([(hx, ny + 4), (hx - 6, ny + 9)], smooth=True, width=1.6, fill=INK)
        self._line([(hx, ny + 4), (hx + 6, ny + 9)], smooth=True, width=1.6, fill=INK)
        # усы
        for dy in (-3, 1, 5):
            self._line([(hx + 12, ny + dy), (hx + 34, ny + dy * 1.8 - 3)],
                       width=1.3, fill=INK)
            self._line([(hx - 12, ny + dy), (hx - 30, ny + dy * 1.6 - 2)],
                       width=1.3, fill=INK)

    # --- позы -------------------------------------------------------------

    def _draw_walking(self, brain):
        ph = brain.phase
        bob = math.sin(ph * 2) * 2.2
        sway = math.sin(ph * 0.9) * 16
        self._tail(sway)
        # дальние лапы — темнее, рисуются первыми
        for hip_x, off in ((-36, math.pi), (18, 0.0)):
            self._leg((hip_x, -40 + bob), paw_position(hip_x, ph + off, 15, 9),
                      8, 4, FUR_DARK)
        self._body(back_y=-66 + bob, belly_y=-22 + bob)
        for hip_x, off in ((-32, math.pi * 0.5), (22, math.pi * 1.5)):
            self._leg((hip_x, -38 + bob), paw_position(hip_x, ph + off, 16, 10),
                      9, 5)
        self._neck(42, -76 + bob, top=-64 + bob, bottom=-28 + bob)
        self._head(hx=42, hy=-76 + bob, blink=(brain.t % 130) < 5)

    def _draw_sitting(self, brain, t):
        breathe = math.sin(t / 16.0) * 1.6
        self._tail(math.sin(t / 22.0) * 7, base=(-40, -30), curl=True)
        # сидячее тело — вертикальнее и компактнее
        self._poly([(-36, -30), (-30, -74), (-4, -88), (22, -72), (28, -38),
                    (20, -6), (-14, -4), (-34, -8)],
                   smooth=True, fill=FUR, outline=INK, width=2)
        self._poly([(-16, -14), (6, -10), (18, -22), (10, -50), (-12, -46)],
                   smooth=True, fill=BELLY, outline="")
        for dx in (14, 0):                      # передние лапки столбиками
            self._line([(dx, -34 + breathe), (dx + 2, -6)], width=9,
                       fill=FUR, capstyle="round")
            self._oval(dx - 4, -10, dx + 9, -2, fill=BELLY, outline=INK, width=1.3)
        self._head(hx=6, hy=-96 + breathe, blink=(brain.t % 120) < 5)

    def _draw_sleeping(self, t):
        breathe = math.sin(t / 20.0) * 2.0
        self._tail(0, base=(-46, -20), curl=True)
        self._oval(-52, -50 + breathe, 44, -2, fill=FUR, outline=INK, width=2)
        self._oval(-30, -36, 24, -6, fill=BELLY, outline="")
        for dx in (-26, -8, 10):
            self._line([(dx, -46 + breathe), (dx + 8, -34)], width=3,
                       fill=FUR_DARK, capstyle="round")
        self._head(hx=26, hy=-40 + breathe, closed=True)
        for i in range(3):
            k = (t / 18.0 + i) % 3
            x, y = 54 + k * 12, -70 - k * 16
            self.c.create_text(*self._pt(x, y), text="z", fill=self.p.ink,
                               font=("Helvetica", int(10 + k * 4), "bold"))

    def _draw_pouncing(self, prog):
        """Присела и цапнула файл лапой."""
        reach = math.sin(min(1.0, prog) * math.pi) * 22
        self._tail(14, base=(-50, -40))
        self._body(back_y=-56, belly_y=-20)
        self._leg((-32, -34), (-34, 0), 9, 4, FUR_DARK)
        self._leg((-26, -32), (-20, 0), 9, 5)
        self._leg((20, -34), (30 + reach, -4 - reach * 0.3), 9, 6)
        self._leg((24, -32), (26 + reach * 0.7, -2), 9, 4)
        self._neck(40 + reach * 0.4, -64, top=-56, bottom=-24)
        self._head(hx=40 + reach * 0.5, hy=-64, tilt=2.0)
        self._paper(32 + reach, -8, small=True)

    def _draw_pushing(self, prog):
        """Заталкивает файл в папку — как на четвёртом рефе."""
        push = math.sin(min(1.0, prog) * math.pi) * 26
        self._folder(84, 0)
        self._tail(-10, base=(-50, -44))
        self._body(back_y=-58, belly_y=-22)
        self._leg((-32, -36), (-36, 0), 9, 4, FUR_DARK)
        self._leg((-24, -34), (-16, 0), 9, 5)
        self._leg((22, -36), (44 + push, -12), 9, 7)
        self._leg((26, -34), (30, 0), 9, 4)
        self._neck(42, -70, top=-60, bottom=-26)
        self._head(hx=42, hy=-70, tilt=1.0)
        if prog < 0.85:
            self._paper(52 + push, -14, small=True)

    def _draw_sweeping(self, frame):
        """Финальный аккорд: метёт хвостом, летит пыль."""
        sway = math.sin(frame / 2.4) * 22
        self._tail(sway, base=(-50, -46))
        self._body(back_y=-60, belly_y=-24)
        self._leg((-32, -36), (-34, 0), 9, 4, FUR_DARK)
        self._leg((-24, -34), (-14, 0), 9, 5)
        self._leg((20, -36), (26, 0), 9, 6)
        self._leg((26, -34), (34, 0), 9, 4)
        self._neck(42, -76, top=-64, bottom=-28)
        self._head(hx=42, hy=-76)
        for i in range(5):
            k = (frame * 0.9 + i * 7) % 26
            r = 2 + k * 0.3
            # дальше пыль не пускаем — иначе облачко срежется краем окна
            x = -46 - k * 1.7
            y = -14 - k * 1.4
            self._oval(x, y - r, x + r * 2, y + r,
                       fill=self.p.dust, outline="", stipple="gray50")

    # --- предметы ---------------------------------------------------------

    def _paper(self, dx, dy, small=False):
        w, h = (9, 11) if small else (12, 15)
        self._poly([(dx - w, dy - h), (dx + w, dy - h), (dx + w, dy + h),
                    (dx - w, dy + h)], fill=self.p.paper, outline=self.p.ink, width=1.5)
        for i in range(-1, 2):
            self._line([(dx - w + 3, dy + i * 5), (dx + w - 3, dy + i * 5)],
                       width=1, fill=self.p.paper_line)

    def _carried_paper(self):
        self._paper(60, -44)

    def _folder(self, dx, dy):
        self._poly([(dx - 24, dy - 6), (dx - 10, dy - 6), (dx - 5, dy - 12),
                    (dx + 8, dy - 12), (dx + 8, dy - 2), (dx - 24, dy - 2)],
                   fill=self.p.folder_dark, outline=self.p.ink, width=1.6)
        self._poly([(dx - 26, dy - 4), (dx + 10, dy - 4), (dx + 6, dy + 16),
                    (dx - 26, dy + 16)], fill=self.p.folder, outline=self.p.ink, width=1.8)

    def _sparkles(self, frame):
        for i in range(6):
            k = (frame * 1.4 + i * 9) % 40
            r = max(1.0, 7 - k * 0.15)
            a = i * 1.05
            x = math.cos(a) * (28 + k) * (0.6 if i % 2 else 1.0)
            y = -70 - math.sin(a) * (18 + k * 0.7)
            self._poly([(x, y - r * 2), (x + r * 0.6, y - r * 0.6), (x + r * 2, y),
                        (x + r * 0.6, y + r * 0.6), (x, y + r * 2),
                        (x - r * 0.6, y + r * 0.6), (x - r * 2, y),
                        (x - r * 0.6, y - r * 0.6)], fill=self.p.spark, outline="")

    def _hearts(self, frame):
        for i in range(3):
            k = (frame * 1.1 + i * 13) % 40
            s = 4 + i
            # выше поднимать нельзя — сердечки вылезут за окно-спрайт
            x, y = 30 + i * 12, -84 - k * 0.9
            self._oval(x - s, y - s, x, y + s * 0.4, fill=self.p.heart, outline="")
            self._oval(x, y - s, x + s, y + s * 0.4, fill=self.p.heart, outline="")
            self._poly([(x - s, y), (x + s, y), (x, y + s * 1.5)],
                       fill=self.p.heart, outline="")

    def _question(self, frame):
        y = -104 - math.sin(frame / 7.0) * 4
        self.c.create_text(*self._pt(34, y), text="?", fill=self.p.ink,
                           font=("Helvetica", 26, "bold"))


# ---------------------------------------------------------------------------
# Окна поверх рабочего стола
# ---------------------------------------------------------------------------


def make_overlay(root, w, h, focusable=False):
    """Прозрачное окно без рамки поверх всего.

    Возвращает (окно, получилось_ли_прозрачным). Всё, что может не
    поддерживаться конкретной сборкой Tk, обёрнуто — программа обязана
    запускаться даже если красоты не вышло.
    """
    win = tk.Toplevel(root)
    win.withdraw()

    if sys.platform == "darwin":
        # родной способ macOS: рамки нет, но окно остаётся полноценным
        # (в отличие от overrideredirect, который ломает ввод с клавиатуры)
        style = ("plain", "none") if focusable else ("help", "noActivates")
        try:
            root.tk.call("::tk::unsupported::MacWindowStyle", "style", str(win), *style)
        except tk.TclError:
            try:
                win.wm_overrideredirect(True)
            except tk.TclError:
                pass
    else:
        try:
            win.wm_overrideredirect(True)
        except tk.TclError:
            pass

    try:
        win.attributes("-topmost", True)
    except tk.TclError:
        pass

    transparent = False
    try:
        win.attributes("-transparent", True)
        win.configure(bg="systemTransparent")
        transparent = True
    except tk.TclError:
        try:
            win.configure(bg=BAR_BG)
        except tk.TclError:
            pass

    win.geometry("{}x{}+0+0".format(w, h))
    return win, transparent


class CatWindow:
    """Маленькое окно со спрайтом кошки, которое само ездит по экрану."""

    W, H, FOOT = CAT_W, CAT_H, FOOT_Y
    PERCH_SINK = 8              # Мурка свешивает лапы с панели
    BUBBLE_LIFT = 0             # насколько облачко выше верхнего края окна
    HEAD_ROOM = 0               # см. Screen.head_room

    def __init__(self, root, screen):
        self.screen = screen
        self.win, self.transparent = make_overlay(root, self.W, self.H)
        bg = "systemTransparent" if self.transparent else BAR_BG
        self.canvas = tk.Canvas(self.win, width=self.W, height=self.H,
                                highlightthickness=0, bd=0, bg=bg)
        self.canvas.pack()
        self.artist = self.make_artist(self.canvas)
        self.last_pos = None
        self.win.deiconify()

    def make_artist(self, canvas):
        return CatArtist(canvas)

    def bind_click(self, fn):
        self.canvas.bind("<Button-1>", lambda e: fn())

    def render(self, brain):
        self.canvas.delete("all")
        self.artist.draw(brain, self.W * 0.5, self.FOOT)
        x = int(brain.x - self.W * 0.5)
        y = int(brain.y - self.FOOT)
        if self.last_pos != (x, y):        # лишние geometry() только тормозят
            self.win.geometry("+{}+{}".format(x, y))
            self.last_pos = (x, y)

    def destroy(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def bubble_spot(cat_y, window, menubar):
    """Где поставить облачко: (y окна облачка, под лапами ли оно).

    Обычно над котом. Но у верхнего края экрана — а иконки на Маке как раз
    лежат сверху — облачко уезжало под строку меню и за экран, и реплику
    было не прочитать. Тогда оно встаёт под лапы."""
    above = cat_y - window.H + 4 - window.BUBBLE_LIFT
    if above >= menubar:
        return above, False
    return cat_y + 6, True


class BubbleWindow:
    """Облачко с репликой, летит за кошкой."""

    W, H = 260, 64
    p = PALETTE

    def __init__(self, root, palette=PALETTE):
        self.p = palette
        self.win, self.transparent = make_overlay(root, self.W, self.H)
        bg = "systemTransparent" if self.transparent else BAR_BG
        self.canvas = tk.Canvas(self.win, width=self.W, height=self.H,
                                highlightthickness=0, bd=0, bg=bg)
        self.canvas.pack()
        self.shown = False

    def update(self, text, x, y, below=False):
        if not text:
            if self.shown:
                self.win.withdraw()
                self.shown = False
            return
        self.canvas.delete("all")
        # под лапами хвостик смотрит вверх, на кота
        top, tail = (20, (22, 8)) if below else (6, (42, 56))
        # ширина — по настоящему шрифту: оценка «9 px на букву» на другом
        # шрифте обрезала край реплики
        label = self.canvas.create_text(self.W / 2, top + 19, text=text,
                                        fill=self.p.bubble_ink, font=("Helvetica", 14))
        right = text_right(self.canvas, label, self.W / 2 + len(text) * 4.5)
        w = min(self.W - 16, max(90, 2 * (right - self.W / 2) + 24))
        x0 = (self.W - w) / 2
        round_rect(self.canvas, x0, top, x0 + w, top + 38, 16,
                   fill=self.p.bubble_bg, outline=self.p.bubble_edge, width=2)
        self.canvas.create_polygon(self.W / 2 - 8, tail[0], self.W / 2 + 8, tail[0],
                                   self.W / 2 - 2, tail[1],
                                   fill=self.p.bubble_bg, outline=self.p.bubble_edge, width=2)
        self.canvas.tag_raise(label)
        self.win.geometry("+{}+{}".format(int(x - self.W / 2), int(y)))
        if not self.shown:
            self.win.deiconify()
            self.win.lift()
            self.shown = True

    def destroy(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


class CommandBar:
    """Та самая панель «Ask anything…» снизу."""

    def __init__(self, root, screen, on_submit, on_exit, on_undo, palette=PALETTE):
        self.p = p = palette
        self.screen = screen
        self.on_submit = on_submit
        self.on_exit = on_exit
        self.win, self.transparent = make_overlay(root, BAR_W, BAR_H, focusable=True)
        bg = "systemTransparent" if self.transparent else BAR_BG
        try:
            self.win.attributes("-alpha", p.bar_alpha)
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(self.win, width=BAR_W, height=BAR_H,
                                highlightthickness=0, bd=0, bg=bg)
        self.canvas.pack()
        self.glass = None
        if p.bar_glass:
            png = png_bytes(BAR_W, BAR_H, glass_pixels(BAR_W, BAR_H, 26, p.bar_bg,
                                                       p.bar_glass, p.bar_edge))
            self.glass = tk.PhotoImage(master=self.win,
                                       data=base64.b64encode(png).decode("ascii"))
            self.canvas.create_image(0, 0, image=self.glass, anchor="nw", tags="glass")
            self._glass_png = png
            # На стекле поле ввода — отдельная плотная «строка поиска»:
            # под буквами, которые она набирает, не должно быть обоев.
            round_rect(self.canvas, 18, 55, BAR_W - 18, 93, 17,
                       fill=p.bar_bg, outline=p.bar_edge, width=1)
        else:
            round_rect(self.canvas, 2, 2, BAR_W - 2, BAR_H - 2, 26,
                       fill=p.bar_bg, outline=p.bar_edge, width=1)

        # кнопка Exit
        self.exit_id = round_rect(self.canvas, 22, 18, 96, 46, 14,
                                  fill=p.bar_bg, outline=p.bar_edge, width=1.4)
        self.exit_text = self.canvas.create_text(59, 32, text="Exit",
                                                 fill=p.bar_text,
                                                 font=("Helvetica", 13))
        for item in (self.exit_id, self.exit_text):
            self.canvas.tag_bind(item, "<Button-1>", lambda e: on_exit())

        # Кнопка отката. Ширина — по тексту, а не числом: текст выезжал за
        # рамку, потому что его ширина зависит от шрифта в системе.
        self.undo_text = self.canvas.create_text(108 + BUTTON_PAD, 32, anchor="w",
                                                 text="Вернуть как было",
                                                 fill=p.bar_hint,
                                                 font=("Helvetica", 11))
        self.undo_id = round_rect(self.canvas, 108, 18, text_right(self.canvas, self.undo_text,
                                                                   214 - BUTTON_PAD) + BUTTON_PAD,
                                  46, 14, fill=p.bar_bg, outline=p.bar_edge, width=1.4)
        self.canvas.tag_lower(self.undo_id, self.undo_text)
        for item in (self.undo_id, self.undo_text):
            self.canvas.tag_bind(item, "<Button-1>", lambda e: on_undo())

        self.entry = tk.Entry(self.win, font=("Helvetica", 19), relief="flat",
                              bg=p.bar_bg, fg=p.bar_text, insertbackground=p.bar_text,
                              highlightthickness=0, bd=0)
        self.canvas.create_window(30, 74, anchor="w", window=self.entry,
                                  width=BAR_W - 120, height=34)
        self.entry.bind("<Return>", lambda e: self._submit())
        self.entry.bind("<Escape>", self._escape)
        self.entry.bind("<KeyPress>", self._key)
        self.entry.bind("<<Paste>>", lambda e: self._hide_hint())
        self.entry.bind("<KeyRelease>", lambda e: self._sync_hint())
        self.hinting = False
        self._show_hint()
        self.canvas.create_text(BAR_W - 40, 74, text="⏎", fill=p.bar_hint,
                                font=("Helvetica", 20))
        self.status = self.canvas.create_text(BAR_W - 40, 32, anchor="e", text="",
                                              fill=p.bar_status, font=("Helvetica", 12))

        # панель можно таскать мышкой
        self.canvas.bind("<Button-1>", self._drag_start, add="+")
        self.canvas.bind("<B1-Motion>", self._drag_move, add="+")
        self._drag = None

        bx, by = screen.bar_origin()
        self.win.geometry("+{}+{}".format(int(bx), int(by)))
        self.win.deiconify()
        if self.glass is not None and self.transparent:
            self._glass_on_mac()
        self.win.after(120, self.focus_entry)

    def _glass_on_mac(self):
        """Tk на Маке не рисует картинку на прозрачном фоне — стекло кладёт
        сам macOS слоем под панелью (см. macimages)."""
        layers = macimages.Layers(self.win, BAR_W, BAR_H, title="Барсик")
        if not layers.ok:
            return
        layers.begin()
        layers.show("glass", macimages.image(("glass", hash(self._glass_png)), data=self._glass_png),
                    0, 0, BAR_W, BAR_H)
        layers.commit()
        self.canvas.delete("glass")
        self.layers = layers

    def focus_entry(self):
        try:
            self.win.lift()
            self.entry.focus_force()
        except tk.TclError:
            pass

    def _escape(self, _event=None):
        """Esc стирает набранное, а на пустой строке — выходит.

        Возвращаем "break", чтобы событие не пошло дальше: иначе один Esc
        мог бы одновременно очистить строку и закрыть программу.
        """
        if self.text():
            self.entry.delete(0, "end")
            self._sync_hint()
        else:
            self.on_exit()
        return "break"

    # Подсказка «Ask anything…» живёт в самом поле, серым текстом. Раньше она
    # была надписью на canvas под полем — и её никто не видел: встроенное в
    # canvas окно (поле ввода) всегда рисуется поверх его надписей.

    def text(self):
        """Что набрано. Подсказка — не набранный текст."""
        return "" if self.hinting else self.entry.get()

    def _show_hint(self):
        try:
            self.entry.delete(0, "end")
            self.entry.insert(0, HINT)
            self.entry.config(fg=self.p.bar_hint)
            self.entry.icursor(0)
            self.hinting = True
        except tk.TclError:         # поле выключено, пока Барсик работает
            pass

    def _hide_hint(self):
        if self.hinting:
            self.entry.delete(0, "end")
            self.entry.config(fg=self.p.bar_text)
            self.hinting = False

    def _key(self, event):
        """Первая буква убирает подсказку, а стирать в подсказке нечего."""
        if not self.hinting:
            return None
        if event.keysym in ("BackSpace", "Delete"):
            return "break"
        if event.char and event.char.isprintable():
            self._hide_hint()
        return None

    def _sync_hint(self):
        if not self.hinting and not self.entry.get():
            self._show_hint()

    def _submit(self):
        text = self.text().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._sync_hint()
        self.on_submit(text)

    def set_status(self, text):
        self.canvas.itemconfigure(self.status, text=text)

    def set_enabled(self, enabled):
        try:
            self.entry.config(state="normal" if enabled else "disabled")
        except tk.TclError:
            pass
        if enabled:
            self._sync_hint()

    def origin(self):
        return self.win.winfo_x(), self.win.winfo_y()

    def _drag_start(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag_move(self, e):
        if not self._drag:
            return
        self.win.geometry("+{}+{}".format(e.x_root - self._drag[0],
                                          e.y_root - self._drag[1]))

    def destroy(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


class ConfirmBar:
    """Подтверждение прямо в панели, без системного диалога — чтобы не
    выбивать из сказки. Но подтверждение всё равно обязательное."""

    W, H = 560, 132

    def __init__(self, root, screen, title, lines, on_yes, on_no, palette=PALETTE):
        p = palette
        self.win, self.transparent = make_overlay(root, self.W, self.H, focusable=True)
        bg = "systemTransparent" if self.transparent else BAR_BG
        self.canvas = tk.Canvas(self.win, width=self.W, height=self.H,
                                highlightthickness=0, bd=0, bg=bg)
        self.canvas.pack()
        round_rect(self.canvas, 2, 2, self.W - 2, self.H - 2, 22,
                   fill=p.bar_bg, outline=p.bar_edge, width=1)
        self.canvas.create_text(28, 26, anchor="w", text=title, fill=p.bar_text,
                                font=("Helvetica", 15, "bold"))
        self.canvas.create_text(28, 56, anchor="w", text="\n".join(lines[:2]),
                                fill=p.bar_hint, font=("Helvetica", 12))

        yes = round_rect(self.canvas, self.W - 220, 86, self.W - 130, 116, 14,
                         fill=p.yes_bg, outline="", width=0)
        yes_t = self.canvas.create_text(self.W - 175, 101, text="Давай",
                                        fill=p.yes_text, font=("Helvetica", 13, "bold"))
        no = round_rect(self.canvas, self.W - 116, 86, self.W - 26, 116, 14,
                        fill=p.no_bg, outline="", width=0)
        no_t = self.canvas.create_text(self.W - 71, 101, text="Не надо",
                                       fill=p.no_text, font=("Helvetica", 13))
        for item in (yes, yes_t):
            self.canvas.tag_bind(item, "<Button-1>", lambda e: on_yes())
        for item in (no, no_t):
            self.canvas.tag_bind(item, "<Button-1>", lambda e: on_no())

        bx, by = screen.bar_origin()
        self.win.geometry("+{}+{}".format(int(bx + (BAR_W - self.W) / 2),
                                          int(by - self.H - 14)))
        self.win.deiconify()
        # "break" обязателен: иначе Esc закроет подтверждение и поедет
        # дальше по цепочке обработчиков
        self.win.bind("<Return>", lambda e: (on_yes(), "break")[1])
        self.win.bind("<Escape>", lambda e: (on_no(), "break")[1])
        self.win.after(60, self._grab_focus)

    def _grab_focus(self):
        try:
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass

    def destroy(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# Приложение
# ---------------------------------------------------------------------------


class CatApp:
    # версия 3 подставляет сюда своего кота, всё остальное общее
    brain_class = CatBrain
    window_class = CatWindow
    name = CAT_NAME
    palette = PALETTE

    def __init__(self, root, demo=False):
        self.root = root
        self.demo = demo
        self.rng = random.Random()
        self.queue = queue.Queue()
        self.icons = {}
        self.confirm = None
        self.pending = None
        self.busy = False
        self.alive = True
        self.journal = []
        self.journal_lock = threading.Lock()
        self.op_id = None
        self.op_title = ""

        root.withdraw()
        self.screen = Screen(w=root.winfo_screenwidth(), h=root.winfo_screenheight(),
                             cat_w=self.window_class.W, cat_h=self.window_class.H,
                             perch_sink=self.window_class.PERCH_SINK,
                             head_room=self.window_class.HEAD_ROOM)
        self.brain = self.brain_class(self.screen, self.rng)
        self.cat = self.window_class(root, self.screen)
        self.cat.artist.p = self.palette
        self.bubble = BubbleWindow(root, self.palette)
        self.bar = CommandBar(root, self.screen, self.submit, self.quit, self.undo,
                              self.palette)
        self.cat.bind_click(self.brain.be_petted)

        mode = "демо — файлы не трогаю" if demo else (
            "ИИ" if engine.AI_KEY else "правила")
        self.bar.set_status("{} · {}".format(self.name, mode))
        self.brain.speak("привет! я " + self.name, frames=110)

        denied = engine.check_access()
        if denied and not demo:
            self.brain.speak("не пускают в " + ", ".join(denied), frames=200)

        if not demo:
            threading.Thread(target=self._load_icons, daemon=True).start()

        self.tick()

    # --- фон ---------------------------------------------------------------

    def _load_icons(self):
        icons = read_desktop_icons()
        self.queue.put(("icons", icons))

    def _log(self, message):
        self.queue.put(("log", message))

    # --- главный цикл ------------------------------------------------------

    def tick(self):
        if not self.alive:      # выход уже нажали — кадр рисовать не на чем
            return
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "icons":
                self.icons = payload
            elif kind == "plan":
                self.ask_confirmation(payload)
            elif kind == "log":
                self.bar.set_status(str(payload)[:60])
            elif kind == "error":
                self.brain.be_confused()
                self.bar.set_status(str(payload)[:60])
                self.busy = False
                self.bar.set_enabled(True)
            elif kind == "undone":
                restored, left = payload
                if restored:
                    self.brain.speak(self.brain._phrase("undone").format(restored),
                                     frames=110)
                else:
                    self.brain.speak("отменять нечего", frames=90)
                self.bar.set_status("можно отменить ещё: {}".format(left) if left
                                    else "отменять больше нечего")
                self.busy = False
                self.bar.set_enabled(True)

        for event in self.brain.tick():
            self.handle_event(event)

        self.cat.render(self.brain)
        y, below = bubble_spot(self.brain.y, self.cat, self.screen.menubar)
        self.bubble.update(self.brain.say, self.brain.x, y, below)
        self.root.after(FRAME_MS, self.tick)

    def handle_event(self, event):
        kind = event[0]
        if kind == "apply":
            trip = event[1]
            if trip is not None and trip.step is not None:
                threading.Thread(target=self._apply_one, args=(trip.step,),
                                 daemon=True).start()
            self.bar.set_status("разобрано: {}/{}".format(
                self.brain.done_count, self.brain.total_count))
        elif kind == "apply_batch":
            steps = event[1]
            if steps and not self.demo:
                threading.Thread(target=self._apply_batch, args=(steps,),
                                 daemon=True).start()
        elif kind == "finished":
            self.busy = False
            self.bar.set_enabled(True)
            self.bar.set_status("готово: {}".format(event[1]))

    def _remember(self, entries):
        """Журнал отката пишется из рабочих потоков — под замком, иначе
        одновременный рейс и хвостовая пачка могут затереть друг друга.

        op_id держит все файлы одного захода в одной записи журнала: иначе
        каждый принесённый кошкой файл стал бы отдельной операцией, и
        «Вернуть как было» отменяло бы их по одному.
        """
        if not entries:
            return
        with self.journal_lock:
            self.journal.extend(entries)
            engine.record_operation(self.journal, self.op_title, self.op_id)

    def _apply_one(self, step):
        self._remember([e for e in [engine.apply_step(step, self._log)] if e])

    def _apply_batch(self, steps):
        entries = [engine.apply_step(step, self._log) for step in steps]
        self._remember([e for e in entries if e])

    # --- команды -----------------------------------------------------------

    def submit(self, text):
        if self.busy:
            self.brain.speak("погоди, я работаю", frames=70)
            return
        self.brain.wake()
        self.busy = True
        self.bar.set_enabled(False)
        self.bar.set_status("думаю…")

        if self.demo:
            self.brain.speak("показываю как умею", frames=90)
            self.journal = []
            self.op_id = None
            self.op_title = ""
            self.brain.start_job(demo_trips(self.screen, self.rng))
            return

        def worker():
            try:
                plan = engine.rule_plan(text)
                if plan is None:
                    if not engine.AI_KEY:
                        self.queue.put(("error", self.brain.phrases["bad_command"][0]))
                        return
                    plan = engine.ai_plan(text)
                self.queue.put(("plan", plan))
            except PermissionError:
                self.queue.put(("error", "нет доступа к папкам"))
            except Exception as exc:
                self.queue.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def ask_confirmation(self, plan):
        if plan.count_files() == 0:
            self.brain.nothing_to_do()
            self.busy = False
            self.bar.set_enabled(True)
            self.bar.set_status("и так порядок")
            return

        self.pending = plan
        bx, _ = self.screen.bar_origin()
        self.brain.step_aside(self.screen.beside_confirm(ConfirmBar.W), bx + BAR_W / 2)
        self.confirm = ConfirmBar(
            self.root, self.screen,
            "Разобрать файлов: {}?".format(plan.count_files()),
            preview_lines(plan) or ["всё удаляемое уходит в Корзину"],
            self._confirm_yes, self._confirm_no, self.palette)

    def _close_confirm(self):
        if self.confirm:
            self.confirm.destroy()
            self.confirm = None
            self.bar.focus_entry()      # вернуть курсор в строку ввода

    def _confirm_yes(self):
        self._close_confirm()
        plan, self.pending = self.pending, None
        if plan is None:
            return
        self.journal = []
        self.op_id = uuid.uuid4().hex
        self.op_title = plan.title
        mkdirs, trips, leftover = plan_to_trips(plan, self.icons, self.screen,
                                                self.rng)
        for step in mkdirs:                    # папки создаём сразу
            engine.apply_step(step, self._log)
        self.brain.start_job(trips, leftover)

    def _confirm_no(self):
        self._close_confirm()
        self.pending = None
        self.busy = False
        self.bar.set_enabled(True)
        self.brain.speak("ну не надо так не надо", frames=90)
        self.brain._go(self.screen.perch(), "home")

    def undo(self):
        if self.busy:
            return
        self.busy = True
        self.bar.set_enabled(False)
        self.bar.set_status("возвращаю…")

        def worker():
            n = engine.undo_last(self._log)
            self.queue.put(("undone", (n, engine.operations_left())))

        threading.Thread(target=worker, daemon=True).start()

    def quit(self):
        self.alive = False
        if self.confirm:
            self.confirm.destroy()
            self.confirm = None
        for w in (self.cat, self.bubble, self.bar):
            w.destroy()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# Диагностика окружения
# ---------------------------------------------------------------------------


def run_check():
    """Что умеет этот конкретный Mac. Полезно прогнать перед первым
    запуском — сразу видно, чего не хватает."""
    print("платформа:        ", sys.platform)
    print("python:           ", sys.version.split()[0])

    root = None
    try:
        root = tk.Tk()
        root.withdraw()
    except Exception as exc:
        print("Tk:                НЕ ЗАПУСКАЕТСЯ —", exc)

    if root is not None:
        print("Tk:                ", root.tk.call("info", "patchlevel"))
        print("экран:             {}x{}".format(root.winfo_screenwidth(),
                                                root.winfo_screenheight()))

        probe = tk.Toplevel(root)
        probe.withdraw()
        try:
            probe.attributes("-transparent", True)
            probe.configure(bg="systemTransparent")
            print("прозрачность:      есть")
        except tk.TclError as exc:
            print("прозрачность:      НЕТ —", exc)
        try:
            root.tk.call("::tk::unsupported::MacWindowStyle", "style", str(probe),
                         "plain", "none")
            print("окно без рамки:    есть")
        except tk.TclError as exc:
            print("окно без рамки:    НЕТ —", exc)
        probe.destroy()
        root.destroy()

    icons = read_desktop_icons()
    if icons:
        print("иконки Finder:     видно {} шт.".format(len(icons)))
    else:
        print("иконки Finder:     не видно (нужно разрешение «Автоматизация "
              "→ Finder», кошка будет ходить к случайным точкам)")

    denied = engine.check_access()
    if denied:
        print("доступ к папкам:   НЕТ к", ", ".join(denied))
        print("                   Системные настройки → Конфиденциальность "
              "и безопасность → Файлы и папки")
    else:
        print("доступ к папкам:   есть")
    key_problem = engine.ai_key_problem() if engine.AI_KEY else None
    print("ключ ИИ:           ", "нет (режим правил)" if not engine.AI_KEY
          else "задан" if key_problem is None else "ЕСТЬ, НО " + key_problem)
    if engine.AI_KEY and key_problem is None:
        problem = engine.ai_connection_problem()
        print("связь с ИИ:        ", "есть" if problem is None else "НЕТ — " + problem)
    return 0 if root is not None else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Кошка на рабочем столе")
    parser.add_argument("--demo", action="store_true",
                        help="показательный режим, файлы не трогаются")
    parser.add_argument("--check", action="store_true",
                        help="проверить окружение и выйти")
    args = parser.parse_args(argv)

    if args.check:
        return run_check()

    if sys.platform != "darwin":
        print("Версия 2 рассчитана на macOS: прозрачные окна поверх рабочего "
              "стола на других системах ведут себя иначе.")

    root = tk.Tk()
    CatApp(root, demo=args.demo)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
