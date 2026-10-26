#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Предпросмотр кошки без запуска Tk.

Отрисовка в desktop_cat.py — это набор команд canvas (овалы, полигоны,
линии). Здесь те же самые команды проигрываются в SVG, поэтому картинку
можно посмотреть на любой машине, не имея macOS и вообще экрана.

Это не скриншот, а пересъёмка теми же нотами на другом инструменте:
сплайны и шрифты считает уже SVG, поэтому от Tk возможны мелкие
отличия. Но композиция, позы и цвета — ровно те, что нарисует программа.

    python3 tools/preview.py              собрать всё в preview/
    python3 tools/preview.py --outdir X   сложить в другую папку
"""

import argparse
import random
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "versions"))

import desktop_cat as dc

STIPPLE_OPACITY = {"gray12": 0.12, "gray25": 0.25, "gray50": 0.5, "gray75": 0.75}


def _coords(args):
    """Tk принимает и плоский список чисел, и один список — нормализуем."""
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        flat = list(args[0])
    else:
        flat = list(args)
    return [(float(flat[i]), float(flat[i + 1])) for i in range(0, len(flat) - 1, 2)]


def _smooth_path(points, close=False):
    """Повторяет сглаживание Tk (-smooth true).

    Tk ведёт квадратичный B-сплайн: кривая проходит через первую и
    последнюю точки, а промежуточные работают направляющими, и стык идёт
    по серединам отрезков. В SVG это ложится один в один на команды Q.
    """
    if len(points) < 3:
        return "M " + " L ".join("{:.2f},{:.2f}".format(x, y) for x, y in points)
    d = ["M {:.2f},{:.2f}".format(*points[0])]
    for i in range(1, len(points) - 1):
        cx, cy = points[i]
        nx, ny = points[i + 1]
        mx, my = (cx + nx) / 2.0, (cy + ny) / 2.0
        d.append("Q {:.2f},{:.2f} {:.2f},{:.2f}".format(cx, cy, mx, my))
    d.append("Q {:.2f},{:.2f} {:.2f},{:.2f}".format(*points[-2], *points[-1]))
    if close:
        d.append("Z")
    return " ".join(d)


def _poly_path(points, close=False):
    d = "M " + " L ".join("{:.2f},{:.2f}".format(x, y) for x, y in points)
    return d + (" Z" if close else "")


class SvgCanvas:
    """Подделка tkinter.Canvas, которая вместо рисования копит SVG."""

    def __init__(self, width, height):
        self._w, self._h = width, height
        self.parts = []

    # --- то, что спрашивает CatArtist -----------------------------------

    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h

    def delete(self, *a, **k):
        self.parts = []

    # --- примитивы -------------------------------------------------------

    def _paint(self, kw):
        fill = kw.get("fill") or "none"
        outline = kw.get("outline")
        width = kw.get("width", 1)
        attrs = ['fill="{}"'.format(fill)]
        opacity = STIPPLE_OPACITY.get(kw.get("stipple"))
        if opacity is not None:
            attrs.append('fill-opacity="{}"'.format(opacity))
        if outline:
            attrs.append('stroke="{}" stroke-width="{}"'.format(outline, width))
        return " ".join(attrs)

    def create_oval(self, x1, y1, x2, y2, **kw):
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        rx, ry = abs(x2 - x1) / 2.0, abs(y2 - y1) / 2.0
        self.parts.append(
            '<ellipse cx="{:.2f}" cy="{:.2f}" rx="{:.2f}" ry="{:.2f}" {}/>'.format(
                cx, cy, rx, ry, self._paint(kw)))

    def create_rectangle(self, x1, y1, x2, y2, **kw):
        self.parts.append(
            '<rect x="{:.2f}" y="{:.2f}" width="{:.2f}" height="{:.2f}" {}/>'.format(
                min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1), self._paint(kw)))

    def create_polygon(self, *args, **kw):
        points = _coords(args)
        path = (_smooth_path(points, close=True) if kw.get("smooth")
                else _poly_path(points, close=True))
        self.parts.append('<path d="{}" {}/>'.format(path, self._paint(kw)))

    def create_line(self, *args, **kw):
        points = _coords(args)
        path = (_smooth_path(points) if kw.get("smooth") else _poly_path(points))
        cap = {"round": "round", "butt": "butt", "projecting": "square"}.get(
            kw.get("capstyle"), "butt")
        self.parts.append(
            '<path d="{}" fill="none" stroke="{}" stroke-width="{}" '
            'stroke-linecap="{}" stroke-linejoin="round"/>'.format(
                path, kw.get("fill", "black"), kw.get("width", 1), cap))

    def create_arc(self, x1, y1, x2, y2, **kw):
        # в кошке дуги идут только на рот — рисуем их лёгкой кривой
        cx = (x1 + x2) / 2.0
        self.parts.append(
            '<path d="M {:.2f},{:.2f} Q {:.2f},{:.2f} {:.2f},{:.2f}" fill="none" '
            'stroke="{}" stroke-width="{}" stroke-linecap="round"/>'.format(
                x1, y1, cx, y2, x2, y1,
                kw.get("outline", "black"), kw.get("width", 1)))

    def create_text(self, x, y, **kw):
        font = kw.get("font") or ("Helvetica", 12)
        size = font[1] if len(font) > 1 else 12
        bold = len(font) > 2 and "bold" in str(font[2])
        anchor = {"w": "start", "e": "end"}.get(kw.get("anchor"), "middle")
        self.parts.append(
            '<text x="{:.2f}" y="{:.2f}" font-family="Helvetica, Arial" '
            'font-size="{}" font-weight="{}" fill="{}" text-anchor="{}" '
            'dominant-baseline="central">{}</text>'.format(
                x, y, size, "bold" if bold else "normal",
                kw.get("fill", "black"), anchor, escape(str(kw.get("text", "")))))

    # --- вывод ------------------------------------------------------------

    def svg_body(self, dx=0, dy=0):
        inner = "\n".join("    " + p for p in self.parts)
        if dx or dy:
            return '  <g transform="translate({},{})">\n{}\n  </g>'.format(dx, dy, inner)
        return inner


def wrap_svg(width, height, body, background=None):
    bg = ('  <rect width="100%" height="100%" fill="{}"/>\n'.format(background)
          if background else "")
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            'viewBox="0 0 {w} {h}">\n{bg}{body}\n</svg>\n').format(
                w=width, h=height, bg=bg, body=body)


def make_brain(state, frame=6, facing=1, carrying=None, phase=1.1, t=30):
    brain = dc.CatBrain(dc.Screen(), random.Random(4))
    brain.state = state
    brain.frame = frame
    brain.t = t
    brain.phase = phase
    brain.facing = facing
    brain.carrying = carrying
    return brain


POSES = [
    ("сидит и ждёт", dc.SIT, {}),
    ("идёт", dc.WALK, {"phase": 1.1}),
    ("несёт файл", dc.CARRY, {"carrying": "файл.txt", "phase": 2.3}),
    ("хватает файл", dc.PICK, {"frame": 9}),
    ("толкает в папку", dc.DROP, {"frame": 9, "carrying": "файл.txt"}),
    ("спит", dc.SLEEP, {"t": 40}),
    ("радуется", dc.HAPPY, {"frame": 7}),
    ("её гладят", dc.PET, {"frame": 9}),
]


def render_pose(state, opts):
    canvas = SvgCanvas(dc.CAT_W, dc.CAT_H)
    dc.CatArtist(canvas).draw(make_brain(state, **opts), dc.CAT_W * 0.5, dc.FOOT_Y)
    return canvas


def sheet(outdir):
    """Все позы одной простынёй."""
    cols, cell_w, cell_h, label_h = 4, dc.CAT_W, dc.CAT_H, 28
    rows = (len(POSES) + cols - 1) // cols
    width, height = cols * cell_w, rows * (cell_h + label_h)

    parts = []
    for i, (title, state, opts) in enumerate(POSES):
        cx, cy = (i % cols) * cell_w, (i // cols) * (cell_h + label_h)
        parts.append('  <rect x="{}" y="{}" width="{}" height="{}" fill="{}" '
                     'stroke="#e6ddcd"/>'.format(cx, cy, cell_w, cell_h + label_h,
                                                 "#fdf8f1" if i % 2 else "#f7efe4"))
        parts.append(render_pose(state, opts).svg_body(cx, cy))
        parts.append('  <text x="{}" y="{}" font-family="Helvetica, Arial" '
                     'font-size="13" fill="#6b5b4b" text-anchor="middle">{}</text>'
                     .format(cx + cell_w / 2, cy + cell_h + 17, escape(title)))

    path = outdir / "позы.svg"
    path.write_text(wrap_svg(width, height, "\n".join(parts), "#fdf8f1"),
                    encoding="utf-8")
    return path


def scene(outdir):
    """Как это выглядит на рабочем столе целиком: обои, панель, кошка."""
    screen = dc.Screen(w=1280, h=800)
    parts = []

    # обои в клетку, как в тех рилсах
    step = 80
    for x in range(0, screen.w, step):
        for y in range(0, screen.h, step):
            shade = "#f7e2e6" if (x // step + y // step) % 2 else "#fbeff1"
            parts.append('  <rect x="{}" y="{}" width="{}" height="{}" fill="{}"/>'
                         .format(x, y, step, step, shade))

    # пара иконок на столе, чтобы был масштаб
    for ix, iy, name in ((150, 140, "Снимок экрана.png"), (330, 140, "отчёт.pdf")):
        parts.append('  <rect x="{}" y="{}" width="54" height="66" rx="6" '
                     'fill="#ffffff" stroke="#d8d2cc"/>'.format(ix, iy))
        parts.append('  <text x="{}" y="{}" font-family="Helvetica, Arial" '
                     'font-size="11" fill="#5d5550" text-anchor="middle">{}</text>'
                     .format(ix + 27, iy + 80, escape(name)))

    # панель «Ask anything…»
    bar = SvgCanvas(dc.BAR_W, dc.BAR_H)
    dc.round_rect(bar, 2, 2, dc.BAR_W - 2, dc.BAR_H - 2, 26,
                  fill=dc.BAR_BG, outline=dc.BAR_EDGE, width=1)
    dc.round_rect(bar, 22, 18, 96, 46, 14, fill=dc.BAR_BG,
                  outline=dc.BAR_EDGE, width=1.4)
    bar.create_text(59, 32, text="Exit", fill=dc.BAR_TEXT, font=("Helvetica", 13))
    dc.round_rect(bar, 108, 18, 214, 46, 14, fill=dc.BAR_BG,
                  outline=dc.BAR_EDGE, width=1.4)
    bar.create_text(161, 32, text="Вернуть как было", fill=dc.BAR_HINT,
                    font=("Helvetica", 11))
    bar.create_text(34, 74, text="Ask anything…", fill=dc.BAR_HINT,
                    font=("Helvetica", 19), anchor="w")
    bar.create_text(dc.BAR_W - 40, 74, text="⏎", fill=dc.BAR_HINT,
                    font=("Helvetica", 20))
    bar.create_text(dc.BAR_W - 40, 32, text="Мурка · режим правил",
                    fill=dc.BAR_HINT, font=("Helvetica", 12), anchor="e")
    bx, by = screen.bar_origin()
    parts.append(bar.svg_body(bx, by))

    # кошка сидит на панели
    px, py = screen.perch()
    parts.append(render_pose(dc.SIT, {"facing": -1}).svg_body(
        px - dc.CAT_W * 0.5, py - dc.FOOT_Y))

    # облачко с репликой
    bubble = SvgCanvas(dc.BubbleWindow.W, dc.BubbleWindow.H)
    text = "жду команду"
    w = min(dc.BubbleWindow.W - 16, max(90, 16 + len(text) * 9))
    x0 = (dc.BubbleWindow.W - w) / 2
    dc.round_rect(bubble, x0, 6, x0 + w, 44, 16, fill=dc.BUBBLE_BG,
                  outline=dc.BUBBLE_INK, width=2)
    bubble.create_polygon(dc.BubbleWindow.W / 2 - 8, 42, dc.BubbleWindow.W / 2 + 8, 42,
                          dc.BubbleWindow.W / 2 - 2, 56,
                          fill=dc.BUBBLE_BG, outline=dc.BUBBLE_INK, width=2)
    bubble.create_text(dc.BubbleWindow.W / 2, 25, text=text, fill=dc.BUBBLE_INK,
                       font=("Helvetica", 14))
    parts.append(bubble.svg_body(px - dc.BubbleWindow.W / 2, py - dc.CAT_H - 46))

    # док внизу
    parts.append('  <rect x="{}" y="{}" width="{}" height="62" rx="18" '
                 'fill="#ffffff" fill-opacity="0.55" stroke="#e6d9dc"/>'
                 .format(screen.w / 2 - 210, screen.h - 74, 420))

    path = outdir / "рабочий-стол.svg"
    path.write_text(wrap_svg(screen.w, screen.h, "\n".join(parts)), encoding="utf-8")
    return path


def walk_strip(outdir):
    """Кадры походки подряд — видно, как переставляются лапы."""
    frames, cell = 6, dc.CAT_W
    parts = []
    for i in range(frames):
        canvas = render_pose(dc.WALK, {"phase": 0.5 + i * 0.52, "t": 10 + i * 7})
        parts.append('  <rect x="{}" y="0" width="{}" height="{}" fill="{}"/>'
                     .format(i * cell, cell, dc.CAT_H,
                             "#fdf8f1" if i % 2 else "#f7efe4"))
        parts.append(canvas.svg_body(i * cell, 0))
    path = outdir / "походка.svg"
    path.write_text(wrap_svg(frames * cell, dc.CAT_H, "\n".join(parts)),
                    encoding="utf-8")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Предпросмотр кошки в SVG")
    parser.add_argument("--outdir", default="preview")
    args = parser.parse_args(argv)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for path in (sheet(outdir), walk_strip(outdir), scene(outdir)):
        print("готово:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
