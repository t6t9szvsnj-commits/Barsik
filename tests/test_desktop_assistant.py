#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автотесты для desktop_assistant.py — всё, что можно проверить без
реального экрана macOS: планирование, файловые операции, откат,
обработку ошибок доступа и «мозги» AI-режима. Требует python3 со
встроенным tkinter (сам import модуля тянет tkinter, хотя окно нигде
не создаётся — тесты не открывают Tk()).
"""

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

# все три версии лежат в папке versions/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "versions"))

import desktop_assistant as da


def touch(path, content=b"data", days_ago=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if days_ago:
        t = time.time() - days_ago * 86400.0
        os.utime(path, (t, t))
    return path


class IsolatedRootsTestCase(unittest.TestCase):
    """Базовый класс: подменяет DESKTOP/DOWNLOADS/TRASH/UNDO_FILE
    на временную папку, чтобы тесты не трогали ничего реального."""

    def setUp(self):
        self._tmp = mock.patch.dict(os.environ)  # no-op placeholder for clarity
        self.tmpdir = self._make_tmp()
        self.desktop = self.tmpdir / "Desktop"
        self.downloads = self.tmpdir / "Downloads"
        self.trash = self.tmpdir / ".Trash"
        self.undo_file = self.tmpdir / ".undo.json"
        self.desktop.mkdir()
        self.downloads.mkdir()

        self._patches = [
            mock.patch.object(da, "DESKTOP", self.desktop),
            mock.patch.object(da, "DOWNLOADS", self.downloads),
            mock.patch.object(da, "ALLOWED_ROOTS", [self.desktop, self.downloads]),
            mock.patch.object(da, "TRASH", self.trash),
            mock.patch.object(da, "UNDO_FILE", self.undo_file),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _make_tmp(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="da_test_")
        self.addCleanup(shutil.rmtree, d, True)
        return Path(d)


# ---------------------------------------------------------------------------
# is_allowed
# ---------------------------------------------------------------------------

class TestIsAllowed(IsolatedRootsTestCase):
    def test_inside_desktop_allowed(self):
        f = touch(self.desktop / "a.txt")
        self.assertTrue(da.is_allowed(f))

    def test_inside_downloads_allowed(self):
        f = touch(self.downloads / "a.txt")
        self.assertTrue(da.is_allowed(f))

    def test_outside_roots_denied(self):
        outside = self.tmpdir / "elsewhere" / "a.txt"
        touch(outside)
        self.assertFalse(da.is_allowed(outside))

    def test_nested_subfolder_allowed(self):
        f = touch(self.desktop / "Разобрать" / "Картинки" / "x.png")
        self.assertTrue(da.is_allowed(f))

    def test_symlink_escaping_allowed_root_is_rejected(self):
        # Симлинк физически лежит на Столе, но указывает наружу — resolve()
        # должен раскрыть реальную цель, и is_allowed обязана её отвергнуть,
        # иначе через симлинк можно было бы двигать/удалять что угодно.
        real_target = touch(self.tmpdir / "secret" / "file.txt")
        link = self.desktop / "innocent.txt"
        link.symlink_to(real_target)
        self.assertFalse(da.is_allowed(link))

    def test_nonexistent_path_outside_root(self):
        self.assertFalse(da.is_allowed(self.tmpdir / "ghost" / "nope.txt"))


# ---------------------------------------------------------------------------
# visible_files / unique_path / age_days / category_of / file_hash
# ---------------------------------------------------------------------------

class TestFileHelpers(IsolatedRootsTestCase):
    def test_visible_files_skips_hidden_and_sort_root(self):
        touch(self.desktop / ".hidden")
        touch(self.desktop / "visible.txt")
        (self.desktop / da.SORT_ROOT_NAME).mkdir()
        touch(self.desktop / da.SORT_ROOT_NAME / "inner.txt")
        result = [p.name for p in da.visible_files(self.desktop)]
        self.assertEqual(result, ["visible.txt"])

    def test_visible_files_excludes_directories(self):
        touch(self.desktop / "real_file.txt")
        (self.desktop / "a_folder").mkdir()
        result = [p.name for p in da.visible_files(self.desktop)]
        self.assertEqual(result, ["real_file.txt"])

    def test_visible_files_missing_folder(self):
        self.assertEqual(da.visible_files(self.tmpdir / "no_such_dir"), [])

    def test_unique_path_no_collision(self):
        target = self.desktop / "new.txt"
        self.assertEqual(da.unique_path(target), target)

    def test_unique_path_with_collisions(self):
        touch(self.desktop / "dup.txt")
        touch(self.desktop / "dup (2).txt")
        result = da.unique_path(self.desktop / "dup.txt")
        self.assertEqual(result.name, "dup (3).txt")

    def test_age_days_recent_file(self):
        f = touch(self.desktop / "fresh.txt")
        self.assertLess(da.age_days(f), 0.01)

    def test_age_days_old_file(self):
        f = touch(self.desktop / "old.txt", days_ago=10)
        self.assertAlmostEqual(da.age_days(f), 10, delta=0.05)

    def test_age_days_missing_file(self):
        self.assertEqual(da.age_days(self.desktop / "ghost.txt"), 0.0)

    def test_category_of_known_and_unknown(self):
        self.assertEqual(da.category_of(Path("a.JPG")), "Картинки")
        self.assertEqual(da.category_of(Path("a.pdf")), "Документы")
        self.assertEqual(da.category_of(Path("a.zip")), "Архивы")
        self.assertEqual(da.category_of(Path("a.mp4")), "Видео")
        self.assertEqual(da.category_of(Path("a.mp3")), "Аудио")
        self.assertEqual(da.category_of(Path("a.py")), "Код")
        self.assertEqual(da.category_of(Path("a.xyz")), "Прочее")
        self.assertEqual(da.category_of(Path("no_extension")), "Прочее")

    def test_file_hash_matches_for_identical_content(self):
        a = touch(self.desktop / "a.bin", content=b"hello world")
        b = touch(self.desktop / "b.bin", content=b"hello world")
        c = touch(self.desktop / "c.bin", content=b"different")
        self.assertEqual(da.file_hash(a), da.file_hash(b))
        self.assertNotEqual(da.file_hash(a), da.file_hash(c))

    def test_file_hash_missing_file_returns_none(self):
        self.assertIsNone(da.file_hash(self.desktop / "ghost.bin"))


# ---------------------------------------------------------------------------
# parse_days
# ---------------------------------------------------------------------------

class TestParseDays(unittest.TestCase):
    def test_explicit_number_of_days(self):
        self.assertEqual(da.parse_days("удали скрины старше 10 дней", 7), 10)
        self.assertEqual(da.parse_days("файлы старше 1 дня", 7), 1)

    def test_weeks(self):
        self.assertEqual(da.parse_days("старше 2 недель", 30), 14)
        self.assertEqual(da.parse_days("старше недели", 30), 7)

    def test_months(self):
        self.assertEqual(da.parse_days("старше 3 месяцев", 30), 90)
        self.assertEqual(da.parse_days("старше месяца", 30), 30)

    def test_default_when_no_time_expression(self):
        self.assertEqual(da.parse_days("просто убери стол", 42), 42)


# ---------------------------------------------------------------------------
# планировщики (rule_plan и plan_*)
# ---------------------------------------------------------------------------

class TestPlanSort(IsolatedRootsTestCase):
    def test_groups_by_category_and_creates_only_needed_mkdirs(self):
        touch(self.desktop / "photo.jpg")
        touch(self.desktop / "doc.pdf")
        touch(self.desktop / "unknown.xyz")
        plan = da.plan_sort(self.desktop)

        mkdirs = sorted(Path(s.dst).name for s in plan.steps if s.op == "mkdir")
        self.assertEqual(mkdirs, ["Документы", "Картинки", "Прочее"])

        moves = {Path(s.src).name: Path(s.dst).parent.name
                 for s in plan.steps if s.op == "move"}
        self.assertEqual(moves["photo.jpg"], "Картинки")
        self.assertEqual(moves["doc.pdf"], "Документы")
        self.assertEqual(moves["unknown.xyz"], "Прочее")

    def test_empty_folder_produces_no_steps(self):
        plan = da.plan_sort(self.desktop)
        self.assertEqual(plan.steps, [])
        self.assertEqual(plan.count_files(), 0)


class TestPlanScreenshots(IsolatedRootsTestCase):
    def test_matches_various_screenshot_names(self):
        names = [
            "Снимок экрана 2024-01-01.png",
            "снимок 12.png",
            "Screenshot 2024-01-01 at 10.00.00.png",
            "Screen Shot 2024-01-01.png",
            "CleanShot 2024-01-01.png",
        ]
        for n in names:
            touch(self.desktop / n, days_ago=10)
        plan = da.plan_screenshots(7)
        matched = {Path(s.src).name for s in plan.steps}
        self.assertEqual(matched, set(names))

    def test_non_screenshot_not_matched(self):
        touch(self.desktop / "vacation_photo.png", days_ago=10)
        plan = da.plan_screenshots(7)
        self.assertEqual(plan.steps, [])

    def test_respects_age_threshold(self):
        touch(self.desktop / "Снимок экрана свежий.png", days_ago=1)
        plan = da.plan_screenshots(7)
        self.assertEqual(plan.steps, [])

    def test_scans_both_desktop_and_downloads(self):
        touch(self.desktop / "Снимок экрана 1.png", days_ago=10)
        touch(self.downloads / "Снимок экрана 2.png", days_ago=10)
        plan = da.plan_screenshots(7)
        self.assertEqual(plan.count_files(), 2)


class TestPlanOld(IsolatedRootsTestCase):
    def test_no_old_files_yields_empty_plan(self):
        touch(self.desktop / "fresh.txt", days_ago=1)
        plan = da.plan_old(30)
        self.assertEqual(plan.steps, [])
        self.assertEqual(plan.count_files(), 0)

    def test_old_file_is_moved_into_dated_archive(self):
        touch(self.desktop / "ancient.txt", days_ago=40)
        plan = da.plan_old(30)
        self.assertEqual(plan.count_files(), 1)
        move_steps = [s for s in plan.steps if s.op == "move"]
        self.assertEqual(len(move_steps), 1)
        self.assertTrue(Path(move_steps[0].dst).parent.name.startswith("Старое до "))

    def test_can_archive_downloads_instead_of_desktop(self):
        touch(self.desktop / "стол.txt", days_ago=40)
        touch(self.downloads / "загрузка.txt", days_ago=40)
        plan = da.plan_old(30, self.downloads)
        moved = [Path(s.src).name for s in plan.steps if s.op == "move"]
        self.assertEqual(moved, ["загрузка.txt"])
        self.assertIn(str(self.downloads),
                      [s.dst for s in plan.steps if s.op == "mkdir"][0])


class TestPlanDuplicates(IsolatedRootsTestCase):
    def test_keeps_oldest_trashes_newer_duplicate(self):
        touch(self.desktop / "older.txt", content=b"same", days_ago=5)
        touch(self.downloads / "newer.txt", content=b"same", days_ago=1)
        touch(self.desktop / "unique.txt", content=b"unique-content")
        plan = da.plan_duplicates()
        trashed = [Path(s.src).name for s in plan.steps]
        self.assertEqual(trashed, ["newer.txt"])
        self.assertNotIn("older.txt", trashed)

    def test_empty_files_are_ignored(self):
        touch(self.desktop / "empty1.txt", content=b"")
        touch(self.desktop / "empty2.txt", content=b"")
        plan = da.plan_duplicates()
        self.assertEqual(plan.steps, [])

    def test_unique_sizes_are_not_flagged(self):
        touch(self.desktop / "a.txt", content=b"x")
        touch(self.desktop / "b.txt", content=b"yy")
        plan = da.plan_duplicates()
        self.assertEqual(plan.steps, [])


class TestPlanEmptyFolders(IsolatedRootsTestCase):
    def test_empty_folder_is_flagged(self):
        (self.desktop / "empty_dir").mkdir()
        plan = da.plan_empty_folders()
        self.assertEqual([Path(s.src).name for s in plan.steps], ["empty_dir"])

    def test_folder_with_only_hidden_file_counts_as_empty(self):
        d = self.desktop / "looks_empty"
        d.mkdir()
        touch(d / ".DS_Store")
        plan = da.plan_empty_folders()
        self.assertEqual([Path(s.src).name for s in plan.steps], ["looks_empty"])

    def test_non_empty_folder_is_not_flagged(self):
        d = self.desktop / "has_stuff"
        d.mkdir()
        touch(d / "file.txt")
        plan = da.plan_empty_folders()
        self.assertEqual(plan.steps, [])

    def test_sort_root_itself_is_never_flagged(self):
        (self.desktop / da.SORT_ROOT_NAME).mkdir()
        plan = da.plan_empty_folders()
        self.assertEqual(plan.steps, [])

    def test_hidden_folder_is_ignored(self):
        (self.desktop / ".hidden_dir").mkdir()
        plan = da.plan_empty_folders()
        self.assertEqual(plan.steps, [])


class TestRulePlanDispatch(IsolatedRootsTestCase):
    def test_duplicates_keyword(self):
        plan = da.rule_plan("найди дубликаты пожалуйста")
        self.assertIn("Дубликаты", plan.title)

    def test_empty_folders_keyword(self):
        plan = da.rule_plan("удали пустые папки")
        self.assertIn("Пустые папки", plan.title)

    def test_screenshots_keyword_with_custom_days(self):
        plan = da.rule_plan("удали скрины старше 10 дней")
        self.assertIn("10 дн.", plan.title)

    def test_old_files_keyword_with_weeks(self):
        plan = da.rule_plan("убери файлы старше 2 недель")
        self.assertIn("14 дн.", plan.title)

    def test_clean_desktop_keyword(self):
        plan = da.rule_plan("убери рабочий стол")
        self.assertIn(self.desktop.name, plan.title)

    def test_clean_downloads_keyword(self):
        plan = da.rule_plan("разбери загрузки")
        self.assertIn(self.downloads.name, plan.title)

    def test_unrecognized_command_returns_none(self):
        self.assertIsNone(da.rule_plan("расскажи анекдот"))

    def test_plural_snimki_still_means_screenshots(self):
        """«снимки» раньше не совпадало со «снимок», команда проваливалась
        в ветку «старше» и вместо скриншотов архивировала все старые файлы."""
        for phrase in ("убери снимки экрана старше 3 дней",
                       "снимки экрана", "удали снимков побольше"):
            plan = da.rule_plan(phrase)
            self.assertIn("Скриншоты", plan.title,
                          "«{}» должно попадать в скриншоты".format(phrase))

    def test_screenshot_days_are_read_from_the_plural_phrase(self):
        self.assertIn("3 дн.", da.rule_plan("убери снимки экрана старше 3 дней").title)

    def test_old_files_in_downloads_touch_downloads_not_desktop(self):
        """«убери загрузки старше недели» раньше разбирало Рабочий стол."""
        touch(self.desktop / "стол.txt", days_ago=40)
        touch(self.downloads / "загрузка.txt", days_ago=40)
        plan = da.rule_plan("убери загрузки старше недели")
        moved = [Path(s.src).name for s in plan.steps if s.op == "move"]
        self.assertEqual(moved, ["загрузка.txt"])

    def test_old_files_default_to_the_desktop(self):
        touch(self.desktop / "стол.txt", days_ago=40)
        touch(self.downloads / "загрузка.txt", days_ago=40)
        plan = da.rule_plan("убери файлы старше недели")
        moved = [Path(s.src).name for s in plan.steps if s.op == "move"]
        self.assertEqual(moved, ["стол.txt"])

    def test_folder_from_picks_downloads_only_when_mentioned(self):
        self.assertEqual(da.folder_from("разбери загрузки"), self.downloads)
        self.assertEqual(da.folder_from("clean downloads"), self.downloads)
        self.assertEqual(da.folder_from("убери стол"), self.desktop)


# ---------------------------------------------------------------------------
# Plan.describe / count_files
# ---------------------------------------------------------------------------

class TestPlanDescribe(unittest.TestCase):
    def test_empty_plan_message(self):
        self.assertIn("Нечего делать", da.Plan().describe())

    def test_describe_includes_comment_and_lines(self):
        plan = da.Plan(title="Заголовок", comment="комментарий")
        plan.steps = [
            da.Step("mkdir", dst="/x/Картинки"),
            da.Step("move", src="/x/a.jpg", dst="/x/Картинки/a.jpg"),
            da.Step("trash", src="/x/junk.tmp"),
        ]
        text = plan.describe()
        self.assertIn("Заголовок", text)
        self.assertIn("комментарий", text)
        self.assertIn("создать папку Картинки", text)
        self.assertIn("junk.tmp", text)
        self.assertEqual(plan.count_files(), 2)  # mkdir не считается файлом

    def test_describe_truncates_long_plans(self):
        plan = da.Plan(title="Много всего")
        plan.steps = [da.Step("trash", src="/x/f{}.txt".format(i)) for i in range(50)]
        text = plan.describe(limit=10)
        self.assertIn("и ещё 40", text)


# ---------------------------------------------------------------------------
# execute()
# ---------------------------------------------------------------------------

class TestExecute(IsolatedRootsTestCase):
    def setUp(self):
        super().setUp()
        self.logs = []

    def log(self, msg):
        self.logs.append(msg)

    def test_mkdir_move_and_trash_happen_for_real(self):
        f1 = touch(self.desktop / "a.jpg")
        f2 = touch(self.desktop / "junk.tmp")
        plan = da.Plan()
        plan.steps = [
            da.Step("mkdir", dst=str(self.desktop / "Разобрать" / "Картинки")),
            da.Step("move", src=str(f1),
                    dst=str(self.desktop / "Разобрать" / "Картинки" / "a.jpg")),
            da.Step("trash", src=str(f2)),
        ]
        done = da.execute(plan, self.log)
        self.assertEqual(done, 2)
        self.assertTrue((self.desktop / "Разобрать" / "Картинки" / "a.jpg").exists())
        self.assertFalse(f1.exists())
        self.assertTrue((self.trash / "junk.tmp").exists())
        self.assertFalse(f2.exists())
        self.assertTrue(self.undo_file.exists())

    def test_journal_written_with_correct_paths(self):
        f1 = touch(self.desktop / "a.txt")
        plan = da.Plan(title="Тестовый план")
        plan.steps = [da.Step("trash", src=str(f1))]
        da.execute(plan, self.log)
        journal = json.loads(self.undo_file.read_text())
        self.assertEqual(len(journal["operations"]), 1)
        operation = journal["operations"][0]
        self.assertEqual(operation["title"], "Тестовый план")
        self.assertEqual(len(operation["moves"]), 1)
        self.assertEqual(operation["moves"][0]["from"], str(f1))
        self.assertEqual(Path(operation["moves"][0]["to"]).parent, self.trash)

    def test_step_outside_allowed_roots_is_skipped(self):
        outside = touch(self.tmpdir / "outside.txt")
        plan = da.Plan()
        plan.steps = [da.Step("trash", src=str(outside))]
        done = da.execute(plan, self.log)
        self.assertEqual(done, 0)
        self.assertTrue(outside.exists())
        self.assertTrue(any("вне разрешённых" in m for m in self.logs))

    def test_move_target_outside_allowed_roots_is_skipped(self):
        f1 = touch(self.desktop / "a.txt")
        plan = da.Plan()
        plan.steps = [da.Step("move", src=str(f1), dst=str(self.tmpdir / "escape" / "a.txt"))]
        done = da.execute(plan, self.log)
        self.assertEqual(done, 0)
        self.assertTrue(f1.exists())

    def test_missing_source_is_silently_skipped(self):
        plan = da.Plan()
        plan.steps = [da.Step("trash", src=str(self.desktop / "ghost.txt"))]
        done = da.execute(plan, self.log)
        self.assertEqual(done, 0)

    def test_trash_collision_gets_unique_name(self):
        touch(self.trash / "dup.txt")  # уже что-то лежит в Корзине с таким именем
        f1 = touch(self.desktop / "dup.txt")
        plan = da.Plan()
        plan.steps = [da.Step("trash", src=str(f1))]
        da.execute(plan, self.log)
        self.assertTrue((self.trash / "dup (2).txt").exists())

    def test_permission_error_on_move_is_reported_clearly(self):
        f1 = touch(self.desktop / "a.txt")
        plan = da.Plan()
        plan.steps = [da.Step("trash", src=str(f1))]
        with mock.patch.object(da.shutil, "move", side_effect=PermissionError("denied")):
            done = da.execute(plan, self.log)
        self.assertEqual(done, 0)
        self.assertTrue(f1.exists())  # файл остался на месте, не потерялся
        self.assertTrue(any("Нет доступа" in m for m in self.logs))
        self.assertFalse(self.undo_file.exists())  # переносов не было — журналу неоткуда взяться

    def test_permission_error_on_mkdir_uses_folder_name_not_empty(self):
        plan = da.Plan()
        plan.steps = [da.Step("mkdir", dst=str(self.desktop / "Разобрать" / "Картинки"))]
        with mock.patch.object(da.Path, "mkdir", side_effect=PermissionError("denied")):
            da.execute(plan, self.log)
        self.assertTrue(any("Нет доступа" in m for m in self.logs))
        # раньше здесь была пустая строка вместо имени папки — регрессия не должна вернуться
        self.assertFalse(any(": : " in m or m.strip().endswith("ошибка на :") for m in self.logs))

    def test_generic_mkdir_error_names_the_target_not_empty_src(self):
        plan = da.Plan()
        plan.steps = [da.Step("mkdir", dst=str(self.desktop / "Разобрать" / "Видео"))]
        with mock.patch.object(da.Path, "mkdir", side_effect=RuntimeError("boom")):
            da.execute(plan, self.log)
        self.assertTrue(any("ошибка на Видео:" in m for m in self.logs),
                        "должно называть папку 'Видео', а не пустую строку: {}".format(self.logs))

    def test_continues_after_one_step_fails(self):
        good = touch(self.desktop / "good.txt")
        bad = touch(self.desktop / "bad.txt")
        plan = da.Plan()
        plan.steps = [da.Step("trash", src=str(bad)), da.Step("trash", src=str(good))]

        real_move = shutil.move
        def flaky(src, dst):
            if "bad.txt" in src:
                raise PermissionError("nope")
            return real_move(src, dst)

        with mock.patch.object(da.shutil, "move", side_effect=flaky):
            done = da.execute(plan, self.log)
        self.assertEqual(done, 1)
        self.assertTrue((self.trash / "good.txt").exists())
        self.assertTrue(bad.exists())


# ---------------------------------------------------------------------------
# undo_last()
# ---------------------------------------------------------------------------

class TestUndoLast(IsolatedRootsTestCase):
    def setUp(self):
        super().setUp()
        self.logs = []

    def log(self, msg):
        self.logs.append(msg)

    def test_nothing_to_undo(self):
        self.assertEqual(da.undo_last(self.log), 0)
        self.assertTrue(any("нечего" in m.lower() for m in self.logs))

    def test_corrupted_journal(self):
        self.undo_file.write_text("{not json", encoding="utf-8")
        self.assertEqual(da.undo_last(self.log), 0)
        self.assertTrue(any("повреждён" in m.lower() for m in self.logs))
        self.assertTrue(self.undo_file.exists())  # повреждённый файл не трогаем молча

    def test_full_roundtrip_restores_file_and_clears_journal(self):
        f = touch(self.desktop / "a.txt")
        plan = da.Plan()
        plan.steps = [da.Step("trash", src=str(f))]
        da.execute(plan, self.log)
        self.assertFalse(f.exists())

        restored = da.undo_last(self.log)
        self.assertEqual(restored, 1)
        self.assertTrue(f.exists())
        self.assertFalse(self.undo_file.exists())

    def test_failed_restore_keeps_entry_in_journal_for_retry(self):
        f = touch(self.desktop / "a.txt")
        plan = da.Plan()
        plan.steps = [da.Step("trash", src=str(f))]
        da.execute(plan, self.log)

        with mock.patch.object(da.shutil, "move", side_effect=PermissionError("denied")):
            restored = da.undo_last(self.log)
        self.assertEqual(restored, 0)
        self.assertTrue(self.undo_file.exists(),
                        "журнал не должен исчезать, если файл не восстановился")

        # теперь "чиним доступ" и повторяем — файл должен наконец вернуться
        restored2 = da.undo_last(self.log)
        self.assertEqual(restored2, 1)
        self.assertTrue(f.exists())
        self.assertFalse(self.undo_file.exists())

    def test_two_operations_can_be_undone_one_after_another(self):
        """Главное в многошаговом откате: вторая уборка не должна стирать
        память о первой."""
        first = touch(self.desktop / "первый.txt")
        plan1 = da.Plan(title="Первая уборка")
        plan1.steps = [da.Step("trash", src=str(first))]
        da.execute(plan1, self.log)

        second = touch(self.desktop / "второй.txt")
        plan2 = da.Plan(title="Вторая уборка")
        plan2.steps = [da.Step("trash", src=str(second))]
        da.execute(plan2, self.log)

        self.assertEqual(da.operations_left(), 2)

        self.assertEqual(da.undo_last(self.log), 1)
        self.assertTrue(second.exists())
        self.assertFalse(first.exists(), "первая уборка отменяется только вторым нажатием")
        self.assertEqual(da.operations_left(), 1)

        self.assertEqual(da.undo_last(self.log), 1)
        self.assertTrue(first.exists())
        self.assertEqual(da.operations_left(), 0)
        self.assertFalse(self.undo_file.exists())

    def test_undo_reports_which_operation_was_rolled_back(self):
        f = touch(self.desktop / "a.txt")
        plan = da.Plan(title="Разложить «Desktop» по папкам:")
        plan.steps = [da.Step("trash", src=str(f))]
        da.execute(plan, self.log)
        da.undo_last(self.log)
        self.assertTrue(any("Разложить «Desktop»" in m for m in self.logs))

    def test_journal_keeps_only_the_last_operations(self):
        for i in range(da.MAX_UNDO_OPERATIONS + 5):
            f = touch(self.desktop / "f{}.txt".format(i))
            plan = da.Plan(title="уборка {}".format(i))
            plan.steps = [da.Step("trash", src=str(f))]
            da.execute(plan, self.log)
        self.assertEqual(da.operations_left(), da.MAX_UNDO_OPERATIONS)
        titles = [op["title"] for op in da.load_operations()]
        self.assertEqual(titles[-1], "уборка {}".format(da.MAX_UNDO_OPERATIONS + 4))
        self.assertEqual(titles[0], "уборка 5")

    def test_same_operation_id_updates_instead_of_stacking(self):
        da.record_operation([{"from": "/a", "to": "/b"}], "заход", op_id="x1")
        da.record_operation([{"from": "/a", "to": "/b"},
                             {"from": "/c", "to": "/d"}], "заход", op_id="x1")
        operations = da.load_operations()
        self.assertEqual(len(operations), 1)
        self.assertEqual(len(operations[0]["moves"]), 2)

    def test_different_operation_ids_stack_up(self):
        da.record_operation([{"from": "/a", "to": "/b"}], "раз", op_id="x1")
        da.record_operation([{"from": "/c", "to": "/d"}], "два", op_id="x2")
        self.assertEqual(da.operations_left(), 2)

    def test_old_single_operation_journal_is_still_readable(self):
        """Журнал, оставшийся от предыдущей версии программы, не должен
        пропадать вместе с возможностью откатиться."""
        f = self.desktop / "старый.txt"
        moved = touch(self.trash / "старый.txt")
        self.undo_file.write_text(json.dumps(
            {"at": "2026-01-01T00:00:00", "moves": [{"from": str(f), "to": str(moved)}]},
            ensure_ascii=False), encoding="utf-8")
        self.assertEqual(da.operations_left(), 1)
        self.assertEqual(da.undo_last(self.log), 1)
        self.assertTrue(f.exists())

    def test_empty_operations_do_not_create_a_journal(self):
        da.record_operation([], "пусто")
        self.assertFalse(self.undo_file.exists())
        self.assertEqual(da.operations_left(), 0)

    def test_corrupted_journal_is_reported_and_left_alone(self):
        self.undo_file.write_text("[не тот формат]", encoding="utf-8")
        self.assertIsNone(da.load_operations())
        self.assertEqual(da.undo_last(self.log), 0)
        self.assertTrue(self.undo_file.exists())

    def test_already_missing_current_file_is_skipped_without_error(self):
        f = touch(self.desktop / "a.txt")
        plan = da.Plan()
        plan.steps = [da.Step("trash", src=str(f))]
        da.execute(plan, self.log)
        (self.trash / "a.txt").unlink()  # пользователь сам стёр файл из Корзины

        restored = da.undo_last(self.log)
        self.assertEqual(restored, 0)


# ---------------------------------------------------------------------------
# check_access / _step_label
# ---------------------------------------------------------------------------

class TestCheckAccess(IsolatedRootsTestCase):
    def test_reports_no_denied_folders_normally(self):
        self.assertEqual(da.check_access(), [])

    def test_detects_permission_denied_folder(self):
        with mock.patch.object(da.Path, "iterdir", side_effect=PermissionError("nope")):
            denied = da.check_access()
        self.assertEqual(set(denied), {self.desktop.name, self.downloads.name})


class TestStepLabel(unittest.TestCase):
    def test_mkdir_uses_dst_name(self):
        step = da.Step("mkdir", dst="/x/y/Картинки")
        self.assertEqual(da._step_label(step), "Картинки")

    def test_move_uses_src_name(self):
        step = da.Step("move", src="/x/a.txt", dst="/y/a.txt")
        self.assertEqual(da._step_label(step), "a.txt")

    def test_empty_paths_fall_back_to_placeholder(self):
        step = da.Step("mkdir", dst="")
        self.assertEqual(da._step_label(step), "?")


# ---------------------------------------------------------------------------
# AI-режим: ai_plan (ai_request мокается — сеть не трогаем)
# ---------------------------------------------------------------------------

class TestAiPlan(IsolatedRootsTestCase):
    def test_valid_plan_is_accepted(self):
        f = touch(self.desktop / "a.jpg")
        response = json.dumps({
            "comment": "раскладываю картинки",
            "steps": [
                {"op": "mkdir", "dst": str(self.desktop / "Разобрать" / "Картинки")},
                {"op": "move", "src": str(f),
                 "dst": str(self.desktop / "Разобрать" / "Картинки" / "a.jpg")},
            ],
        })
        with mock.patch.object(da, "ai_request", return_value=response):
            plan = da.ai_plan("разложи картинки")
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.comment, "раскладываю картинки")

    def test_strips_markdown_code_fences(self):
        response = "```json\n" + json.dumps({"comment": "ок", "steps": []}) + "\n```"
        with mock.patch.object(da, "ai_request", return_value=response):
            plan = da.ai_plan("что-нибудь")
        self.assertEqual(plan.comment, "ок")
        self.assertEqual(plan.steps, [])

    def test_disallowed_paths_are_rejected_and_counted(self):
        response = json.dumps({
            "comment": "план",
            "steps": [
                {"op": "trash", "src": "/etc/passwd"},
                {"op": "move", "src": "/etc/hosts", "dst": str(self.desktop / "hosts")},
                {"op": "mkdir", "dst": "/etc/newdir"},
            ],
        })
        with mock.patch.object(da, "ai_request", return_value=response):
            plan = da.ai_plan("сделай что-то подозрительное")
        self.assertEqual(plan.steps, [])
        self.assertIn("отклонено небезопасных шагов: 3", plan.comment)

    def test_move_to_nonexistent_source_is_rejected(self):
        response = json.dumps({
            "comment": "план",
            "steps": [{"op": "move", "src": str(self.desktop / "ghost.txt"),
                       "dst": str(self.desktop / "dst.txt")}],
        })
        with mock.patch.object(da, "ai_request", return_value=response):
            plan = da.ai_plan("перемести файл")
        self.assertEqual(plan.steps, [])

    def test_invalid_json_raises_clear_error(self):
        with mock.patch.object(da, "ai_request", return_value="это не json вообще"):
            with self.assertRaises(RuntimeError):
                da.ai_plan("что угодно")


# ---------------------------------------------------------------------------
# ai_request: сборка запроса и переключение модели/провайдера
# (сеть не трогаем — urlopen подменён, проверяем, что реально уходит наружу)
# ---------------------------------------------------------------------------

class FakeHTTPResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def capturing_urlopen(payload):
    """Возвращает (fake_urlopen, captured) — captured['req'] заполнится
    реальным urllib.request.Request, который построил ai_request()."""
    captured = {}

    def _fake(req, timeout=60, context=None):
        captured["req"] = req
        captured["context"] = context
        return FakeHTTPResponse(payload)

    return _fake, captured


class TestAiRequestModelSelection(IsolatedRootsTestCase):
    """Скрипт писал Opus с расчётом на то, что его же (или любую другую
    модель Anthropic) можно будет подключить позже одной переменной
    окружения, без правки кода. Эти тесты это доказывают, а не просто
    утверждают."""

    def test_default_anthropic_model_when_no_override(self):
        fake, captured = capturing_urlopen({"content": [{"text": "{}"}]})
        with mock.patch.multiple(da, AI_KEY="test-key", AI_PROVIDER="anthropic",
                                 AI_MODEL="", AI_BASE=""):
            with mock.patch.object(da.urllib.request, "urlopen", side_effect=fake):
                da.ai_request("тест")
        body = json.loads(captured["req"].data.decode("utf-8"))
        self.assertEqual(body["model"], "claude-sonnet-5")

    def test_switching_to_opus_via_env_var_reaches_the_request_body(self):
        fake, captured = capturing_urlopen({"content": [{"text": "{}"}]})
        with mock.patch.multiple(da, AI_KEY="test-key", AI_PROVIDER="anthropic",
                                 AI_MODEL="claude-opus-5", AI_BASE=""):
            with mock.patch.object(da.urllib.request, "urlopen", side_effect=fake):
                da.ai_request("тест")
        req = captured["req"]
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model"], "claude-opus-5")
        self.assertEqual(req.get_header("X-api-key"), "test-key")
        self.assertTrue(req.full_url.endswith("/v1/messages"))
        self.assertEqual(req.get_method(), "POST")

    def test_custom_anthropic_base_url_is_respected(self):
        fake, captured = capturing_urlopen({"content": [{"text": "ok"}]})
        with mock.patch.multiple(da, AI_KEY="k", AI_PROVIDER="anthropic",
                                 AI_MODEL="claude-opus-5",
                                 AI_BASE="https://my-proxy.example.com"):
            with mock.patch.object(da.urllib.request, "urlopen", side_effect=fake):
                result = da.ai_request("тест")
        self.assertEqual(captured["req"].full_url,
                         "https://my-proxy.example.com/v1/messages")
        self.assertEqual(result, "ok")

    def test_openrouter_via_openai_compatible_branch(self):
        fake, captured = capturing_urlopen(
            {"choices": [{"message": {"content": "{}"}}]})
        with mock.patch.multiple(da, AI_KEY="or-key", AI_PROVIDER="openai",
                                 AI_MODEL="anthropic/claude-opus-5",
                                 AI_BASE="https://openrouter.ai/api/v1"):
            with mock.patch.object(da.urllib.request, "urlopen", side_effect=fake):
                result = da.ai_request("тест")
        req = captured["req"]
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model"], "anthropic/claude-opus-5")
        self.assertEqual(req.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(req.get_header("Authorization"), "Bearer or-key")
        self.assertEqual(result, "{}")

    def test_answer_limit_is_12000_tokens_for_both_providers(self):
        # Было 2000: Sonnet 5 думает перед ответом, размышления входят в тот
        # же потолок, и на сложной просьбе план обрывался посреди JSON.
        for provider, payload in (("anthropic", {"content": [{"text": "{}"}]}),
                                  ("openai", {"choices": [{"message": {"content": "{}"}}]})):
            fake, captured = capturing_urlopen(payload)
            with mock.patch.multiple(da, AI_KEY="k", AI_PROVIDER=provider,
                                     AI_MODEL="", AI_BASE=""):
                with mock.patch.object(da.urllib.request, "urlopen", side_effect=fake):
                    da.ai_request("тест")
            body = json.loads(captured["req"].data.decode("utf-8"))
            self.assertEqual(body["max_tokens"], 12000, provider)

    def test_request_waits_long_enough_for_a_big_answer(self):
        seen = {}

        def fake(req, timeout=None, context=None):
            seen["timeout"] = timeout
            return FakeHTTPResponse({"content": [{"text": "{}"}]})

        with mock.patch.multiple(da, AI_KEY="k", AI_PROVIDER="anthropic",
                                 AI_MODEL="", AI_BASE=""):
            with mock.patch.object(da.urllib.request, "urlopen", side_effect=fake):
                da.ai_request("тест")
        self.assertGreaterEqual(seen["timeout"], 180)

    def test_truncated_answer_is_reported_as_truncated_not_as_bad_json(self):
        for provider, payload in (
                ("anthropic", {"stop_reason": "max_tokens",
                               "content": [{"text": '{"steps": [{"op": "mo'}]}),
                ("openai", {"choices": [{"finish_reason": "length",
                                         "message": {"content": '{"steps": ['}}]})):
            fake, _ = capturing_urlopen(payload)
            with mock.patch.multiple(da, AI_KEY="k", AI_PROVIDER=provider,
                                     AI_MODEL="", AI_BASE=""):
                with mock.patch.object(da.urllib.request, "urlopen", side_effect=fake):
                    with self.assertRaises(RuntimeError) as ctx:
                        da.ai_plan("разложи всё по проектам")
            self.assertIn("не поместился в лимит", str(ctx.exception), provider)
            self.assertNotIn("не JSON", str(ctx.exception), provider)

    def test_placeholder_key_from_the_guide_is_explained_not_sent(self):
        # В OPENROUTER.md стоит заглушка «sk-or-ТВОЙ_КЛЮЧ». Незаменённая, она
        # роняла запрос с «'latin-1' codec can't encode…» — пользователю не понять.
        for key in ("sk-or-ТВОЙ_КЛЮЧ", "sk-or-abc def"):
            with mock.patch.multiple(da, AI_KEY=key, AI_PROVIDER="openai",
                                     AI_MODEL="m", AI_BASE=""):
                with mock.patch.object(da.urllib.request, "urlopen") as m:
                    with self.assertRaises(RuntimeError) as ctx:
                        da.ai_request("тест")
                    m.assert_not_called()
            self.assertIn("заглушка", str(ctx.exception))
        with mock.patch.object(da, "AI_KEY", "sk-or-v1-0123abcd"):
            self.assertIsNone(da.ai_key_problem())

    def test_missing_key_fails_before_any_network_call(self):
        with mock.patch.multiple(da, AI_KEY=""):
            with mock.patch.object(da.urllib.request, "urlopen") as m:
                with self.assertRaises(RuntimeError):
                    da.ai_request("тест")
                m.assert_not_called()

    def test_http_error_from_provider_is_wrapped_clearly(self):
        def raise_http_error(req, timeout=60, context=None):
            raise da.urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", hdrs=None,
                fp=io.BytesIO(b"invalid x-api-key"))

        with mock.patch.multiple(da, AI_KEY="bad-key", AI_PROVIDER="anthropic",
                                 AI_MODEL="", AI_BASE=""):
            with mock.patch.object(da.urllib.request, "urlopen",
                                   side_effect=raise_http_error):
                with self.assertRaises(RuntimeError) as ctx:
                    da.ai_request("тест")
        self.assertIn("401", str(ctx.exception))


class TestHttpsCertificates(unittest.TestCase):
    """Python с python.org на Маке не видит системных сертификатов, пока не
    запустишь его «Install Certificates», а в Барсик.app их нет вовсе: запрос
    к ИИ падал на проверке сертификата. Берём корневые из самой macOS."""

    PEM = ("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"
           "-----BEGIN CERTIFICATE-----\nBBBB\n-----END CERTIFICATE-----\n")

    def test_system_root_certificates_are_split_one_by_one(self):
        def run(cmd, **kw):
            self.assertEqual(cmd[:3], ["security", "find-certificate", "-a"])
            return mock.Mock(stdout=self.PEM)
        certs = da.macos_root_certs(run)
        self.assertEqual(len(certs), 2)
        self.assertTrue(all(c.startswith("-----BEGIN") and
                            c.rstrip().endswith("-----END CERTIFICATE-----") for c in certs))

    def test_missing_security_tool_means_no_extra_certificates(self):
        def run(cmd, **kw):
            raise FileNotFoundError("security")
        self.assertEqual(da.macos_root_certs(run), [])

    def test_one_broken_certificate_does_not_spoil_the_rest(self):
        # не-ASCII внутри PEM даёт TypeError, а не SSLError — и раньше рушил
        # загрузку всех остальных сертификатов
        real = self._some_real_certificate()
        with mock.patch.object(da.sys, "platform", "darwin"), \
                mock.patch.object(da, "macos_root_certs",
                                  return_value=["кривой", "garbage", real]):
            with mock.patch.object(da.ssl, "create_default_context",
                                   side_effect=lambda: da.ssl.SSLContext(da.ssl.PROTOCOL_TLS_CLIENT)):
                ctx = da.ssl_context()
        self.assertEqual(ctx.verify_mode, da.ssl.CERT_REQUIRED)
        self.assertEqual(ctx.cert_store_stats()["x509_ca"], 1)

    def _some_real_certificate(self):
        cafile = da.ssl.get_default_verify_paths().cafile
        if not cafile or not os.path.exists(cafile):
            self.skipTest("в системе нет файла с сертификатами для образца")
        text = Path(cafile).read_text(encoding="ascii", errors="ignore")
        end = "-----END CERTIFICATE-----"
        first = text.split(end)[0]
        return first[first.index("-----BEGIN CERTIFICATE-----"):] + end + "\n"

    def test_ai_request_uses_the_certificate_aware_context(self):
        fake, captured = capturing_urlopen({"content": [{"text": "{}"}]})
        with mock.patch.multiple(da, AI_KEY="k", AI_PROVIDER="anthropic",
                                 AI_MODEL="", AI_BASE=""):
            with mock.patch.object(da.urllib.request, "urlopen", side_effect=fake):
                da.ai_request("тест")
        self.assertIsInstance(captured["context"], da.ssl.SSLContext)

    def test_error_answer_from_the_server_still_means_the_connection_works(self):
        def unauthorized(req, timeout=None, context=None):
            raise da.urllib.error.HTTPError(req.full_url, 401, "no key", hdrs=None,
                                            fp=io.BytesIO(b""))
        with mock.patch.object(da.urllib.request, "urlopen", side_effect=unauthorized):
            self.assertIsNone(da.ai_connection_problem())

    def test_certificate_failure_is_reported_with_its_reason(self):
        def bad_cert(req, timeout=None, context=None):
            raise da.urllib.error.URLError("CERTIFICATE_VERIFY_FAILED")
        with mock.patch.object(da.urllib.request, "urlopen", side_effect=bad_cert):
            self.assertIn("CERTIFICATE_VERIFY_FAILED", da.ai_connection_problem())


class TestAiSettingsFile(unittest.TestCase):
    """Приложение Барсик.app, запущенное двойным кликом, не видит переменных
    из ~/.zshrc, поэтому ключ можно положить в файл настроек."""

    def _read(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ai"
            p.write_text(text, encoding="utf-8")
            return da.read_ai_settings(p)

    def test_the_same_lines_as_for_zshrc_are_understood(self):
        got = self._read(
            "# мой ключ\n"
            "export ASSISTANT_PROVIDER=openai\n"
            "export OPENAI_API_KEY='sk-or-123'\n"
            'ASSISTANT_BASE_URL="https://openrouter.ai/api/v1"\n'
            "\n"
            "ASSISTANT_MODEL = anthropic/claude-sonnet-5  \n")
        self.assertEqual(got, {
            "ASSISTANT_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-or-123",
            "ASSISTANT_BASE_URL": "https://openrouter.ai/api/v1",
            "ASSISTANT_MODEL": "anthropic/claude-sonnet-5",
        })

    def test_unknown_names_and_garbage_lines_are_ignored(self):
        got = self._read("PATH=/evil\nпросто текст\nANTHROPIC_API_KEY=\n"
                         "ANTHROPIC_API_KEY=sk-ant-1\n")
        self.assertEqual(got, {"ANTHROPIC_API_KEY": "sk-ant-1"})

    def test_missing_or_unreadable_file_means_no_settings(self):
        self.assertEqual(da.read_ai_settings("/нет/такого/файла"), {})
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ai"
            p.write_bytes(b"\xff\xfe\x00 not utf8 \xff")
            self.assertEqual(da.read_ai_settings(p), {})

    def test_environment_wins_over_the_file(self):
        got = da.resolve_ai_settings(
            {"ASSISTANT_MODEL": "claude-haiku-4-5", "ASSISTANT_PROVIDER": ""},
            {"ASSISTANT_MODEL": "claude-sonnet-5", "ASSISTANT_PROVIDER": "openai"})
        self.assertEqual(got["ASSISTANT_MODEL"], "claude-haiku-4-5")
        # пустая переменная окружения не затирает значение из файла
        self.assertEqual(got["ASSISTANT_PROVIDER"], "openai")

    def test_settings_file_lives_in_the_home_folder(self):
        self.assertEqual(da.AI_SETTINGS_FILE, Path.home() / ".desktop_assistant_ai")


# ---------------------------------------------------------------------------
# is_dark_mode / open_privacy_settings — не должны падать без macOS
# ---------------------------------------------------------------------------

class TestMacOnlyShellouts(unittest.TestCase):
    def test_is_dark_mode_false_when_defaults_missing(self):
        with mock.patch.object(da.subprocess, "run", side_effect=FileNotFoundError()):
            self.assertFalse(da.is_dark_mode())

    def test_is_dark_mode_true_when_output_says_dark(self):
        fake = mock.Mock(stdout="Dark\n")
        with mock.patch.object(da.subprocess, "run", return_value=fake):
            self.assertTrue(da.is_dark_mode())

    def test_is_dark_mode_false_on_light_theme(self):
        fake = mock.Mock(stdout="")
        with mock.patch.object(da.subprocess, "run", return_value=fake):
            self.assertFalse(da.is_dark_mode())

    def test_open_privacy_settings_does_not_raise_without_open_command(self):
        with mock.patch.object(da.subprocess, "run", side_effect=FileNotFoundError()):
            da.open_privacy_settings()  # не должно бросать исключение


# ---------------------------------------------------------------------------
# Cat — прогоняем анимацию через все состояния на фейковом canvas,
# без реального Tk-окна, чтобы поймать любые runtime-ошибки в отрисовке.
# ---------------------------------------------------------------------------

class FakeCanvas:
    """Достаточно duck-typed канваса, чтобы Cat ничего не заметил."""

    def __init__(self, w=640, h=155):
        self._w, self._h = w, h
        self.calls = 0

    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h

    def delete(self, *a, **k):
        pass

    def _record(self, *a, **k):
        self.calls += 1

    create_rectangle = _record
    create_oval = _record
    create_polygon = _record
    create_line = _record
    create_text = _record
    create_arc = _record


class TestCatAnimation(unittest.TestCase):
    def _run_cycle(self, palette):
        canvas = FakeCanvas()
        cat = da.Cat(canvas, palette)
        for state, caption in (("thinking", "прикидываю…"),
                               ("work", "убираю…"),
                               ("happy", "готово!"),
                               ("idle", "жду команду")):
            cat.set_state(state, caption)
            for _ in range(80):
                cat.tick()
        return cat, canvas

    def test_full_cycle_light_palette_does_not_raise(self):
        cat, canvas = self._run_cycle(da.LIGHT)
        self.assertGreater(canvas.calls, 0)

    def test_full_cycle_dark_palette_does_not_raise(self):
        cat, canvas = self._run_cycle(da.DARK)
        self.assertGreater(canvas.calls, 0)

    def test_work_state_eventually_delivers_papers(self):
        canvas = FakeCanvas()
        cat = da.Cat(canvas, da.LIGHT)
        cat.set_state("work", "убираю…")
        for _ in range(200):
            cat.tick()
        self.assertGreater(cat.delivered, 0)

    def test_sparks_are_capped_in_happy_state(self):
        canvas = FakeCanvas()
        cat = da.Cat(canvas, da.LIGHT)
        cat.set_state("happy", "готово!")
        for _ in range(300):
            cat.tick()
            self.assertLessEqual(len(cat.sparks), 9)

    def test_switching_state_resets_particles_and_counters(self):
        canvas = FakeCanvas()
        cat = da.Cat(canvas, da.LIGHT)
        cat.set_state("work", "убираю…")
        for _ in range(200):
            cat.tick()
        self.assertGreater(cat.delivered, 0)
        cat.set_state("idle", "жду команду")
        self.assertEqual(cat.delivered, 0)
        self.assertEqual(cat.papers, [])
        self.assertEqual(cat.sparks, [])
        self.assertEqual(cat.dust, [])

    def test_tiny_canvas_size_does_not_crash(self):
        # winfo_width/height могут вернуть 1 до первой отрисовки в Tk —
        # Cat._size() должен подставлять разумный минимум.
        canvas = FakeCanvas(w=1, h=1)
        cat = da.Cat(canvas, da.LIGHT)
        cat.set_state("work", "убираю…")
        for _ in range(30):
            cat.tick()
        self.assertGreaterEqual(cat._size()[0], 320)
        self.assertGreaterEqual(cat._size()[1], 120)


if __name__ == "__main__":
    unittest.main(verbosity=2)
