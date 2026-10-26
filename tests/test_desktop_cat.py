#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автотесты для desktop_cat.py (версия 2).

Проверяется всё, что не требует живого экрана: геометрия рабочего стола,
разбор ответа Finder, превращение плана в маршрут кошки, её конечный
автомат (полный рабочий цикл, события, границы экрана) и вся отрисовка —
через поддельный canvas, чтобы поймать любые ошибки в геометрии поз.

Что отсюда проверить нельзя и надо смотреть на живом Маке:
прозрачность окон, окна без рамки, фокус клавиатуры в панели,
разрешение «Автоматизация → Finder» и реальные координаты иконок.
"""

import io
import math
import os
import random
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

# все три версии лежат в папке versions/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "versions"))

import desktop_assistant as engine
import desktop_cat as dc


class FakeCanvas:
    """Duck-typed canvas: считает вызовы и запоминает, что рисовали."""

    def __init__(self, w=210, h=170):
        self._w, self._h = w, h
        self.items = []

    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h

    def delete(self, *a, **k):
        self.items = []

    def _record(self, *a, **k):
        self.items.append((a, k))
        return len(self.items)

    create_rectangle = _record
    create_oval = _record
    create_polygon = _record
    create_line = _record
    create_text = _record
    create_arc = _record

    def itemconfigure(self, *a, **k):
        self.items.append((a, k))

    # ширина буквы в поддельном шрифте — нарочно шире, чем у Helvetica
    CHAR_W = 10

    def bbox(self, item):
        """Рамка текста так, как её посчитал бы Tk для широкого шрифта."""
        (x, y, *_), kw = self.items[item - 1]
        w = len(kw.get("text", "")) * self.CHAR_W
        left = x if kw.get("anchor") == "w" else x - w / 2
        return (left, y - 8, left + w, y + 8)

    def tag_raise(self, *a):
        pass

    tag_lower = tag_raise


class FakeEntry:
    """Подделка tk.Entry — ровно те методы, которыми пользуется панель."""

    def __init__(self, text=""):
        self.text = text
        self.fg = None

    def get(self):
        return self.text

    def delete(self, *a):
        self.text = ""

    def insert(self, index, text):
        self.text = self.text[:index] + text + self.text[index:]

    def config(self, fg=None, **kw):
        self.fg = fg if fg is not None else self.fg

    def icursor(self, *a):
        pass


def coords(args):
    """Все числа-координаты из вызова canvas.

    Многоугольники и линии приходят одним списком ([x1, y1, x2, y2, …]), а
    овалы и текст — отдельными числами. Раньше проверка «не вылезает за
    окно» брала только отдельные числа, и папка, бумажка и искры не
    проверялись вовсе: у Барсика папка на деле уходила под край окна."""
    out = []
    for a in args:
        if isinstance(a, (list, tuple)):
            out.extend(coords(a))
        elif isinstance(a, (int, float)):
            out.append(a)
    return out


def make_screen(w=1440, h=900):
    return dc.Screen(w=w, h=h)


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

class TestScreen(unittest.TestCase):
    def setUp(self):
        self.screen = make_screen()

    def test_clamp_keeps_cat_fully_on_screen(self):
        for raw in [(-500, -500), (99999, 99999), (0, 0),
                    (self.screen.w, self.screen.h)]:
            x, y = self.screen.clamp(*raw)
            self.assertGreaterEqual(x, dc.CAT_W * 0.5 - 0.01)
            self.assertLessEqual(x, self.screen.w - dc.CAT_W * 0.5 + 0.01)
            self.assertGreaterEqual(y, self.screen.menubar)
            self.assertLessEqual(y, self.screen.h)

    def test_clamp_leaves_valid_point_untouched(self):
        x, y = self.screen.clamp(700, 500)
        self.assertEqual((x, y), (700, 500))

    def test_bar_sits_above_the_dock(self):
        bx, by = self.screen.bar_origin()
        self.assertGreaterEqual(bx, 0)
        self.assertLess(by + dc.BAR_H, self.screen.h - self.screen.dock)

    def test_perch_is_on_the_bar(self):
        bx, by = self.screen.bar_origin()
        px, py = self.screen.perch()
        self.assertGreater(px, bx)
        self.assertLess(px, bx + dc.BAR_W)
        self.assertAlmostEqual(py, by + 8, delta=dc.CAT_H)

    def test_trash_is_bottom_right(self):
        tx, ty = self.screen.trash()
        self.assertGreater(tx, self.screen.w * 0.7)
        self.assertGreater(ty, self.screen.h * 0.7)

    def test_random_spots_always_valid(self):
        rng = random.Random(7)
        for _ in range(200):
            x, y = self.screen.random_spot(rng)
            self.assertEqual((x, y), self.screen.clamp(x, y))

    def test_tiny_screen_does_not_produce_broken_geometry(self):
        tiny = dc.Screen(w=320, h=240)
        x, y = tiny.clamp(0, 0)
        self.assertTrue(math.isfinite(x) and math.isfinite(y))
        rng = random.Random(1)
        x2, y2 = tiny.random_spot(rng)
        self.assertTrue(math.isfinite(x2) and math.isfinite(y2))


# ---------------------------------------------------------------------------
# Разбор координат иконок
# ---------------------------------------------------------------------------

class TestIconPositions(unittest.TestCase):
    def test_parses_normal_output(self):
        raw = "photo.jpg\t100\t200\nотчёт.pdf\t300\t420\n"
        self.assertEqual(dc.parse_icon_positions(raw),
                         {"photo.jpg": (100, 200), "отчёт.pdf": (300, 420)})

    def test_handles_float_coordinates(self):
        self.assertEqual(dc.parse_icon_positions("a.txt\t100.0\t200.7"),
                         {"a.txt": (100, 200)})

    def test_name_containing_tab_is_kept_whole(self):
        raw = "стран\tный.txt\t10\t20"
        self.assertEqual(dc.parse_icon_positions(raw), {"стран\tный.txt": (10, 20)})

    def test_ignores_junk_and_empty_lines(self):
        raw = "\n\nmusor\nbad\tx\ty\ngood.txt\t5\t6\n   \n"
        self.assertEqual(dc.parse_icon_positions(raw), {"good.txt": (5, 6)})

    def test_handles_crlf_and_none(self):
        self.assertEqual(dc.parse_icon_positions("a.txt\t1\t2\r\n"), {"a.txt": (1, 2)})
        self.assertEqual(dc.parse_icon_positions(None), {})
        self.assertEqual(dc.parse_icon_positions(""), {})

    def test_icon_point_applies_menubar_and_icon_offsets(self):
        screen = make_screen()
        positions = {"a.txt": (400, 300)}
        x, y = dc.icon_point("a.txt", positions, screen)
        self.assertAlmostEqual(x, 400 + dc.ICON_SIZE * 0.5)
        self.assertAlmostEqual(y, 300 + screen.menubar + dc.ICON_SIZE * 0.95)

    def test_icon_point_missing_name_returns_none(self):
        self.assertIsNone(dc.icon_point("ghost.txt", {}, make_screen()))

    def test_icon_point_is_clamped_to_screen(self):
        screen = make_screen()
        x, y = dc.icon_point("far.txt", {"far.txt": (99999, 99999)}, screen)
        self.assertEqual((x, y), screen.clamp(x, y))

    def test_read_desktop_icons_survives_missing_osascript(self):
        with mock.patch.object(dc.subprocess, "run", side_effect=FileNotFoundError()):
            self.assertEqual(dc.read_desktop_icons(), {})

    def test_read_desktop_icons_survives_permission_denied(self):
        fail = mock.Mock(returncode=1, stdout="", stderr="not allowed")
        with mock.patch.object(dc.subprocess, "run", return_value=fail):
            self.assertEqual(dc.read_desktop_icons(), {})

    def test_read_desktop_icons_parses_success(self):
        ok = mock.Mock(returncode=0, stdout="a.txt\t10\t20\n")
        with mock.patch.object(dc.subprocess, "run", return_value=ok):
            self.assertEqual(dc.read_desktop_icons(), {"a.txt": (10, 20)})


# ---------------------------------------------------------------------------
# План -> маршруты
# ---------------------------------------------------------------------------

class TestPlanToTrips(unittest.TestCase):
    def setUp(self):
        self.screen = make_screen()
        self.rng = random.Random(42)

    def _plan(self, steps):
        plan = engine.Plan(title="тест")
        plan.steps = steps
        return plan

    def test_splits_mkdirs_visual_trips_and_leftover(self):
        steps = [engine.Step("mkdir", dst="/D/Разобрать/Картинки")]
        steps += [engine.Step("move", src="/D/f{}.jpg".format(i),
                              dst="/D/Разобрать/Картинки/f{}.jpg".format(i))
                  for i in range(12)]
        mkdirs, trips, leftover = dc.plan_to_trips(self._plan(steps), {},
                                                   self.screen, self.rng,
                                                   max_visual=8)
        self.assertEqual(len(mkdirs), 1)
        self.assertEqual(len(trips), 8)
        self.assertEqual(len(leftover), 4)
        self.assertEqual([Path(s.src).name for s in leftover],
                         ["f8.jpg", "f9.jpg", "f10.jpg", "f11.jpg"])

    def test_trash_steps_go_to_the_trash_corner(self):
        plan = self._plan([engine.Step("trash", src="/D/junk.tmp")])
        _, trips, _ = dc.plan_to_trips(plan, {}, self.screen, self.rng)
        self.assertEqual(trips[0].drop, self.screen.trash())

    def test_real_icon_position_is_used_when_known(self):
        plan = self._plan([engine.Step("move", src="/D/photo.jpg",
                                       dst="/D/Разобрать/Картинки/photo.jpg")])
        positions = {"photo.jpg": (500, 300), "Картинки": (900, 200)}
        _, trips, _ = dc.plan_to_trips(plan, positions, self.screen, self.rng)
        self.assertEqual(trips[0].pick,
                         dc.icon_point("photo.jpg", positions, self.screen))
        self.assertEqual(trips[0].drop,
                         dc.icon_point("Картинки", positions, self.screen))

    def test_unknown_icon_falls_back_to_a_valid_spot(self):
        plan = self._plan([engine.Step("move", src="/D/photo.jpg",
                                       dst="/D/Разобрать/Картинки/photo.jpg")])
        _, trips, _ = dc.plan_to_trips(plan, {}, self.screen, self.rng)
        trip = trips[0]
        self.assertEqual(trip.pick, self.screen.clamp(*trip.pick))
        self.assertEqual(trip.drop, self.screen.clamp(*trip.drop))

    def test_later_trips_are_marked_quick(self):
        steps = [engine.Step("trash", src="/D/f{}.txt".format(i)) for i in range(6)]
        _, trips, _ = dc.plan_to_trips(self._plan(steps), {}, self.screen, self.rng)
        self.assertEqual([t.quick for t in trips],
                         [False, False, False, True, True, True])

    def test_label_is_the_file_name(self):
        plan = self._plan([engine.Step("trash", src="/D/важное.txt")])
        _, trips, _ = dc.plan_to_trips(plan, {}, self.screen, self.rng)
        self.assertEqual(trips[0].label, "важное.txt")

    def test_empty_plan_produces_nothing(self):
        mkdirs, trips, leftover = dc.plan_to_trips(self._plan([]), {},
                                                   self.screen, self.rng)
        self.assertEqual((mkdirs, trips, leftover), ([], [], []))

    def test_preview_lines_describe_the_first_operations(self):
        plan = self._plan([
            engine.Step("mkdir", dst="/D/Разобрать/Картинки"),
            engine.Step("move", src="/D/a.jpg", dst="/D/Разобрать/Картинки/a.jpg"),
            engine.Step("trash", src="/D/junk.tmp"),
            engine.Step("trash", src="/D/junk2.tmp"),
        ])
        lines = dc.preview_lines(plan, limit=2)
        self.assertEqual(lines[0], "a.jpg  →  Картинки")
        self.assertEqual(lines[1], "в Корзину: junk.tmp")
        self.assertEqual(lines[2], "и ещё 1")

    def test_preview_lines_without_extra_tail(self):
        plan = self._plan([engine.Step("trash", src="/D/one.txt")])
        self.assertEqual(dc.preview_lines(plan), ["в Корзину: one.txt"])

    def test_preview_lines_of_empty_plan(self):
        self.assertEqual(dc.preview_lines(self._plan([])), [])

    def test_preview_lines_ignore_mkdir_only_plans(self):
        plan = self._plan([engine.Step("mkdir", dst="/D/Разобрать/Видео")])
        self.assertEqual(dc.preview_lines(plan), [])

    def test_demo_trips_carry_no_real_steps(self):
        trips = dc.demo_trips(self.screen, self.rng, count=5)
        self.assertEqual(len(trips), 5)
        self.assertTrue(all(t.step is None for t in trips))
        self.assertTrue(all(t.label for t in trips))


# ---------------------------------------------------------------------------
# Мозги кошки
# ---------------------------------------------------------------------------

class TestCatBrain(unittest.TestCase):
    def setUp(self):
        self.screen = make_screen()
        self.brain = dc.CatBrain(self.screen, random.Random(3))

    def _trip(self, label="a.txt", pick=(200, 400), drop=(1100, 300), quick=False):
        return dc.Trip(engine.Step("trash", src="/D/" + label), pick, drop,
                       label, quick)

    def _run(self, frames, collect=None):
        events = []
        for _ in range(frames):
            for ev in self.brain.tick():
                events.append(ev)
                if collect and ev[0] == collect:
                    pass
        return events

    def test_starts_sitting_on_the_perch(self):
        self.assertEqual(self.brain.state, dc.SIT)
        self.assertEqual((self.brain.x, self.brain.y), self.screen.perch())

    def test_walking_reduces_distance_and_arrives(self):
        self.brain._go((1200, 500), "wander")
        self.assertEqual(self.brain.state, dc.WALK)
        prev = math.hypot(1200 - self.brain.x, 500 - self.brain.y)
        for _ in range(600):
            self.brain.tick()
            if self.brain.state != dc.WALK:
                break
            now = math.hypot(1200 - self.brain.x, 500 - self.brain.y)
            self.assertLess(now, prev + 0.001)
            prev = now
        self.assertEqual(self.brain.state, dc.SIT)
        self.assertAlmostEqual(self.brain.x, 1200, delta=1)

    def test_facing_flips_with_direction(self):
        self.brain.x = 200
        self.brain._go((1200, self.brain.y), "wander")
        self.brain.tick()
        self.assertEqual(self.brain.facing, 1)
        self.brain._go((50, self.brain.y), "wander")
        self.brain.tick()
        self.assertEqual(self.brain.facing, -1)

    def test_full_job_applies_every_trip_once_in_order(self):
        trips = [self._trip("a.txt"), self._trip("b.txt"), self._trip("c.txt")]
        self.brain.start_job(trips)
        events = self._run(4000)
        applied = [e[1].label for e in events if e[0] == "apply"]
        self.assertEqual(applied, ["a.txt", "b.txt", "c.txt"])
        self.assertEqual(self.brain.done_count, 3)

    def test_job_ends_happy_then_returns_home_and_sits(self):
        self.brain.start_job([self._trip()])
        finished = False
        for _ in range(5000):
            for ev in self.brain.tick():
                if ev[0] == "finished":
                    finished = True
            if finished and self.brain.state == dc.SIT:
                break
        self.assertTrue(finished, "работа должна завершиться событием finished")
        self.assertEqual(self.brain.state, dc.SIT)
        self.assertAlmostEqual(self.brain.x, self.screen.perch()[0], delta=2,
                               msg="после работы кошка возвращается на панель")

    def test_carrying_only_between_pick_and_drop(self):
        self.brain.start_job([self._trip()])
        seen_carrying = False
        for _ in range(3000):
            self.brain.tick()
            if self.brain.carrying:
                seen_carrying = True
                self.assertIn(self.brain.state, (dc.CARRY, dc.DROP))
            if self.brain.state == dc.HAPPY:
                break
        self.assertTrue(seen_carrying)
        self.assertIsNone(self.brain.carrying)

    def test_picked_event_precedes_apply_event(self):
        self.brain.start_job([self._trip()])
        kinds = [e[0] for e in self._run(3000) if e[0] in ("picked", "apply")]
        self.assertEqual(kinds[:2], ["picked", "apply"])

    def test_leftover_is_applied_once_as_a_batch(self):
        leftover = [engine.Step("trash", src="/D/x{}.txt".format(i))
                    for i in range(5)]
        self.brain.start_job([self._trip()], leftover)
        events = self._run(5000)
        batches = [e for e in events if e[0] == "apply_batch"]
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0][1]), 5)
        self.assertEqual(self.brain.done_count, 6)

    def test_job_with_only_leftover_still_sweeps(self):
        leftover = [engine.Step("trash", src="/D/x.txt")]
        self.brain.start_job([], leftover)
        events = self._run(4000)
        self.assertIn("apply_batch", [e[0] for e in events])
        self.assertIn("finished", [e[0] for e in events])

    def test_empty_job_goes_straight_to_happy(self):
        self.brain.start_job([])
        self.assertEqual(self.brain.state, dc.HAPPY)
        events = self._run(3000)
        self.assertIn("finished", [e[0] for e in events])

    def test_demo_trip_still_emits_apply_with_empty_step(self):
        trip = dc.Trip(None, (300, 400), (900, 400), "демо.txt")
        self.brain.start_job([trip])
        applied = [e for e in self._run(3000) if e[0] == "apply"]
        self.assertEqual(len(applied), 1)
        self.assertIsNone(applied[0][1].step)

    def test_cat_never_leaves_the_screen_during_a_long_job(self):
        rng = random.Random(11)
        trips = [self._trip("f{}.txt".format(i), self.screen.random_spot(rng),
                            self.screen.random_spot(rng)) for i in range(6)]
        self.brain.start_job(trips)
        for _ in range(6000):
            self.brain.tick()
            self.assertGreaterEqual(self.brain.x, dc.CAT_W * 0.5 - 1)
            self.assertLessEqual(self.brain.x, self.screen.w - dc.CAT_W * 0.5 + 1)
            self.assertGreaterEqual(self.brain.y, self.screen.menubar - 1)
            self.assertLessEqual(self.brain.y, self.screen.h + 1)

    def test_quick_trips_move_faster(self):
        self.brain.start_job([self._trip(quick=True)])
        self.assertGreater(self.brain.speed, dc.WALK_SPEED)

    def test_busy_flag_reflects_work(self):
        self.assertFalse(self.brain.busy)
        self.brain.start_job([self._trip()])
        self.assertTrue(self.brain.busy)

    def test_petting_is_ignored_while_working(self):
        self.brain.start_job([self._trip()])
        state = self.brain.state
        self.brain.be_petted()
        self.assertEqual(self.brain.state, state)

    def test_petting_while_idle_plays_the_animation(self):
        self.brain.be_petted()
        self.assertEqual(self.brain.state, dc.PET)
        for _ in range(dc.PET_FRAMES + 2):
            self.brain.tick()
        self.assertEqual(self.brain.state, dc.SIT)

    def test_confused_returns_to_sitting(self):
        self.brain.be_confused()
        self.assertEqual(self.brain.state, dc.CONFUSED)
        self.assertTrue(self.brain.say)
        for _ in range(dc.CONFUSED_FRAMES + 2):
            self.brain.tick()
        self.assertEqual(self.brain.state, dc.SIT)

    def test_nothing_to_do_walks_home(self):
        self.brain.x, self.brain.y = 200, 400
        self.brain.nothing_to_do()
        self.assertEqual(self.brain.state, dc.WALK)
        for _ in range(3000):
            self.brain.tick()
            if self.brain.state == dc.SIT:
                break
        self.assertAlmostEqual(self.brain.x, self.screen.perch()[0], delta=2)

    def test_speech_expires_on_its_own(self):
        self.brain.speak("мур", frames=5)
        self.assertEqual(self.brain.say, "мур")
        for _ in range(6):
            self.brain.tick()
        self.assertEqual(self.brain.say, "")

    def test_idle_cat_eventually_does_something(self):
        brain = dc.CatBrain(self.screen, random.Random(5))
        states = set()
        for _ in range(4000):
            brain.tick()
            states.add(brain.state)
        self.assertTrue(states & {dc.WALK, dc.SLEEP},
                        "кошка должна хотя бы иногда гулять или спать")

    def test_sleeping_cat_wakes_on_command(self):
        self.brain._set_state(dc.SLEEP)
        self.brain.wake()
        self.assertEqual(self.brain.state, dc.SIT)

    def test_unknown_state_does_not_crash_tick(self):
        self.brain.state = "какое-то-новое-состояние"
        self.brain.tick()  # не должно бросать


# ---------------------------------------------------------------------------
# Отрисовка
# ---------------------------------------------------------------------------

class TestWalkCycle(unittest.TestCase):
    """Лапы раньше двигались наоборот: опорная скользила вперёд, а
    поднятая уносилась назад — кошка шла лунной походкой. Знак косинуса
    был не тот, и на глаз в статике это не видно."""

    STRIDE, LIFT = 15, 9

    def _paw(self, phase):
        return dc.paw_position(0, phase, self.STRIDE, self.LIFT)

    def test_planted_paw_pushes_backward(self):
        # опорная фаза: лапа на земле и должна ехать назад, толкая кошку
        phases = [math.pi + i * (math.pi / 12) for i in range(1, 12)]
        for a, b in zip(phases, phases[1:]):
            xa, ya = self._paw(a)
            xb, yb = self._paw(b)
            self.assertEqual((ya, yb), (0.0, 0.0), "лапа должна быть на земле")
            self.assertLess(xb, xa, "опорная лапа обязана ехать назад")

    def test_lifted_paw_swings_forward(self):
        # маховая фаза: лапа поднята и переставляется вперёд
        phases = [i * (math.pi / 12) for i in range(1, 12)]
        for a, b in zip(phases, phases[1:]):
            xa, ya = self._paw(a)
            xb, yb = self._paw(b)
            self.assertLess(ya, 0, "лапа должна быть в воздухе")
            self.assertGreater(xb, xa, "поднятая лапа обязана идти вперёд")

    def test_paw_touches_down_in_front_and_lifts_off_behind(self):
        front, _ = self._paw(math.pi)      # конец маха — приземление
        back, _ = self._paw(0.0)           # конец опоры — отрыв
        self.assertAlmostEqual(front, self.STRIDE, places=6)
        self.assertAlmostEqual(back, -self.STRIDE, places=6)

    def test_stride_matches_travel_speed_so_paws_do_not_skate(self):
        """Длина шага должна совпадать с тем, сколько кошка проезжает за
        опорную фазу. Иначе лапы будут скользить по полу, как на льду."""
        cycle_frames = 2 * math.pi / dc.PHASE_STEP
        travel_per_stance = cycle_frames / 2 * dc.WALK_SPEED
        for stride in (15, 16):
            self.assertAlmostEqual(2 * stride, travel_per_stance,
                                   delta=travel_per_stance * 0.3,
                                   msg="шаг {} не совпадает с проездом {:.1f}"
                                       .format(2 * stride, travel_per_stance))

    def test_diagonal_legs_are_in_opposite_phases(self):
        """У кошки диагональные лапы работают в противофазе — иначе она
        будет прыгать как кролик."""
        left, _ = dc.paw_position(0, 0.0, self.STRIDE, self.LIFT)
        right, _ = dc.paw_position(0, math.pi, self.STRIDE, self.LIFT)
        self.assertAlmostEqual(left, -right, places=6)


class FakeWin:
    def geometry(self, *a):
        pass

    deiconify = lift = withdraw = geometry


class TestStepAsideDuringConfirmation(unittest.TestCase):
    """Окно подтверждения встаёт над панелью ровно там, где сидит кот, и
    закрывало его целиком — торчали одни уши. Пока оно висит, кот отходит."""

    def _confirm_rect(self, screen):
        bx, by = screen.bar_origin()
        left = bx + (dc.BAR_W - dc.ConfirmBar.W) / 2
        return left, left + dc.ConfirmBar.W

    def test_the_spot_beside_the_dialog_keeps_the_cat_window_clear_of_it(self):
        for cat_w, cat_h in ((dc.CAT_W, dc.CAT_H), (278, 224)):   # Мурка и Барсик
            screen = dc.Screen(cat_w=cat_w, cat_h=cat_h)
            x, _ = screen.beside_confirm(dc.ConfirmBar.W)
            _, right = self._confirm_rect(screen)
            self.assertGreaterEqual(x - cat_w / 2, right, cat_w)
            self.assertLessEqual(x + cat_w / 2, screen.w)

    def test_cat_walks_aside_and_sits_facing_the_dialog(self):
        screen = make_screen()
        brain = dc.CatBrain(screen, random.Random(2))
        bx, _ = screen.bar_origin()
        brain.step_aside(screen.beside_confirm(dc.ConfirmBar.W), bx + dc.BAR_W / 2)
        for _ in range(2000):
            brain.tick()
            if brain.state == dc.SIT:
                break
        self.assertEqual(brain.state, dc.SIT)
        self.assertEqual((brain.x, brain.y), screen.beside_confirm(dc.ConfirmBar.W))
        self.assertEqual(brain.facing, -1)      # диалог слева — смотрит на него

    def _app(self):
        app = dc.CatApp.__new__(dc.CatApp)
        app.screen = make_screen()
        app.brain = dc.CatBrain(app.screen, random.Random(3))
        app.root, app.bar, app.confirm = None, mock.Mock(), None
        app.busy = True
        return app

    def test_asking_for_confirmation_sends_the_cat_aside(self):
        app = self._app()
        plan = mock.Mock(count_files=mock.Mock(return_value=2), steps=[])
        with mock.patch.object(dc, "ConfirmBar") as bar_cls, \
                mock.patch.object(dc, "preview_lines", return_value=["a → b"]):
            bar_cls.W = 560
            app.ask_confirmation(plan)
        self.assertEqual(app.brain.purpose, "aside")
        self.assertEqual(app.brain.target, app.screen.beside_confirm(560))

    def test_declining_brings_the_cat_back_to_the_bar(self):
        app = self._app()
        app.brain.step_aside(app.screen.beside_confirm(560), 0)
        app._confirm_no()
        self.assertEqual(app.brain.purpose, "home")
        self.assertEqual(app.brain.target, app.screen.perch())


class TestSpeechBubble(unittest.TestCase):
    def test_bubble_sits_above_the_cat_when_there_is_room(self):
        y, below = dc.bubble_spot(600, dc.CatWindow, dc.MENUBAR_H)
        self.assertFalse(below)
        self.assertEqual(y, 600 - dc.CAT_H + 4)

    def test_bubble_moves_under_the_paws_at_the_top_of_the_screen(self):
        # Иконки на Маке лежат сверху. Там облачко уезжало под строку меню и
        # за экран, реплику было не прочитать.
        screen = make_screen()
        _, top_y = screen.clamp(700, 0)
        y, below = dc.bubble_spot(top_y, dc.CatWindow, screen.menubar)
        self.assertTrue(below)
        self.assertGreater(y, top_y)
        self.assertGreaterEqual(y, screen.menubar)

    def test_bubble_under_the_paws_points_its_tail_up(self):
        bubble = dc.BubbleWindow.__new__(dc.BubbleWindow)
        bubble.canvas, bubble.win, bubble.shown = FakeCanvas(260, 64), FakeWin(), False
        for below in (False, True):
            bubble.update("держу", 500, 300, below)
            tail = [a for a, kw in bubble.canvas.items if len(a) == 6][0]
            box_top = min(coords([a for a, kw in bubble.canvas.items if kw.get("smooth")][0]))
            tip_y, base_y = tail[5], tail[1]
            if below:
                self.assertLess(tip_y, base_y)          # хвостик вверх, к коту
                self.assertLess(tip_y, box_top)
            else:
                self.assertGreater(tip_y, base_y)       # хвостик вниз
            for y in coords([a for a, kw in bubble.canvas.items])[1::2]:
                self.assertTrue(0 <= y <= dc.BubbleWindow.H, y)


class TestTextFitsItsFrame(unittest.TestCase):
    """Рамки кнопки и облачка считались под один шрифт: «Вернуть как было»
    выезжал за кнопку, а длинная реплика — за край облачка. Теперь рамка
    строится по ширине, которую текст получил на деле (canvas.bbox)."""

    def _frame_and_text(self, canvas, text):
        label = [(a, kw) for a, kw in canvas.items if kw.get("text") == text][0]
        idx = canvas.items.index(label) + 1
        tx1, _, tx2, _ = canvas.bbox(idx)
        # рамка — ближайший к тексту скруглённый прямоугольник
        frames = [coords(a)[0::2] for a, kw in canvas.items if kw.get("smooth")]
        frame = min(frames, key=lambda xs: abs(min(xs) - tx1) + abs(max(xs) - tx2))
        return (min(frame), max(frame)), (tx1, tx2)

    def test_undo_button_is_as_wide_as_its_text(self):
        class Canvas(FakeCanvas):
            def __init__(self, *a, **k):
                super().__init__(dc.BAR_W, dc.BAR_H)

            def pack(self, *a, **k):
                pass

            tag_bind = bind = create_window = pack

        win = mock.Mock()
        with mock.patch.object(dc, "make_overlay", return_value=(win, True)), \
                mock.patch.object(dc.tk, "Canvas", Canvas), \
                mock.patch.object(dc.tk, "Entry"):
            bar = dc.CommandBar(None, make_screen(), None, None, None)
        (fx1, fx2), (tx1, tx2) = self._frame_and_text(bar.canvas, "Вернуть как было")
        self.assertLessEqual(fx1 + 6, tx1)
        self.assertGreaterEqual(fx2 - 6, tx2)

    def test_long_phrase_stays_inside_the_bubble(self):
        bubble = dc.BubbleWindow.__new__(dc.BubbleWindow)
        bubble.canvas, bubble.win, bubble.shown = FakeCanvas(260, 64), FakeWin(), False
        bubble.update("нашёл тебе цветок", 500, 300)
        (fx1, fx2), (tx1, tx2) = self._frame_and_text(bubble.canvas, "нашёл тебе цветок")
        self.assertLessEqual(fx1, tx1)
        self.assertGreaterEqual(fx2, tx2)
        self.assertTrue(0 <= fx1 and fx2 <= dc.BubbleWindow.W)


class TestCatArtist(unittest.TestCase):
    def setUp(self):
        self.canvas = FakeCanvas()
        self.artist = dc.CatArtist(self.canvas)
        self.screen = make_screen()

    def _brain(self, state, carrying=None, facing=1, frame=5):
        brain = dc.CatBrain(self.screen, random.Random(1))
        brain.state = state
        brain.carrying = carrying
        brain.facing = facing
        brain.frame = frame
        brain.t = 33
        brain.phase = 1.2
        return brain

    def test_every_state_draws_without_errors(self):
        states = [dc.SIT, dc.SLEEP, dc.WALK, dc.CARRY, dc.PICK, dc.DROP,
                  dc.SWEEP, dc.HAPPY, dc.CONFUSED, dc.PET]
        for state in states:
            for facing in (1, -1):
                for carrying in (None, "файл.txt"):
                    self.canvas.delete()
                    brain = self._brain(state, carrying, facing)
                    self.artist.draw(brain, 105, dc.FOOT_Y)
                    self.assertGreater(len(self.canvas.items), 5,
                                       "состояние {} нарисовало слишком "
                                       "мало".format(state))

    def test_animation_progresses_over_frames(self):
        brain = self._brain(dc.WALK)
        for frame in range(1, 60):
            self.canvas.delete()
            brain.frame = frame
            brain.t = frame
            brain.phase = frame * 0.33
            self.artist.draw(brain, 105, dc.FOOT_Y)
            self.assertTrue(self.canvas.items)

    def test_carried_paper_appears_only_while_carrying(self):
        brain = self._brain(dc.CARRY, carrying="файл.txt")
        self.artist.draw(brain, 105, dc.FOOT_Y)
        with_paper = len(self.canvas.items)
        self.canvas.delete()
        brain.carrying = None
        self.artist.draw(brain, 105, dc.FOOT_Y)
        self.assertLess(len(self.canvas.items), with_paper)

    def test_pick_and_drop_progress_through_full_range(self):
        for state, frames in ((dc.PICK, dc.PICK_FRAMES), (dc.DROP, dc.DROP_FRAMES)):
            brain = self._brain(state)
            for frame in range(0, frames + 5):
                self.canvas.delete()
                brain.frame = frame
                self.artist.draw(brain, 105, dc.FOOT_Y)
                self.assertTrue(self.canvas.items)

    def test_nothing_is_drawn_outside_the_sprite_window(self):
        """Всё, что вылезет за окно-спрайт, на реальном экране просто
        обрежется — получится кошка без хвоста или без ушей. Проверяем
        каждую позу, оба направления и весь цикл анимации."""
        states = [dc.SIT, dc.SLEEP, dc.WALK, dc.CARRY, dc.PICK, dc.DROP,
                  dc.SWEEP, dc.HAPPY, dc.CONFUSED, dc.PET]
        worst = {}
        for state in states:
            for facing in (1, -1):
                brain = self._brain(state, carrying="файл.txt", facing=facing)
                for frame in range(1, 60):
                    self.canvas.delete()
                    brain.frame = frame
                    brain.t = frame
                    brain.phase = frame * 0.33
                    self.artist.draw(brain, dc.CAT_W * 0.5, dc.FOOT_Y)
                    for args, _ in self.canvas.items:
                        nums = coords(args)
                        for x in nums[0::2]:
                            worst[state] = min(worst.get(state, 0),
                                               x, dc.CAT_W - x)
                        for y in nums[1::2]:
                            worst[state] = min(worst.get(state, 0),
                                               y, dc.CAT_H - y)
        overflowing = {s: round(v, 1) for s, v in worst.items() if v < 0}
        self.assertEqual(overflowing, {},
                         "эти позы вылезают за окно {}x{}: {}".format(
                             dc.CAT_W, dc.CAT_H, overflowing))

    def test_round_rect_produces_one_smooth_polygon(self):
        canvas = FakeCanvas()
        item = dc.round_rect(canvas, 0, 0, 100, 50, 12, fill="#000000")
        self.assertEqual(len(canvas.items), 1)
        args, kwargs = canvas.items[0]
        self.assertTrue(kwargs.get("smooth"))
        self.assertEqual(item, 1)


class TestCommandBarKeys(unittest.TestCase):
    """Escape раньше уезжал дальше по цепочке обработчиков и мог закрыть
    всю программу заодно с очисткой строки. Логика проверяется без Tk —
    у панели подменяются поле ввода и canvas."""

    def _bar(self, text=""):
        bar = dc.CommandBar.__new__(dc.CommandBar)
        bar.p = dc.PALETTE
        bar.entry = FakeEntry()
        bar.canvas = FakeCanvas()
        bar.hinting = False
        bar._show_hint()
        if text:
            self._type(bar, text)
        self.exits = []
        self.sent = []
        bar.on_exit = lambda: self.exits.append(True)
        bar.on_submit = self.sent.append
        return bar

    @staticmethod
    def _type(bar, text):
        """Как Tk: сначала наш обработчик KeyPress, потом вставка буквы."""
        for ch in text:
            if bar._key(mock.Mock(char=ch, keysym=ch)) != "break":
                bar.entry.insert(len(bar.entry.get()), ch)
            bar._sync_hint()

    def test_escape_with_text_only_clears_it(self):
        bar = self._bar("убери стол")
        self.assertEqual(bar._escape(), "break")
        self.assertEqual(bar.text(), "")
        self.assertEqual(self.exits, [], "выходить из программы тут нельзя")

    def test_escape_on_empty_line_exits(self):
        bar = self._bar("")
        self.assertEqual(bar._escape(), "break")
        self.assertEqual(self.exits, [True])

    def test_escape_always_stops_propagation(self):
        for text in ("", "что-то"):
            self.assertEqual(self._bar(text)._escape(), "break")

    def test_hint_reappears_after_clearing(self):
        bar = self._bar("текст")
        bar._escape()
        self.assertEqual(bar.entry.get(), dc.HINT)
        self.assertEqual(bar.entry.fg, dc.PALETTE.bar_hint)

    def test_hint_is_inside_the_field_where_it_can_be_seen(self):
        # Подсказка была надписью на canvas под полем ввода. Поле — встроенное
        # окно, оно всегда поверх надписей canvas, и «Ask anything…» никто
        # не видел: пустая панель была просто пустой.
        bar = self._bar("")
        self.assertEqual(bar.entry.get(), dc.HINT)
        self.assertNotIn(dc.HINT, [k.get("text") for _, k in bar.canvas.items])
        self.assertEqual(bar.text(), "")

    def test_first_letter_replaces_the_hint(self):
        bar = self._bar("у")
        self.assertEqual(bar.entry.get(), "у")
        self.assertEqual(bar.entry.fg, dc.PALETTE.bar_text)
        self._type(bar, "бери")
        self.assertEqual(bar.text(), "убери")

    def test_backspace_does_not_eat_the_hint(self):
        bar = self._bar("")
        self.assertEqual(bar._key(mock.Mock(char="\x08", keysym="BackSpace")), "break")
        self.assertEqual(bar.entry.get(), dc.HINT)

    def test_hint_comes_back_when_everything_is_erased(self):
        bar = self._bar("аб")
        bar.entry.delete(0, "end")          # стёрли всё руками
        bar._sync_hint()
        self.assertEqual(bar.entry.get(), dc.HINT)

    def test_enter_on_the_hint_sends_nothing(self):
        bar = self._bar("")
        bar._submit()
        self.assertEqual(self.sent, [])

    def test_enter_sends_what_was_typed_and_brings_the_hint_back(self):
        bar = self._bar("убери стол")
        bar._submit()
        self.assertEqual(self.sent, ["убери стол"])
        self.assertEqual(bar.entry.get(), dc.HINT)

    def test_shift_alone_keeps_the_hint(self):
        bar = self._bar("")
        bar._key(mock.Mock(char="", keysym="Shift_L"))
        self.assertTrue(bar.hinting)


# ---------------------------------------------------------------------------
# Связка с движком версии 1
# ---------------------------------------------------------------------------

class TestEngineIntegration(unittest.TestCase):
    """Кошка носит файлы по одному — проверяем, что «донесла» реально
    означает «файл переехал», и что журнал отката при этом честный."""

    def setUp(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="dc_test_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.desktop = tmp / "Desktop"
        self.trash = tmp / ".Trash"
        self.desktop.mkdir()
        self.undo = tmp / ".undo.json"
        for p in [mock.patch.object(engine, "DESKTOP", self.desktop),
                  mock.patch.object(engine, "ALLOWED_ROOTS", [self.desktop]),
                  mock.patch.object(engine, "TRASH", self.trash),
                  mock.patch.object(engine, "UNDO_FILE", self.undo)]:
            p.start()
            self.addCleanup(p.stop)
        self.logs = []

    def log(self, m):
        self.logs.append(m)

    def test_one_trip_moves_exactly_one_file(self):
        a = self.desktop / "a.txt"
        b = self.desktop / "b.txt"
        a.write_text("a")
        b.write_text("b")
        plan = engine.Plan()
        plan.steps = [engine.Step("trash", src=str(a)),
                      engine.Step("trash", src=str(b))]
        _, trips, _ = dc.plan_to_trips(plan, {}, make_screen(), random.Random(1))

        entry = engine.apply_step(trips[0].step, self.log)
        self.assertIsNotNone(entry)
        self.assertFalse(a.exists())
        self.assertTrue(b.exists(), "второй файл должен подождать своего рейса")

        engine.apply_step(trips[1].step, self.log)
        self.assertFalse(b.exists())

    def test_journal_accumulates_across_trips_and_undo_restores_all(self):
        files = []
        for name in ("x.txt", "y.txt", "z.txt"):
            f = self.desktop / name
            f.write_text(name)
            files.append(f)
        plan = engine.Plan()
        plan.steps = [engine.Step("trash", src=str(f)) for f in files]
        _, trips, _ = dc.plan_to_trips(plan, {}, make_screen(), random.Random(1))

        journal = []
        op_id = "рейсы-одного-захода"
        for trip in trips:
            entry = engine.apply_step(trip.step, self.log)
            if entry:
                journal.append(entry)
            engine.record_operation(journal, "Уборка", op_id)

        self.assertTrue(all(not f.exists() for f in files))
        # все три рейса должны лежать в журнале одной операцией, а не тремя
        self.assertEqual(engine.operations_left(), 1)
        restored = engine.undo_last(self.log)
        self.assertEqual(restored, 3)
        self.assertTrue(all(f.exists() for f in files))
        self.assertEqual(engine.operations_left(), 0)

    def test_mkdirs_run_before_moves_so_targets_exist(self):
        f = self.desktop / "photo.jpg"
        f.write_text("img")
        target_dir = self.desktop / "Разобрать" / "Картинки"
        plan = engine.Plan()
        plan.steps = [engine.Step("mkdir", dst=str(target_dir)),
                      engine.Step("move", src=str(f),
                                  dst=str(target_dir / "photo.jpg"))]
        mkdirs, trips, _ = dc.plan_to_trips(plan, {}, make_screen(),
                                            random.Random(1))
        for step in mkdirs:
            engine.apply_step(step, self.log)
        self.assertTrue(target_dir.is_dir())

        engine.apply_step(trips[0].step, self.log)
        self.assertTrue((target_dir / "photo.jpg").exists())

    def test_trip_outside_allowed_roots_is_refused_by_the_engine(self):
        outside = Path(self.desktop).parent / "outside.txt"
        outside.write_text("no")
        step = engine.Step("trash", src=str(outside))
        entry = engine.apply_step(step, self.log)
        self.assertIsNone(entry)
        self.assertTrue(outside.exists())
        self.assertTrue(any("вне разрешённых" in m for m in self.logs))


# ---------------------------------------------------------------------------
# main / --check не должны падать на не-macOS
# ---------------------------------------------------------------------------

class TestEntryPoints(unittest.TestCase):
    def test_check_reports_when_tk_cannot_start(self):
        with mock.patch.object(dc.tk, "Tk", side_effect=RuntimeError("no display")):
            with mock.patch("sys.stdout", io.StringIO()) as out:
                self.assertEqual(dc.run_check(), 1)
        self.assertIn("НЕ ЗАПУСКАЕТСЯ", out.getvalue())

    def test_argparse_accepts_known_flags(self):
        with mock.patch.object(dc, "run_check", return_value=0) as checker:
            self.assertEqual(dc.main(["--check"]), 0)
            checker.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
