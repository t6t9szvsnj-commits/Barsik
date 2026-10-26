#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автотесты для desktop_barsik.py (версия 3).

Мозг, окна и движок у версии 3 общие с версией 2 и проверяются там. Здесь —
то, что у Барсика своё: какой кадр показывается в каждом состоянии, что лапы не
скользят по столу, что зеркальный кадр и мелочи (бумажка, папка, сердечки)
не вылезают за окно, что кадры в репозитории на месте и что он говорит о
себе в мужском роде.

Что отсюда проверить нельзя: как кадры выглядят на настоящем столе — это
только глазами на Маке.
"""

import io
import json
import math
import os
import random
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

# все три версии лежат в папке versions/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "versions"))

import desktop_cat as dc
import desktop_barsik as dr
from tests.test_desktop_cat import FakeCanvas, coords

ALL_STATES = [dc.SIT, dc.SLEEP, dc.WALK, dc.CARRY, dc.PICK, dc.DROP,
              dc.SWEEP, dc.HAPPY, dc.CONFUSED, dc.PET]


def tiny_png(w, h):
    """Настоящий PNG нужного размера — для проверки чтения заголовка."""
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def fake_meta(size=(250, 150), anchor=(110, 140)):
    counts = {"walk": 24, "stand": 12, "sit": 12, "blink": 1, "sleep": 8,
              "pick": 8, "hop": 8}
    info = {"mouth": [200, 70], "crown": [180, 8]}
    return {"size": list(size), "anchor": list(anchor), "cycle_px": 46.4,
            "anims": {a: [dict(info) for _ in range(n)] for a, n in counts.items()}}


def write_sprites(folder, meta, size_override=None):
    with open(Path(folder) / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    w, h = size_override or meta["size"]
    for anim, frames in meta["anims"].items():
        for i in range(len(frames)):
            (Path(folder) / dr.frame_name(anim, i)).write_bytes(tiny_png(w, h))


class ImageCanvas(FakeCanvas):
    """Поддельный canvas, который умеет ещё и картинки."""

    create_image = FakeCanvas._record


def make_brain(meta, state=dc.SIT, facing=1, carrying=None, frame=5):
    brain = dr.brain_for(meta)(dc.Screen(w=1440, h=900), random.Random(3))
    brain.state = state
    brain.facing = facing
    brain.carrying = carrying
    brain.frame = frame
    brain.t = 40
    brain.phase = 1.0
    return brain


# ---------------------------------------------------------------------------
# Характер
# ---------------------------------------------------------------------------

class TestBarsikIsHim(unittest.TestCase):
    def test_default_name_is_barsik(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CAT_NAME", None)
            import importlib
            self.assertEqual(importlib.reload(dr).CAT_NAME, "Барсик")

    def test_inside_the_app_bundle_frames_are_looked_up_where_pyinstaller_put_them(self):
        import importlib
        try:
            with mock.patch.object(sys, "_MEIPASS", "/Applications/Барсик.app/Contents/Frameworks",
                                   create=True):
                self.assertEqual(importlib.reload(dr).SPRITES,
                                 Path("/Applications/Барсик.app/Contents/Frameworks/sprites/barsik"))
        finally:
            importlib.reload(dr)
        self.assertEqual(dr.SPRITES, Path(dr.__file__).resolve().parent / "sprites" / "barsik")

    def test_barsik_speaks_about_himself_in_masculine(self):
        # Барсик — кот. Во второй версии кошка «не поняла» и «вернула», и эти
        # фразы по наследству достались бы и ему.
        every = " ".join(p for kind in dr.PHRASES.values() for p in kind)
        for feminine in ("поняла", "вернула", "сделала", "устала"):
            self.assertNotIn(feminine, every)
        self.assertIn("не понял", dr.PHRASES["confused"])
        self.assertEqual(dr.PHRASES["undone"][0].format(3), "вернул 3 шт.")

    def test_version_two_keeps_her_own_phrases(self):
        self.assertIn("не поняла", dc.PHRASES["confused"])
        self.assertIs(dc.CatBrain.phrases, dc.PHRASES)

    def test_brain_uses_barsik_phrases(self):
        brain = make_brain(fake_meta())
        brain.be_confused()
        self.assertIn(brain.say, dr.PHRASES["confused"])


# ---------------------------------------------------------------------------
# Цвета
# ---------------------------------------------------------------------------

def contrast(fg, bg):
    """Контраст по WCAG: 4.5 — порог для обычного текста, 3 — для крупного."""
    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
        return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]
    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class TestBarsikColors(unittest.TestCase):
    def test_light_blue_is_the_main_colour(self):
        p = dr.PALETTE
        self.assertEqual(p.bar_bg, dr.BLUE)
        self.assertEqual(dr.BarsikApp.palette, p)

    def test_all_five_colours_are_used(self):
        used = set(vars(dr.PALETTE).values())
        for colour in (dr.BLUE, dr.BLACK, dr.WINE, dr.MILK, dr.GREEN):
            self.assertIn(colour, used)

    def test_every_text_is_readable_on_its_background(self):
        p = dr.PALETTE
        for fg, bg, need, what in (
                (p.bar_text, p.bar_bg, 4.5, "текст в панели"),
                (p.bar_hint, p.bar_bg, 4.5, "подсказка и «Вернуть как было»"),
                (p.bar_status, p.bar_bg, 4.5, "строка состояния"),
                (p.yes_text, p.yes_bg, 4.5, "«Давай»"),
                (p.no_text, p.no_bg, 4.5, "«Не надо»"),
                (p.bubble_ink, p.bubble_bg, 4.5, "облачко"),
                (p.ink, p.bar_bg, 3, "вопрос и «z z z»")):
            self.assertGreaterEqual(contrast(fg, bg), need, what)

    def test_confirmation_buttons_are_drawn_in_their_own_colours(self):
        # «Не надо» рисовался цветом текста панели, а не no_text: у Барсика это
        # чёрный на винном — почти не читается, хотя палитра была верной
        class Canvas(ImageCanvas):
            def __init__(self, *a, **k):
                super().__init__()

            def pack(self, *a, **k):
                pass

            tag_bind = bind = create_window = pack

        with mock.patch.object(dc, "make_overlay", return_value=(mock.Mock(), True)), \
                mock.patch.object(dc.tk, "Canvas", Canvas):
            bar = dc.ConfirmBar(None, dc.Screen(), "Разобрать?", ["a"], None, None,
                                dr.PALETTE)
        fills = {kw.get("text"): kw.get("fill") for _, kw in bar.canvas.items}
        self.assertEqual(fills["Давай"], dr.PALETTE.yes_text)
        self.assertEqual(fills["Не надо"], dr.PALETTE.no_text)

    def test_barsiks_bar_is_frosted_glass_and_the_letters_stay_solid(self):
        # просили прозрачнее; -alpha делал прозрачными и буквы, поэтому
        # прозрачный теперь только фон, а окно целиком — нет
        self.assertEqual(dr.PALETTE.bar_alpha, 1.0)
        self.assertTrue(0.5 <= dr.PALETTE.bar_glass <= 0.8)

    def test_text_on_the_glass_reads_on_any_wallpaper(self):
        p = dr.PALETTE
        tint = dc.hex_rgb(p.bar_bg)

        def through_glass(wallpaper):
            return "#%02x%02x%02x" % tuple(
                round(t * p.bar_glass + w * (1 - p.bar_glass)) for t, w in zip(tint, wallpaper))
        for wallpaper, need_status in (((0, 0, 0), 4.0), ((255, 255, 255), 4.5),
                                       ((40, 40, 90), 4.5), ((200, 90, 60), 4.5)):
            behind = through_glass(wallpaper)
            self.assertGreaterEqual(contrast(p.bar_text, behind), 4.5, wallpaper)
            self.assertGreaterEqual(contrast(p.bar_status, behind), need_status, wallpaper)

    def test_glass_has_clear_corners_a_solid_edge_and_see_through_middle(self):
        w, h = 120, 60
        rows = dc.glass_pixels(w, h, 20, "#bfe2f4", 0.65, "#12301f")
        self.assertEqual(len(rows), h)
        self.assertTrue(all(len(r) == w * 4 for r in rows))
        alpha = lambda x, y: rows[y][x * 4 + 3]   # noqa: E731
        self.assertEqual(alpha(0, 0), 0)                            # угол прозрачный
        self.assertEqual(alpha(w - 1, h - 1), 0)
        self.assertAlmostEqual(alpha(w // 2, h - 12) / 255, 0.65, delta=0.02)   # стекло
        self.assertGreater(alpha(w // 2, h - 1), 200)               # рамка плотная
        self.assertGreater(alpha(w // 2, 8), alpha(w // 2, h - 12))  # блик сверху

    def test_glass_picture_is_a_real_png(self):
        rows = dc.glass_pixels(40, 20, 8, "#bfe2f4", 0.65, "#12301f")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "glass.png"
            path.write_bytes(dc.png_bytes(40, 20, rows))
            self.assertEqual(dr.png_size(path), (40, 20))
            raw = zlib.decompress(path.read_bytes()[33 + 8:-16])   # данные IDAT без crc и IEND
            self.assertEqual(len(raw), 20 * (1 + 40 * 4))

    def test_murka_keeps_her_own_colours(self):
        self.assertEqual(dc.CatApp.palette, dc.PALETTE)
        self.assertEqual(dc.PALETTE.bar_bg, dc.BAR_BG)
        self.assertEqual(dc.PALETTE.bar_glass, 0.0)      # у Мурки панель без стекла


# ---------------------------------------------------------------------------
# Кадры
# ---------------------------------------------------------------------------

class TestChooseFrame(unittest.TestCase):
    def setUp(self):
        self.meta = fake_meta()

    def test_every_state_maps_to_an_existing_frame(self):
        for state in ALL_STATES:
            brain = make_brain(self.meta, state)
            for frame in range(0, 80):
                brain.frame = brain.t = frame
                brain.phase = frame * 0.4
                anim, i = dr.choose_frame(brain, self.meta)
                self.assertIn(anim, dr.ANIMS)
                self.assertLess(i, len(self.meta["anims"][anim]),
                                "{}: кадр {} за пределами позы {}".format(state, i, anim))
                self.assertGreaterEqual(i, 0)

    def test_walk_frame_follows_the_step_phase(self):
        brain = make_brain(self.meta, dc.WALK)
        seen = []
        for k in range(24):
            brain.phase = 2 * math.pi * k / 24 + 0.01
            seen.append(dr.choose_frame(brain, self.meta))
        self.assertEqual(seen, [("walk", k) for k in range(24)])

    def test_carry_uses_walk_frames_too(self):
        brain = make_brain(self.meta, dc.CARRY, carrying="a.txt")
        self.assertEqual(dr.choose_frame(brain, self.meta)[0], "walk")

    def test_pick_reaches_the_last_frame_exactly_when_it_ends(self):
        brain = make_brain(self.meta, dc.PICK)
        brain.frame = 0
        self.assertEqual(dr.choose_frame(brain, self.meta), ("pick", 0))
        brain.frame = dc.PICK_FRAMES
        self.assertEqual(dr.choose_frame(brain, self.meta), ("pick", 7))

    def test_sitting_barsik_blinks_now_and_then(self):
        brain = make_brain(self.meta, dc.SIT)
        anims = set()
        for t in range(300):
            brain.t = t
            anims.add(dr.choose_frame(brain, self.meta)[0])
        self.assertEqual(anims, {"sit", "blink"})

    def test_petting_closes_his_eyes(self):
        brain = make_brain(self.meta, dc.PET)
        self.assertEqual(dr.choose_frame(brain, self.meta), ("blink", 0))


class TestPaws(unittest.TestCase):
    def test_paws_do_not_slide_on_the_desktop(self):
        """За один цикл шага кот должен сдвинуться ровно на столько, на
        сколько в кадрах уезжает назад опорная лапа. Иначе он «буксует» или
        едет на месте, перебирая лапами."""
        meta = fake_meta()
        cls = dr.brain_for(meta)
        travelled = 2 * math.pi / cls.phase_step * cls.walk_speed
        self.assertAlmostEqual(travelled, meta["cycle_px"], places=6)

    def test_quick_trips_speed_up_the_legs_as_well(self):
        meta = fake_meta()
        brain = make_brain(meta, dc.WALK)
        brain.x, brain.y = 300, 400
        before = brain.phase
        brain._move_toward((1200, 400), brain.walk_speed * dc.SPEED_RAMP)
        self.assertAlmostEqual(brain.phase - before,
                               brain.phase_step * dc.SPEED_RAMP, places=6)

    def test_version_two_walk_is_unchanged(self):
        brain = dc.CatBrain(dc.Screen(), random.Random(1))
        self.assertEqual(brain.walk_speed, dc.WALK_SPEED)
        self.assertEqual(brain.phase_step, dc.PHASE_STEP)


# ---------------------------------------------------------------------------
# Окно и рисование
# ---------------------------------------------------------------------------

class TestWindowAndArtist(unittest.TestCase):
    def setUp(self):
        self.meta = fake_meta()
        self.w, self.h, self.foot = dr.window_size(self.meta)
        self.canvas = ImageCanvas(self.w, self.h)
        self.artist = dr.BarsikArtist(self.canvas, self.meta,
                                     lambda anim, i, f: "{}{}{}".format(anim, i, f))

    def test_anchor_is_in_the_middle_of_the_window(self):
        # версия 2 ставит окно так, что x кота — середина окна
        sw, sh = self.meta["size"]
        self.assertGreaterEqual(self.w, sw)
        self.assertEqual(self.h, dr.TOP_PAD + sh)
        self.assertEqual(self.foot, dr.TOP_PAD + self.meta["anchor"][1])

    def _draw_all(self, check):
        for state in ALL_STATES:
            for facing in (1, -1):
                brain = make_brain(self.meta, state, facing, carrying="файл.txt")
                for frame in range(0, 70):
                    self.canvas.delete()
                    brain.frame = brain.t = frame
                    brain.phase = frame * 0.4
                    self.artist.draw(brain, self.w * 0.5, self.foot)
                    check(state, facing, brain)

    def test_barsik_sits_on_the_bar_edge_instead_of_behind_it(self):
        # Мурка свешивает лапы за край панели на 8 пикселей. У Барсика в кадре
        # лапы и обвитый хвост стоят на полу — так панель срезала их.
        window = dr.window_for(self.meta, lambda *a: None)
        self.assertEqual(window.PERCH_SINK, 0)
        self.assertEqual(dc.CatWindow.PERCH_SINK, 8)
        screen = dc.Screen(perch_sink=window.PERCH_SINK, cat_h=window.H)
        _, by = screen.bar_origin()
        self.assertEqual(screen.perch()[1], by)

    def test_mirrored_frame_is_placed_so_the_paws_stay_put(self):
        sw, _ = self.meta["size"]
        ax, ay = self.meta["anchor"]

        def check(state, facing, brain):
            (x, y), kw = self.canvas.items[0]
            self.assertTrue(kw["image"].endswith(str(facing)))
            anchor_x = x + (ax if facing > 0 else sw - ax)
            self.assertAlmostEqual(anchor_x, self.w * 0.5)
            self.assertAlmostEqual(y + ay, self.foot)
        self._draw_all(check)

    def test_nothing_is_drawn_outside_the_window(self):
        """Всё, что вылезет за окно, на столе просто обрежется: бумажка без
        края, сердечки без верхушек. Проверяем все позы в обе стороны."""
        sw, sh = self.meta["size"]
        worst = {}

        def check(state, facing, brain):
            for args, kw in self.canvas.items:
                if "image" in kw:
                    xs, ys = [args[0], args[0] + sw], [args[1], args[1] + sh]
                else:
                    nums = coords(args)
                    xs, ys = nums[0::2], nums[1::2]
                for x in xs:
                    worst[state] = min(worst.get(state, 0), x, self.w - x)
                for y in ys:
                    worst[state] = min(worst.get(state, 0), y, self.h - y)
        self._draw_all(check)
        overflowing = {s: round(v, 1) for s, v in worst.items() if v < -0.01}
        self.assertEqual(overflowing, {}, "вылезают за окно {}x{}: {}".format(
            self.w, self.h, overflowing))

    def test_paper_is_drawn_only_while_carrying(self):
        brain = make_brain(self.meta, dc.CARRY, carrying="файл.txt")
        self.artist.draw(brain, self.w * 0.5, self.foot)
        with_paper = len(self.canvas.items)
        self.canvas.delete()
        brain.carrying = None
        self.artist.draw(brain, self.w * 0.5, self.foot)
        self.assertEqual(len(self.canvas.items), 1)       # только сам Барсик
        self.assertGreater(with_paper, 1)


# ---------------------------------------------------------------------------
# Гортензия
# ---------------------------------------------------------------------------

FLOWER = {"carry": {"size": [59, 64], "grip": [4, 4]},
          "gift": {"size": [99, 63], "base": [41, 59]}}


def gift_brain(flower=FLOWER, seed=3, x=None):
    brain = dr.brain_for(fake_meta(), flower)(dc.Screen(w=1440, h=900), random.Random(seed))
    if x is not None:
        brain.x = x
    return brain


def run(brain, frames, until=None):
    """Крутит мозг и собирает события; until(brain, events) — остановиться."""
    seen = []
    for _ in range(frames):
        events = brain.tick()
        seen += events
        if until and until(brain, events):
            break
    return seen


class TestGiftChance(unittest.TestCase):
    def test_chance_grows_by_five_percent_every_ninety_seconds(self):
        every = dr.GIFT_EVERY_MS / dc.FRAME_MS
        self.assertEqual(every, 2250)
        chance = dr.GiftChance(mock.Mock(random=lambda: 0.99), every)
        for roll in range(1, 5):
            for _ in range(2249):
                self.assertFalse(chance.tick())
            chance.tick()
            self.assertAlmostEqual(chance.chance, 0.05 * roll)

    def test_after_a_gift_the_chance_starts_from_zero(self):
        rolls = iter([0.99, 0.99, 0.01, 0.99])
        chance = dr.GiftChance(mock.Mock(random=lambda: next(rolls)), every=1)
        self.assertEqual([chance.tick() for _ in range(4)], [False, False, True, False])
        self.assertAlmostEqual(chance.chance, 0.05)

    def test_sooner_or_later_he_always_brings_it(self):
        chance = dr.GiftChance(mock.Mock(random=lambda: 0.999999), every=1)
        self.assertIn(True, [chance.tick() for _ in range(20)])

    def test_on_average_a_flower_every_eight_idle_minutes(self):
        # в среднем раз в 8 минут простоя, и это же написано в README
        minutes = dr.GIFT_EVERY_MS / 60000
        rng = random.Random(1)
        chance = dr.GiftChance(rng, every=1)
        waits, n = [], 0
        while len(waits) < 20000:
            n += 1
            if chance.tick():
                waits.append(n * minutes)
                n = 0
        self.assertAlmostEqual(sum(waits) / len(waits), 8, delta=0.3)
        self.assertLessEqual(max(waits), 30)       # README: не позже чем через полчаса



class TestHydrangea(unittest.TestCase):
    def test_idle_barsik_goes_for_a_flower_when_the_dice_say_so(self):
        brain = gift_brain()
        brain.gift_chance = dr.GiftChance(mock.Mock(random=lambda: 0.0), every=5)
        run(brain, 6)
        self.assertEqual(brain.purpose, "fetch")

    def test_the_clock_stands_while_he_works_or_the_app_waits(self):
        brain = gift_brain()
        brain.gift_chance = dr.GiftChance(mock.Mock(random=lambda: 0.0), every=5)
        brain.hold = True                   # думает над командой или ждёт «да»
        run(brain, 50)
        self.assertEqual(brain.gift_chance.clock, 0)
        brain.hold = False
        brain.trips = [object()]            # в работе
        brain.state = dc.WALK
        brain.target = (brain.x, brain.y)
        brain.tick()
        self.assertEqual(brain.gift_chance.clock, 0)

    def test_without_flower_pictures_he_never_goes(self):
        brain = gift_brain(flower=None)
        brain.gift_due = True
        run(brain, 200)
        self.assertNotEqual(brain.purpose, "fetch")

    def test_whole_trip_off_screen_back_with_flower_and_lays_it_down(self):
        brain = gift_brain()
        brain.gift_due = True
        states, went_off, events = set(), False, []
        for _ in range(3000):
            events += brain.tick()
            states.add(brain.state)
            went_off = went_off or not 0 <= brain.x <= brain.screen.w
            if brain.state == dc.CARRY:
                self.assertEqual(brain.carrying, dr.FLOWER)
            if brain.state == dc.SIT and events:
                break
        self.assertTrue(went_off, "за цветком он уходит за край экрана")
        self.assertTrue({dr.AWAY, dc.CARRY, dr.PRESENT, dc.PET} <= states, states)
        gifts = [e for e in events if e[0] == "gift"]
        self.assertEqual(len(gifts), 1)
        x, y, facing = gifts[0][1]
        self.assertEqual((x - brain.x) * facing, dr.FLOWER_AHEAD)
        self.assertEqual((brain.x, brain.y), brain.screen.perch())
        self.assertIsNone(brain.carrying)
        self.assertIn(brain.say, dr.PHRASES["gift"] + dr.PHRASES["thanks"] + [""])

    def _carrying_brain(self):
        brain = gift_brain()
        brain.gift_due = True
        run(brain, 3000, until=lambda b, e: b.state == dc.CARRY and 0 < b.x < b.screen.w)
        self.assertEqual(brain.carrying, dr.FLOWER)
        return brain

    def test_called_to_work_while_carrying_he_puts_the_flower_down_first(self):
        # команда пришла, пока он несёт цветок: иначе он пошёл бы по
        # файлам с гортензией в зубах, а потом «положил» бы её вместо файла
        brain = self._carrying_brain()
        brain.step_aside(brain.screen.perch(), 0)
        events = brain.tick()
        self.assertEqual([e[0] for e in events], ["gift"])
        self.assertIsNone(brain.carrying)
        self.assertEqual(brain.purpose, "aside")

    def test_a_job_or_confusion_also_frees_his_mouth(self):
        for interrupt in (lambda b: b.start_job([]), lambda b: b.be_confused()):
            brain = self._carrying_brain()
            interrupt(brain)
            self.assertIsNone(brain.carrying)
            self.assertEqual([e[0] for e in brain.tick()], ["gift"])

    def test_interrupted_behind_the_edge_he_comes_back_instead_of_sitting_there(self):
        brain = gift_brain()
        brain.gift_due = True
        run(brain, 3000, until=lambda b, e: b.state == dr.AWAY)
        brain.be_confused()                 # ошибка от ИИ, пока он за краем
        run(brain, 3000, until=lambda b, e: b.state == dc.SIT and 0 <= b.x <= b.screen.w)
        self.assertTrue(0 <= brain.x <= brain.screen.w)
        self.assertIsNone(brain.carrying)

    def test_he_is_not_distracted_by_petting_while_away(self):
        brain = gift_brain()
        brain.gift_due = True
        run(brain, 3000, until=lambda b, e: b.state == dr.AWAY)
        brain.be_petted()
        self.assertEqual(brain.state, dr.AWAY)

    def test_flower_in_mouth_stays_inside_the_window_on_every_frame(self):
        meta = dr.load_meta()
        flower = dr.load_flower()
        w, h, foot = dr.window_size(meta)
        canvas = ImageCanvas(w, h)
        artist = dr.BarsikArtist(canvas, meta, lambda *a: "img", flower)
        cw, ch = flower["carry"]["size"]
        drawn = 0
        for state, n in ((dc.CARRY, 60), (dr.PRESENT, dr.PRESENT_FRAMES)):
            for facing in (1, -1):
                brain = make_brain(meta, state, facing, carrying=dr.FLOWER)
                for frame in range(n):
                    canvas.delete()
                    brain.frame = brain.t = frame
                    brain.phase = frame * 0.4
                    artist.draw(brain, w * 0.5, foot)
                    for (x, y), kw in canvas.items[1:]:
                        drawn += 1
                        self.assertTrue(0 <= x and x + cw <= w, (state, facing, frame, x))
                        self.assertTrue(0 <= y and y + ch <= foot + 1, (state, facing, frame, y))
        self.assertGreater(drawn, 0)

    def test_lying_flower_is_on_screen_and_its_stem_points_to_barsik(self):
        screen = dc.Screen(w=1440, h=900)
        w, h = FLOWER["gift"]["size"]
        bx, by = FLOWER["gift"]["base"]
        left, top = dr.flower_spot(700, 600, -1, FLOWER, screen)
        self.assertEqual((left + bx, top + by), (700, 600))
        left, top = dr.flower_spot(700, 600, 1, FLOWER, screen)
        self.assertEqual((left + w - bx, top + by), (700, 600))
        for x, y in ((-50, 600), (1500, 600), (700, 0), (700, 2000)):
            left, top = dr.flower_spot(x, y, 1, FLOWER, screen)
            self.assertTrue(0 <= left <= screen.w - w and screen.menubar <= top <= screen.h - h)

    def test_flower_pictures_in_the_repository_match_their_description(self):
        flower = dr.load_flower()
        self.assertIsNotNone(flower)

    def test_missing_or_wrong_flower_pictures_just_turn_gifts_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(dr.load_flower(tmp))
            with open(Path(tmp) / "flower.json", "w") as f:
                json.dump(FLOWER, f)
            (Path(tmp) / "flower_carry.png").write_bytes(tiny_png(59, 64))
            (Path(tmp) / "flower_gift.png").write_bytes(tiny_png(10, 10))
            self.assertIsNone(dr.load_flower(tmp))
            (Path(tmp) / "flower_gift.png").write_bytes(tiny_png(99, 63))
            self.assertEqual(dr.load_flower(tmp), FLOWER)

    def test_gift_phrases_are_his_not_hers(self):
        for kind in ("fetch", "gift", "thanks"):
            for phrase in dr.PHRASES[kind]:
                self.assertNotRegex(phrase, r"(ла|ась)\b", phrase)


# ---------------------------------------------------------------------------
# Кадры на диске
# ---------------------------------------------------------------------------

class TestSpriteFiles(unittest.TestCase):
    def test_png_size_reads_the_header(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.png"
            p.write_bytes(tiny_png(7, 5))
            self.assertEqual(dr.png_size(p), (7, 5))
            p.write_bytes(b"not a png at all, just text......")
            self.assertIsNone(dr.png_size(p))

    def test_complete_set_has_no_problems(self):
        with tempfile.TemporaryDirectory() as d:
            write_sprites(d, fake_meta((20, 10), (8, 9)))
            self.assertEqual(dr.sprite_problems(d), [])

    def test_missing_meta_is_reported_in_plain_words(self):
        with tempfile.TemporaryDirectory() as d:
            problems = dr.sprite_problems(d)
        self.assertEqual(len(problems), 1)
        self.assertIn("нет описания кадров", problems[0])

    def test_missing_frame_and_wrong_size_are_reported(self):
        with tempfile.TemporaryDirectory() as d:
            meta = fake_meta((20, 10), (8, 9))
            write_sprites(d, meta)
            (Path(d) / "walk_03.png").unlink()
            (Path(d) / "sit_00.png").write_bytes(tiny_png(21, 10))
            problems = " | ".join(dr.sprite_problems(d))
        self.assertIn("нет кадра walk_03.png", problems)
        self.assertIn("sit_00.png размером 21x10", problems)

    def test_corrupt_meta_counts_as_missing(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "meta.json").write_text("{ не json", encoding="utf-8")
            self.assertIsNone(dr.load_meta(d))

    def test_frames_in_the_repository_are_complete(self):
        """Кадры Барсика лежат в репозитории — без них программа не запустится.
        Этот тест и ловит «забыл закоммитить кадры»."""
        self.assertEqual(dr.sprite_problems(), [])

    def test_speech_bubble_does_not_cover_hearts_question_or_zzz(self):
        """Облачко стояло в верхней полосе окна, где рисуются сердечки, и
        когда Барсика гладят (он при этом всегда говорит «мррр»), сердечек не
        было видно. Нижний край облачка — хвостик — должен быть выше всего,
        что рисуется над головой."""
        meta = dr.load_meta()
        window = dr.window_for(meta, lambda *a: "img")
        # BubbleWindow.update: хвостик кончается на y=56 плюс обводка 2
        bubble_bottom = window.FOOT - window.H + 4 - window.BUBBLE_LIFT + 58
        canvas = ImageCanvas(window.W, window.H)
        artist = dr.BarsikArtist(canvas, meta, lambda *a: "img")
        highest = window.H
        for state in (dc.PET, dc.CONFUSED, dc.SLEEP, dc.HAPPY):
            for facing in (1, -1):
                brain = make_brain(meta, state, facing)
                for frame in range(0, 60):
                    canvas.delete()
                    brain.frame = brain.t = frame
                    artist.draw(brain, window.W * 0.5, window.FOOT)
                    for args, kw in canvas.items[1:]:
                        ys = coords(args)[1::2]
                        if "font" in kw:          # у текста — центр, берём верх буквы
                            ys = [y - kw["font"][1] * 0.7 for y in ys]
                        highest = min([highest] + ys)
        self.assertLessEqual(bubble_bottom, highest)
        self.assertEqual(dc.CatWindow.BUBBLE_LIFT, 0)      # у Мурки всё как было

    def test_barsik_never_climbs_so_high_that_his_ears_go_under_the_menu_bar(self):
        # Мурке хватало запаса cat_h * 0.55, а Барсик выше: у верхнего края
        # экрана, где на Маке лежат иконки, уши и хвост уезжали под меню.
        meta = dr.load_meta()
        window = dr.window_for(meta, lambda *a: "img")
        screen = dc.Screen(cat_w=window.W, cat_h=window.H, head_room=window.HEAD_ROOM)
        _, y = screen.clamp(700, 0)
        sprite_top = y - meta["anchor"][1]
        self.assertGreaterEqual(sprite_top, screen.menubar)

    def test_layout_fits_every_common_mac_screen(self):
        """От старого MacBook Air 13 до iMac: панель, Барсик на ней, место в
        стороне от подтверждения и прогулки — всё на экране и ниже меню."""
        meta = dr.load_meta()
        window = dr.window_for(meta, lambda *a: "img")
        ay = meta["anchor"][1]
        for w, h in ((1280, 800), (1440, 900), (1470, 956), (1512, 982),
                     (1710, 1112), (1728, 1117), (2240, 1260)):
            s = dc.Screen(w=w, h=h, cat_w=window.W, cat_h=window.H,
                          perch_sink=window.PERCH_SINK, head_room=window.HEAD_ROOM)
            bx, by = s.bar_origin()
            confirm_right = bx + (dc.BAR_W + dc.ConfirmBar.W) / 2
            spots = [s.perch(), s.beside_confirm(dc.ConfirmBar.W)]
            rng = random.Random(w)
            spots += [s.random_spot(rng) for _ in range(200)]
            for x, y in spots:
                self.assertGreaterEqual(x - window.W / 2, 0, (w, h))
                self.assertLessEqual(x + window.W / 2, w, (w, h))
                self.assertGreaterEqual(y - ay, s.menubar, (w, h))
            aside_x, _ = spots[1]
            self.assertGreaterEqual(aside_x - window.W / 2, confirm_right - 0.5, (w, h))
            self.assertGreaterEqual(by - dc.ConfirmBar.H - 14, s.menubar, (w, h))

    def test_repository_frames_keep_everything_inside_the_window(self):
        meta = dr.load_meta()
        w, h, foot = dr.window_size(meta)
        canvas = ImageCanvas(w, h)
        artist = dr.BarsikArtist(canvas, meta, lambda *a: "img")
        for state in ALL_STATES:
            for facing in (1, -1):
                brain = make_brain(meta, state, facing, carrying="a.txt")
                for frame in range(0, 60, 3):
                    canvas.delete()
                    brain.frame = brain.t = frame
                    brain.phase = frame * 0.4
                    artist.draw(brain, w * 0.5, foot)
                    for args, kw in canvas.items[1:]:
                        nums = coords(args)
                        for x in nums[0::2]:
                            self.assertTrue(-0.01 <= x <= w + 0.01, (state, facing, x))
                        for y in nums[1::2]:
                            self.assertTrue(-0.01 <= y <= h + 0.01, (state, facing, y))


class FakeNative:
    """Подделка слоёв macOS: запоминает, что и где показано."""

    def __init__(self):
        self.calls = []

    def begin(self):
        self.calls.append(("begin",))

    def commit(self):
        self.calls.append(("commit",))

    def put(self, slot, anim, i, facing, left, top, w, h):
        self.calls.append(("put", slot, anim, i, facing, left, top, w, h))

    def hide(self, slot):
        self.calls.append(("hide", slot))


class TestPicturesOnMac(unittest.TestCase):
    """На Маке Барсика не было видно: Tk 8.6 не рисует картинки на canvas с
    прозрачным фоном, а CI проверял только, что кадры загрузились. Снимки
    экрана в CI (tools/mac/look.py) показали: фигуры есть, картинок нет —
    ни кота, ни стекла панели. Теперь на Маке картинки рисует сам macOS
    слоями под окном, а художник лишь говорит, какой кадр и где."""

    def setUp(self):
        self.meta = dr.load_meta()
        self.flower = dr.load_flower()
        self.w, self.h, self.foot = dr.window_size(self.meta)

    def _draw(self, native, brain):
        canvas = ImageCanvas(self.w, self.h)
        artist = dr.BarsikArtist(canvas, self.meta, lambda a, i, f: (a, i, f), self.flower)
        artist.native = native
        artist.draw(brain, self.w * 0.5, self.foot)
        return canvas

    def test_native_layer_gets_the_same_frame_and_place_as_the_canvas(self):
        for facing in (1, -1):
            brain = make_brain(self.meta, dc.WALK, facing)
            on_canvas = self._draw(None, brain).items[0]
            native = FakeNative()
            canvas = self._draw(native, brain)
            (x, y), kw = on_canvas
            anim, i, _ = kw["image"]
            put = [c for c in native.calls if c[0] == "put"][0]
            self.assertEqual(put[1:7], ("cat", anim, i, facing, x, y))
            self.assertEqual(put[7:], tuple(self.meta["size"]))
            self.assertFalse([k for _, k in canvas.items if "image" in k],
                             "на Маке картинка на canvas невидима — её там быть не должно")

    def test_flower_in_mouth_goes_to_its_own_layer_above_barsik(self):
        native = FakeNative()
        self._draw(native, make_brain(self.meta, dc.CARRY, 1, carrying=dr.FLOWER))
        slots = [c[1] for c in native.calls if c[0] == "put"]
        self.assertEqual(slots, ["cat", "flower"])        # цветок поверх кота

    def test_flower_layer_is_hidden_when_he_does_not_carry_it(self):
        native = FakeNative()
        self._draw(native, make_brain(self.meta, dc.SIT, 1))
        self.assertIn(("hide", "flower"), native.calls)

    def test_one_transaction_per_frame(self):
        native = FakeNative()
        self._draw(native, make_brain(self.meta, dc.PET, 1))
        kinds = [c[0] for c in native.calls]
        self.assertEqual((kinds[0], kinds[-1]), ("begin", "commit"))
        self.assertEqual(kinds.count("begin"), 1)

    def test_hearts_and_paper_are_still_drawn_by_tk_on_top(self):
        native = FakeNative()
        canvas = self._draw(native, make_brain(self.meta, dc.PET, 1))
        self.assertGreater(len(canvas.items), 3)

    def test_layers_are_placed_from_the_top_like_on_the_canvas(self):
        # На Маке гортензия была у Барсика на лбу, а сам он висел над панелью:
        # слои отсчитывали y снизу окна (setGeometryFlipped на слое вида
        # AppKit перебивает). Середина слоя в координатах Cocoa — снизу.
        mac = dr.macimages
        # кадр у верхнего края окна высотой 224: его середина высоко, у верха
        self.assertEqual(mac.cocoa_center(10, 0, 100, 50, 224), (60, 199))
        # кадр у нижнего края: середина низко
        self.assertEqual(mac.cocoa_center(0, 174, 100, 50, 224), (50, 25))
        # окно целиком (стекло, цветок на столе) — середина в середине
        self.assertEqual(mac.cocoa_center(0, 0, 720, 116, 116), (360, 58))

    def test_off_the_mac_nothing_native_is_tried(self):
        if sys.platform == "darwin":
            self.skipTest("на Маке слои есть")
        self.assertFalse(dr.macimages.available())
        self.assertIsNone(dr.native_sprites(None, 10, 10, None, True))


class TestPanelSwitch(unittest.TestCase):
    """Стекло или прежняя панель — переключателем, чтобы старая версия
    оставалась под рукой без второй копии программы."""

    def _settings(self, text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / ".desktop_assistant_ai"
        path.write_text(text, encoding="utf-8")
        return path

    def test_glass_by_default(self):
        self.assertTrue(dr.wants_glass(False, {}, self._settings("")))

    def test_flag_from_terminal_turns_glass_off(self):
        self.assertFalse(dr.wants_glass(True, {}, self._settings("")))

    def test_line_in_the_settings_file_turns_glass_off_for_the_app(self):
        # Барсик.app не видит переменных из Терминала — только этот файл
        path = self._settings("OPENAI_API_KEY=sk-or-x\nBARSIK_PANEL=plain\n")
        self.assertFalse(dr.wants_glass(False, {}, path))
        for word in ("обычная", "старая", "0", '"plain"'):
            self.assertFalse(dr.wants_glass(False, {}, self._settings("BARSIK_PANEL=" + word)), word)
        self.assertTrue(dr.wants_glass(False, {}, self._settings("BARSIK_PANEL=glass")))

    def test_environment_wins_over_the_file(self):
        path = self._settings("BARSIK_PANEL=plain")
        self.assertTrue(dr.wants_glass(False, {"BARSIK_PANEL": "glass"}, path))

    def test_panel_setting_does_not_leak_into_ai_settings(self):
        path = self._settings("BARSIK_PANEL=plain\nASSISTANT_MODEL=m")
        self.assertEqual(dr.base.engine.read_ai_settings(path), {"ASSISTANT_MODEL": "m"})

    def test_plain_panel_is_the_previous_one(self):
        p = dr.PALETTE_PLAIN
        self.assertEqual((p.bar_glass, p.bar_alpha), (0.0, 0.75))
        self.assertGreaterEqual(contrast(p.bar_status, p.bar_bg), 4.5)
        # всё остальное — общее со стеклом
        self.assertEqual((p.bar_bg, p.yes_bg, p.no_bg, p.bubble_bg),
                         (dr.PALETTE.bar_bg, dr.PALETTE.yes_bg, dr.PALETTE.no_bg,
                          dr.PALETTE.bubble_bg))

    def test_start_passes_the_choice_to_barsik(self):
        for argv, glass in (([], True), (["--no-glass"], False)):
            with mock.patch.object(dr, "sprite_problems", return_value=[]), \
                    mock.patch.object(dr, "wants_glass", side_effect=lambda flag: not flag), \
                    mock.patch.object(dr.tk, "Tk"), \
                    mock.patch.object(dr, "BarsikApp") as app, \
                    mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(dr.main(argv), 0)
            self.assertEqual(app.call_args.kwargs["glass"], glass, argv)


class TestEntryPoints(unittest.TestCase):
    def test_check_fails_when_frames_are_missing(self):
        with mock.patch.object(dr.base, "run_check", return_value=0), \
                mock.patch.object(dr, "sprite_problems", return_value=["нет кадра x.png"]), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(dr.run_check(), 1)
        self.assertIn("git pull", out.getvalue())

    def test_start_without_frames_explains_instead_of_crashing(self):
        with mock.patch.object(dr, "sprite_problems", return_value=["нет кадра x.png"]), \
                mock.patch.object(dr.tk, "Tk") as tk_root, \
                mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(dr.main([]), 1)
        tk_root.assert_not_called()
        self.assertIn("sprites/barsik", out.getvalue())

    def test_argparse_accepts_known_flags(self):
        with mock.patch.object(dr, "run_check", return_value=0) as checker:
            self.assertEqual(dr.main(["--check"]), 0)
            checker.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
