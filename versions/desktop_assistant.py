#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ассистент рабочего стола для macOS.

Окошко, куда пишешь команду по-русски. Две версии в одном файле:

  1) РЕЖИМ ПРАВИЛ  — работает сразу, без интернета и без ключа.
                     Понимает фиксированный набор команд (см. help).

  2) РЕЖИМ ИИ      — включается сам, если задан ключ в переменной окружения.
                     Понимает команды свободным текстом.

Безопасность:
  - ничего не удаляется навсегда, всё уходит в Корзину;
  - перед выполнением показывается план и спрашивается подтверждение;
  - трогать можно только папки из ALLOWED_ROOTS;
  - последнюю операцию можно откатить кнопкой «Отменить последнее».

Про доступ к папкам на macOS:
  Начиная с Catalina доступ к Рабочему столу и Загрузкам защищён системой
  (Конфиденциальность и безопасность → Файлы и папки). Если разрешение не
  выдано, ОС бросает PermissionError при попытке заглянуть в папку — это
  не баг программы, а системное ограничение. Ниже это отлавливается и
  показывается понятным текстом вместо сырого системного сообщения.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import math
import queue
import random
import threading
import time
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

HOME = Path.home()
DESKTOP = HOME / "Desktop"
DOWNLOADS = HOME / "Downloads"
TRASH = HOME / ".Trash"

# Ассистенту разрешено трогать файлы только внутри этих папок.
ALLOWED_ROOTS = [DESKTOP, DOWNLOADS]

# Куда раскладываются файлы при уборке
SORT_ROOT_NAME = "Разобрать"

UNDO_FILE = HOME / ".desktop_assistant_undo.json"

# Сколько последних операций помнит журнал отката — столько раз подряд
# можно нажать «Отменить».
MAX_UNDO_OPERATIONS = 20

CATEGORIES = {
    "Картинки": {".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp",
                 ".tiff", ".bmp", ".svg", ".raw"},
    "Документы": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".pages", ".odt",
                  ".md", ".xlsx", ".xls", ".numbers", ".csv", ".pptx", ".key",
                  ".epub"},
    "Архивы": {".zip", ".rar", ".7z", ".tar", ".gz", ".dmg", ".pkg", ".iso"},
    "Видео": {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"},
    "Аудио": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"},
    "Код": {".py", ".js", ".ts", ".html", ".css", ".json", ".sh", ".sql"},
}

SCREENSHOT_RE = re.compile(
    r"^(снимок экрана|снимок|screenshot|screen shot|cleanshot|записьэкрана|запись экрана)",
    re.IGNORECASE,
)

# Настройки ИИ-режима — переменные окружения или файл настроек, см. README.
# Файл нужен приложению Барсик.app: запущенное двойным кликом, оно не видит
# переменных из ~/.zshrc. Переменные окружения важнее файла.
AI_SETTINGS_FILE = HOME / ".desktop_assistant_ai"
AI_SETTING_NAMES = ("ASSISTANT_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "ASSISTANT_MODEL", "ASSISTANT_BASE_URL")


def read_ai_settings(path, names=AI_SETTING_NAMES):
    """Строки вида KEY=VALUE. Можно вставить те же строки, что для ~/.zshrc:
    `export` в начале и кавычки вокруг значения допустимы, # — комментарий.
    names — какие ключи брать: тот же файл хранит и настройки вида Барсика."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    found = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if sep and key in names and value:
            found[key] = value
    return found


def resolve_ai_settings(environ, from_file):
    settings = dict(from_file)
    settings.update({k: environ[k] for k in AI_SETTING_NAMES if environ.get(k)})
    return settings


_AI = resolve_ai_settings(os.environ, read_ai_settings(AI_SETTINGS_FILE))
AI_PROVIDER = _AI.get("ASSISTANT_PROVIDER", "anthropic").lower()
AI_KEY = _AI.get("ANTHROPIC_API_KEY") or _AI.get("OPENAI_API_KEY") or ""
AI_MODEL = _AI.get("ASSISTANT_MODEL", "")
AI_BASE = _AI.get("ASSISTANT_BASE_URL", "")
# Потолок ответа модели. Было 2000, но Sonnet 5 по умолчанию думает перед
# ответом, и размышления входят в тот же потолок: на сложной просьбе он
# упирался в него посреди плана, и JSON обрывался.
AI_MAX_TOKENS = 12000
# Ответ приходит целиком, когда модель закончит; с большим потолком на это
# может уйти больше минуты.
AI_TIMEOUT = 180

# Текст-подсказка при отказе в доступе к папке (TCC на macOS).
PRIVACY_HINT = (
    "Нет доступа к папке «{name}».\n"
    "Открой Системные настройки → Конфиденциальность и безопасность → "
    "Файлы и папки, и разреши программе, из которой запущен скрипт "
    "(Terminal, iTerm2 или сам Python), доступ к «Рабочему столу» "
    "и «Загрузкам»."
)


def open_privacy_settings():
    """Открыть системные настройки приватности (лучшее, что можно сделать
    без диалога — Apple не даёт открывать раздел «Файлы и папки» напрямую)."""
    try:
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy"],
            timeout=3)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# План операций
# ---------------------------------------------------------------------------

@dataclass
class Step:
    op: str                 # move | trash | mkdir
    src: str = ""
    dst: str = ""
    note: str = ""


@dataclass
class Plan:
    title: str = ""
    steps: list = field(default_factory=list)
    comment: str = ""

    def describe(self, limit=40):
        if not self.steps:
            return "Нечего делать — подходящих файлов не нашлось."
        lines = [self.title or "План:"]
        if self.comment:
            lines.append("  " + self.comment)
        shown = self.steps[:limit]
        for s in shown:
            if s.op == "trash":
                lines.append("  🗑  в Корзину: " + Path(s.src).name)
            elif s.op == "move":
                parent = Path(s.dst).parent
                try:
                    where = str(parent.relative_to(HOME))
                except ValueError:
                    where = str(parent)
                lines.append("  →  " + Path(s.src).name + "  в  " + where)
            elif s.op == "mkdir":
                lines.append("  📁  создать папку " + Path(s.dst).name)
        if len(self.steps) > limit:
            lines.append("  … и ещё " + str(len(self.steps) - limit))
        return "\n".join(lines)

    def count_files(self):
        return len([s for s in self.steps if s.op in ("move", "trash")])


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

def is_allowed(path):
    """Файл лежит внутри разрешённых папок?"""
    try:
        p = Path(path).resolve()
    except OSError:
        return False
    for root in ALLOWED_ROOTS:
        try:
            p.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def visible_files(folder):
    """Файлы в папке, без скрытых и без самой папки 'Разобрать'.

    Если macOS не дала доступ к папке (TCC), iterdir() бросает
    PermissionError — сознательно не глушим её здесь, чтобы наверху
    (в App.run) показать пользователю понятную подсказку вместо того,
    чтобы молча вернуть пустой список и создать видимость, что "всё
    убрано", хотя на самом деле ничего не проверялось.
    """
    if not folder.is_dir():
        return []
    out = []
    for p in folder.iterdir():
        if p.name.startswith("."):
            continue
        if p.name in (SORT_ROOT_NAME,):
            continue
        if p.is_file():
            out.append(p)
    return sorted(out)


def unique_path(dest):
    """Если файл с таким именем есть — добавить (2), (3)…"""
    dest = Path(dest)
    if not dest.exists():
        return dest
    stem, suffix, n = dest.stem, dest.suffix, 2
    while True:
        cand = dest.with_name(stem + " (" + str(n) + ")" + suffix)
        if not cand.exists():
            return cand
        n += 1


def age_days(path):
    try:
        return (time.time() - path.stat().st_mtime) / 86400.0
    except OSError:
        return 0.0


def category_of(path):
    ext = path.suffix.lower()
    for name, exts in CATEGORIES.items():
        if ext in exts:
            return name
    return "Прочее"


def file_hash(path, chunk=1024 * 1024):
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            while True:
                block = f.read(chunk)
                if not block:
                    break
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Исполнитель плана
# ---------------------------------------------------------------------------

def _step_label(step):
    """Имя, которое стоит показать в логе для этого шага (для mkdir своего
    файла нет — берём имя создаваемой папки, а не пустой step.src)."""
    path = step.dst if step.op == "mkdir" else step.src
    return Path(path).name or "?"


def apply_step(step, log):
    """Выполнить ОДИН шаг плана.

    Возвращает запись для журнала отката ({"from":…, "to":…}), если файл
    реально переехал, иначе None. Отдельная функция нужна версии 2: там
    кот несёт файлы по одному, и операция должна происходить ровно в тот
    момент, когда он дошёл до папки, а не пачкой в конце.
    """
    try:
        if step.op == "mkdir":
            if not is_allowed(step.dst):
                log("  пропущено (папка вне разрешённых): " + step.dst)
                return None
            Path(step.dst).mkdir(parents=True, exist_ok=True)
            return None

        src = Path(step.src)
        if not src.exists():
            return None
        if not is_allowed(src):
            log("  пропущено (вне разрешённых папок): " + src.name)
            return None

        if step.op == "trash":
            TRASH.mkdir(exist_ok=True)
            dst = unique_path(TRASH / src.name)
        else:
            dst = Path(step.dst)
            if not is_allowed(dst):
                log("  пропущено (цель вне разрешённых папок): " + src.name)
                return None
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst = unique_path(dst)

        shutil.move(str(src), str(dst))
        return {"from": str(src), "to": str(dst)}
    except PermissionError:
        where = Path(step.dst if step.op == "mkdir" else step.src).parent
        log("  " + PRIVACY_HINT.format(name=where.name))
        return None
    except Exception as exc:
        log("  ошибка на " + _step_label(step) + ": " + str(exc))
        return None


def load_operations():
    """Стопка операций из журнала, самая старая первой.

    Возвращает [] если журнала нет, и None если он испорчен — это разные
    вещи: в первом случае отменять нечего, во втором надо сказать, что
    журнал сломан, и ничего не трогать.
    """
    if not UNDO_FILE.exists():
        return []
    try:
        data = json.loads(UNDO_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if "operations" in data:
        operations = data["operations"]
    elif "moves" in data:
        operations = [data]     # журнал старого формата — одна операция
    else:
        return None
    if not isinstance(operations, list):
        return None
    return [op for op in operations if isinstance(op, dict) and op.get("moves")]


def save_operations(operations):
    """Записать стопку, оставив только последние MAX_UNDO_OPERATIONS."""
    kept = list(operations)[-MAX_UNDO_OPERATIONS:]
    try:
        if kept:
            UNDO_FILE.write_text(
                json.dumps({"version": 2, "operations": kept},
                           ensure_ascii=False, indent=1),
                encoding="utf-8")
        elif UNDO_FILE.exists():
            UNDO_FILE.unlink()
    except OSError:
        pass


def record_operation(moves, title="", op_id=None):
    """Запомнить операцию для отката.

    op_id нужен версии 2: она дописывает файлы по одному, пока кошка их
    носит, и должна обновлять свою текущую операцию, а не плодить по
    записи на каждый файл.
    """
    if not moves:
        return
    operations = load_operations() or []
    stamp = datetime.now().isoformat()
    entry = {"at": stamp, "title": title, "id": op_id or stamp,
             "moves": list(moves)}
    if op_id and operations and operations[-1].get("id") == op_id:
        operations[-1] = entry
    else:
        operations.append(entry)
    save_operations(operations)


def operations_left():
    """Сколько операций ещё можно отменить."""
    return len(load_operations() or [])


def execute(plan, log):
    journal = []
    for step in plan.steps:
        entry = apply_step(step, log)
        if entry:
            journal.append(entry)
    record_operation(journal, plan.title)
    return len(journal)


def undo_last(log):
    """Отменить последнюю операцию. Нажимать можно несколько раз подряд —
    журнал помнит последние MAX_UNDO_OPERATIONS заходов."""
    operations = load_operations()
    if operations is None:
        log("Журнал повреждён.")
        return 0
    if not operations:
        log("Отменять нечего.")
        return 0

    operation = operations.pop()
    restored = 0
    failed = []
    for mv in reversed(operation.get("moves", [])):
        cur, orig = Path(mv["to"]), Path(mv["from"])
        if not cur.exists():
            continue
        try:
            orig.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(cur), str(unique_path(orig)))
            restored += 1
        except PermissionError:
            log("  " + PRIVACY_HINT.format(name=orig.parent.name))
            failed.append(mv)
        except Exception as exc:
            log("  не вернулось: " + cur.name + " (" + str(exc) + ")")
            failed.append(mv)

    if failed:
        # часть файлов не вернулась — операция остаётся в стопке, чтобы
        # можно было повторить откат позже (например, выдав доступ к папке)
        operation["moves"] = list(reversed(failed))
        operations.append(operation)

    save_operations(operations)
    if operation.get("title"):
        log("Отменено: " + operation["title"])
    return restored


def check_access():
    """Быстрая проверка, что папки из ALLOWED_ROOTS реально читаются.

    os.access() тут не годится — TCC на macOS блокирует не на уровне
    unix-битов доступа, а перехватывает сам системный вызов, так что
    единственный надёжный способ проверить — попробовать прочитать
    содержимое папки и посмотреть, не прилетит ли PermissionError.
    """
    denied = []
    for root in ALLOWED_ROOTS:
        if not root.is_dir():
            continue
        try:
            next(root.iterdir(), None)
        except PermissionError:
            denied.append(root.name)
    return denied


# ---------------------------------------------------------------------------
# ВЕРСИЯ 1. Режим правил
# ---------------------------------------------------------------------------

def plan_sort(folder, older_than=None):
    root = folder / SORT_ROOT_NAME
    steps, cats = [], set()
    for f in visible_files(folder):
        if older_than is not None and age_days(f) < older_than:
            continue
        cat = category_of(f)
        cats.add(cat)
        steps.append(Step("move", str(f), str(root / cat / f.name)))
    plan = Plan(title="Разложить «" + folder.name + "» по папкам:")
    plan.steps = [Step("mkdir", dst=str(root / c)) for c in sorted(cats)] + steps
    return plan


def plan_screenshots(days):
    steps = []
    for folder in (DESKTOP, DOWNLOADS):
        for f in visible_files(folder):
            if SCREENSHOT_RE.match(f.name) and age_days(f) >= days:
                steps.append(Step("trash", str(f)))
    plan = Plan(title="Скриншоты старше " + str(days) + " дн. — в Корзину:")
    plan.steps = steps
    return plan


def plan_old(days, folder=None):
    folder = DESKTOP if folder is None else folder
    root = folder / SORT_ROOT_NAME / ("Старое до " + datetime.now().strftime("%d.%m.%Y"))
    steps = [Step("mkdir", dst=str(root))]
    for f in visible_files(folder):
        if age_days(f) >= days:
            steps.append(Step("move", str(f), str(root / f.name)))
    plan = Plan(title="Файлы старше " + str(days) + " дн. — в архивную папку:")
    plan.steps = steps if len(steps) > 1 else []
    return plan


def plan_duplicates():
    by_size = {}
    for folder in (DESKTOP, DOWNLOADS):
        for f in visible_files(folder):
            try:
                by_size.setdefault(f.stat().st_size, []).append(f)
            except OSError:
                pass

    steps = []
    for size, group in by_size.items():
        if len(group) < 2 or size == 0:
            continue
        seen = {}
        for f in sorted(group, key=lambda p: p.stat().st_mtime):
            h = file_hash(f)
            if h is None:
                continue
            if h in seen:
                steps.append(Step("trash", str(f),
                                  note="копия " + seen[h].name))
            else:
                seen[h] = f
    plan = Plan(title="Дубликаты (оставляю самый старый файл):")
    plan.steps = steps
    return plan


def plan_empty_folders():
    steps = []
    for folder in (DESKTOP, DOWNLOADS):
        if not folder.is_dir():
            continue
        for p in folder.iterdir():
            if p.is_dir() and not p.name.startswith(".") and p.name != SORT_ROOT_NAME:
                try:
                    if not any(x for x in p.iterdir() if not x.name.startswith(".")):
                        steps.append(Step("trash", str(p)))
                except OSError:
                    pass
    plan = Plan(title="Пустые папки — в Корзину:")
    plan.steps = steps
    return plan


def parse_days(text, default):
    m = re.search(r"(\d+)\s*(дн|день|дня|дней|сут)", text)
    if m:
        return int(m.group(1))
    if "недел" in text:
        m2 = re.search(r"(\d+)\s*недел", text)
        return 7 * int(m2.group(1)) if m2 else 7
    if "месяц" in text:
        m3 = re.search(r"(\d+)\s*месяц", text)
        return 30 * int(m3.group(1)) if m3 else 30
    return default


def folder_from(text):
    """В какой папке работать — по упоминанию «загрузки» в команде."""
    return DOWNLOADS if ("загруз" in text or "download" in text) else DESKTOP


def rule_plan(text):
    """Разбор команды по ключевым словам. Возвращает Plan или None."""
    t = text.lower().strip()

    if "дубликат" in t or "повтор" in t:
        return plan_duplicates()

    if "пуст" in t and "папк" in t:
        return plan_empty_folders()

    # «сним» ловит и «снимок», и «снимки», и «снимков» — иначе команда
    # проваливалась в ветку «старше» и вместо скриншотов сгребала в архив
    # вообще все старые файлы
    if "скрин" in t or "сним" in t or "screenshot" in t:
        return plan_screenshots(parse_days(t, 7))

    if "старше" in t or "старьё" in t or "старье" in t:
        return plan_old(parse_days(t, 30), folder_from(t))

    clean_words = ("убер", "разлож", "сортир", "почист", "чист", "порядок",
                   "разбер", "прибер")
    if any(w in t for w in clean_words):
        return plan_sort(folder_from(t))

    return None


HELP_TEXT = """Что понимает режим правил:

  • убери рабочий стол          — разложить всё по папкам
  • разбери загрузки            — то же для папки «Загрузки»
  • удали скрины старше 7 дней  — скриншоты в Корзину
  • убери файлы старше 2 недель — в архивную папку с датой
  • найди дубликаты             — одинаковые файлы в Корзину
  • удали пустые папки

Числа и «дней / недель / месяцев» распознаются в любой из команд.
Свободный текст заработает после подключения ключа (см. README)."""


# ---------------------------------------------------------------------------
# ВЕРСИЯ 2. Режим ИИ
# ---------------------------------------------------------------------------

AI_SYSTEM = """Ты планировщик операций с файлами на macOS. Пользователь пишет
команду по-русски, ты возвращаешь план действий.

Отвечай ТОЛЬКО валидным JSON, без пояснений и без markdown-разметки:
{"comment": "одна строка что ты собираешься сделать", "steps": [ ... ]}

Шаг бывает трёх видов:
  {"op": "mkdir", "dst": "<абсолютный путь папки>"}
  {"op": "move",  "src": "<абсолютный путь>", "dst": "<абсолютный путь>"}
  {"op": "trash", "src": "<абсолютный путь>"}

Правила:
- используй ТОЛЬКО пути из списка файлов ниже, не выдумывай файлы;
- src и dst обязаны находиться внутри разрешённых папок;
- удаление делается только через "trash", никогда иначе;
- если для папки назначения нужен mkdir, поставь его раньше move;
- если команда непонятна или опасна, верни {"comment": "<почему>", "steps": []}."""


def folder_listing(limit=250):
    rows = []
    for folder in ALLOWED_ROOTS:
        for f in visible_files(folder)[:limit]:
            try:
                st = f.stat()
            except OSError:
                continue
            rows.append({
                "path": str(f),
                "kb": round(st.st_size / 1024),
                "changed": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
                "age_days": round(age_days(f)),
            })
    return rows


ROOT_CERTS = "/System/Library/Keychains/SystemRootCertificates.keychain"


def macos_root_certs(run=subprocess.run):
    """Корневые сертификаты macOS в виде PEM, по одному.

    Python с python.org их не видит: свои он ставит отдельным «Install
    Certificates», про который никто не знает, а в собранном Барсик.app их нет
    вовсе. Без них любой запрос к ИИ падал на проверке сертификата."""
    try:
        out = run(["security", "find-certificate", "-a", "-p", ROOT_CERTS],
                  capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    end = "-----END CERTIFICATE-----"
    return [chunk.strip() + "\n" + end + "\n" for chunk in out.split(end)
            if "-----BEGIN CERTIFICATE-----" in chunk]


def ssl_context():
    ctx = ssl.create_default_context()
    if sys.platform == "darwin":
        for pem in macos_root_certs():
            try:
                ctx.load_verify_locations(cadata=pem)
            except (ssl.SSLError, ValueError, TypeError):
                pass        # один кривой сертификат не должен ломать остальные
    return ctx


def ai_base_url():
    if AI_PROVIDER == "anthropic":
        return AI_BASE or "https://api.anthropic.com"
    return AI_BASE or "https://api.openai.com/v1"


def ai_connection_problem(timeout=10):
    """None, если до сервера ИИ можно достучаться по HTTPS, иначе причина.
    Ответ сервера с ошибкой (401 без ключа и т.п.) — это успех: связь есть,
    сертификат принят."""
    req = urllib.request.Request(ai_base_url(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()):
            return None
    except urllib.error.HTTPError:
        return None
    except (urllib.error.URLError, OSError) as exc:
        return str(getattr(exc, "reason", exc))


def ai_key_problem():
    """None, если ключ похож на ключ, иначе понятная причина.

    Ключи бывают только латиницей. Русские буквы почти наверняка значат,
    что в файле настроек осталась заглушка из инструкции («ТВОЙ_КЛЮЧ»);
    без этой проверки запрос падал с непонятным «'latin-1' codec…»."""
    if not AI_KEY:
        return "ключ не задан"
    if not AI_KEY.isascii() or any(c.isspace() for c in AI_KEY):
        return ("в ключе русские буквы или пробелы — похоже, вместо ключа "
                "осталась заглушка из инструкции")
    return None


def ai_request(user_text):
    """Запрос к модели. Возвращает текст ответа."""
    problem = ai_key_problem()
    if problem:
        raise RuntimeError(problem[0].upper() + problem[1:])

    context = {
        "сегодня": datetime.now().strftime("%Y-%m-%d"),
        "разрешённые_папки": [str(p) for p in ALLOWED_ROOTS],
        "папка_для_разбора": str(DESKTOP / SORT_ROOT_NAME),
        "файлы": folder_listing(),
    }
    prompt = ("Файлы и контекст:\n" + json.dumps(context, ensure_ascii=False) +
              "\n\nКоманда пользователя: " + user_text)

    if AI_PROVIDER == "anthropic":
        url = ai_base_url() + "/v1/messages"
        body = {
            "model": AI_MODEL or "claude-sonnet-5",
            "max_tokens": AI_MAX_TOKENS,
            "system": AI_SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "content-type": "application/json",
            "x-api-key": AI_KEY,
            "anthropic-version": "2023-06-01",
        }
    else:
        # любой OpenAI-совместимый провайдер: OpenAI, OpenRouter, DeepSeek, Ollama
        url = ai_base_url() + "/chat/completions"
        body = {
            "model": AI_MODEL or "gpt-4o-mini",
            "max_tokens": AI_MAX_TOKENS,
            "messages": [{"role": "system", "content": AI_SYSTEM},
                         {"role": "user", "content": prompt}],
        }
        headers = {
            "content-type": "application/json",
            "authorization": "Bearer " + AI_KEY,
        }

    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=AI_TIMEOUT, context=ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("Ответ сервера " + str(e.code) + ": " +
                           e.read().decode("utf-8", "replace")[:300])

    # Обрезанный ответ иначе всплывал как «модель ответила не JSON», и было
    # непонятно, что чинить.
    if AI_PROVIDER == "anthropic":
        cut = data.get("stop_reason") == "max_tokens"
    else:
        cut = (data.get("choices") or [{}])[0].get("finish_reason") == "length"
    if cut:
        raise RuntimeError("Ответ модели не поместился в лимит ({} токенов) — "
                           "попробуй попросить короче или по частям".format(AI_MAX_TOKENS))

    if AI_PROVIDER == "anthropic":
        return "".join(b.get("text", "") for b in data.get("content", []))
    return data["choices"][0]["message"]["content"]


def ai_plan(user_text):
    raw = ai_request(user_text).strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise RuntimeError("Модель ответила не JSON:\n" + raw[:300])

    plan = Plan(title="План от ИИ:", comment=parsed.get("comment", ""))
    skipped = 0
    for s in parsed.get("steps", []):
        op = s.get("op")
        src, dst = s.get("src", ""), s.get("dst", "")
        if op == "mkdir":
            if is_allowed(dst):
                plan.steps.append(Step("mkdir", dst=dst))
            else:
                skipped += 1
        elif op == "trash":
            if is_allowed(src) and Path(src).exists():
                plan.steps.append(Step("trash", src=src))
            else:
                skipped += 1
        elif op == "move":
            if is_allowed(src) and is_allowed(dst) and Path(src).exists():
                plan.steps.append(Step("move", src=src, dst=dst))
            else:
                skipped += 1
    if skipped:
        plan.comment += "  (отклонено небезопасных шагов: " + str(skipped) + ")"
    return plan


# ---------------------------------------------------------------------------
# Котик
# ---------------------------------------------------------------------------

def is_dark_mode():
    """Тёмная тема в macOS включена?"""
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                             capture_output=True, text=True, timeout=2)
        return "dark" in out.stdout.lower()
    except Exception:
        return False


LIGHT = {
    "bg": "#f7f3ec", "floor": "#e7ddcb", "ink": "#3b2f26",
    "fur": "#e8a15d", "fur2": "#d4873f", "belly": "#fbe6cf",
    "ear": "#f2b9a0", "folder": "#f0c36a", "folder2": "#d9a94f",
    "paper": "#ffffff", "dust": "#cec3ae", "spark": "#f2a516",
    "broom": "#7d5330", "bristle": "#dfc48a", "text": "#6b5b4b",
}

DARK = {
    "bg": "#22252d", "floor": "#2d313b", "ink": "#eae4da",
    "fur": "#d1904f", "fur2": "#b0743a", "belly": "#f0d5b6",
    "ear": "#d49c8a", "folder": "#d9a94f", "folder2": "#b88c38",
    "paper": "#e3e7ee", "dust": "#474d59", "spark": "#f7c948",
    "broom": "#6b4526", "bristle": "#b89c66", "text": "#9aa0ab",
}


class Cat:
    """Рисует кота на Canvas. Каждый кадр перерисовывается целиком —
    фигур мало, на 20 кадрах в секунду это незаметно."""

    STEP_MS = 50

    def __init__(self, canvas, palette):
        self.c = canvas
        self.p = palette
        self.t = 0
        self.state = "idle"
        self.state_t = 0
        self.caption = "жду команду"
        self.cx = None
        self.papers = []
        self.sparks = []
        self.dust = []
        self.folder_pop = 0
        self.blink = 0
        self.next_blink = 40
        self.delivered = 0

    # --- состояния -------------------------------------------------------

    def set_state(self, state, caption=None):
        if state != self.state:
            self.state = state
            self.state_t = 0
            self.papers, self.sparks, self.dust = [], [], []
            self.delivered = 0
        if caption is not None:
            self.caption = caption

    # --- покадровая логика -----------------------------------------------

    def tick(self):
        self.t += 1
        self.state_t += 1
        self._update_cx()

        self.next_blink -= 1
        if self.next_blink <= 0:
            self.blink = 5
            self.next_blink = random.randint(45, 140)
        if self.blink > 0:
            self.blink -= 1
        if self.folder_pop > 0:
            self.folder_pop -= 1

        if self.state == "work":
            self._tick_work()
        elif self.state == "happy":
            self._tick_happy()

        self.draw()

    def _update_cx(self):
        """Кот смещается влево, когда работает, — чтобы освободить место
        под папку. Позиция нужна и частицам, поэтому считается до них."""
        w = self._size()[0]
        target = w * 0.33 if self.state == "work" else w * 0.5
        self.cx = target if self.cx is None else self.cx + (target - self.cx) * 0.12

    def _tick_work(self):
        w, h = self._size()
        base = h - 18

        # бумажки летят от кота к папке
        if self.state_t % 7 == 0 and len(self.papers) < 6:
            self.papers.append({"pos": 0.0, "spin": random.uniform(-0.25, 0.25),
                                "size": random.randint(7, 11)})
        for pa in list(self.papers):
            pa["pos"] += 0.035
            if pa["pos"] >= 1.0:
                self.papers.remove(pa)
                self.folder_pop = 7
                self.delivered += 1

        # пыль из-под метлы
        if self.state_t % 5 == 0:
            self.dust.append({"x": self.cx + 34 + random.randint(-6, 10),
                              "y": base - random.randint(0, 5),
                              "r": 1.5, "life": 12})
        for d in list(self.dust):
            d["life"] -= 1
            d["r"] += 0.32
            d["y"] -= 0.7
            d["x"] += 0.5
            if d["life"] <= 0:
                self.dust.remove(d)

    def _tick_happy(self):
        if self.state_t % 6 == 0 and len(self.sparks) < 9:
            self.sparks.append({
                "x": self.cx + random.randint(-46, 46),
                "y": self._size()[1] - 40 - random.randint(0, 52),
                "life": 20, "r": random.uniform(3, 6)})
        for s in list(self.sparks):
            s["life"] -= 1
            s["y"] -= 0.9
            if s["life"] <= 0:
                self.sparks.remove(s)

    # --- рисование --------------------------------------------------------

    def _size(self):
        w = max(self.c.winfo_width(), 320)
        h = max(self.c.winfo_height(), 120)
        return w, h

    def draw(self):
        c, p = self.c, self.p
        w, h = self._size()
        base = h - 18

        if self.cx is None:
            self._update_cx()
        cx = self.cx

        c.delete("all")
        c.create_rectangle(0, base + 2, w, h, fill=p["floor"], outline="")

        if self.state == "work":
            self._draw_folder(w * 0.74, base)

        # дыхание / подпрыгивание
        if self.state == "work":
            bob = math.sin(self.t / 2.2) * 2.5
        elif self.state == "happy":
            bob = -abs(math.sin(self.t / 4.0)) * 9
        else:
            bob = math.sin(self.t / 13.0) * 1.6

        self._draw_cat(cx, base, bob)

        if self.state == "work":
            self._draw_broom(cx, base, bob)
            for d in self.dust:
                c.create_oval(d["x"] - d["r"], d["y"] - d["r"],
                              d["x"] + d["r"], d["y"] + d["r"],
                              fill=p["dust"], outline="")
            for pa in self.papers:
                self._draw_paper(pa, cx, base, w)
        elif self.state == "thinking":
            self._draw_bubble(cx, base, bob)

        for s in self.sparks:
            self._draw_spark(s)

        c.create_text(w / 2, h - 5, text=self.caption, anchor="s",
                      fill=p["text"], font=("Helvetica", 11))

    def _draw_cat(self, cx, base, bob):
        c, p = self.c, self.p
        y = base + bob

        # тень
        c.create_oval(cx - 30, base - 5, cx + 30, base + 4,
                      fill=p["floor"], outline="")

        # хвост
        sw = math.sin(self.t / 7.0) * (11 if self.state != "idle" else 6)
        sg = -1 if self.state == "work" else 1     # метёт правой — хвост влево
        c.create_line(cx + sg * 19, y - 20,
                      cx + sg * (40 + sw * 0.6), y - 26 - abs(sw) * 0.3,
                      cx + sg * (46 + sw), y - 50 - abs(sw) * 0.5,
                      smooth=True, width=7, fill=p["fur2"], capstyle="round")

        # тело
        c.create_oval(cx - 26, y - 50, cx + 26, base + 2,
                      fill=p["fur"], outline=p["ink"], width=2)
        c.create_oval(cx - 14, y - 30, cx + 14, base - 1,
                      fill=p["belly"], outline="")

        # лапы
        for dx in (-15, 5):
            c.create_oval(cx + dx, base - 10, cx + dx + 11, base + 2,
                          fill=p["belly"], outline=p["ink"], width=1.5)

        # уши
        for sgn in (-1, 1):
            c.create_polygon(cx + sgn * 18, y - 80, cx + sgn * 13, y - 98,
                             cx + sgn * 3, y - 84,
                             fill=p["fur"], outline=p["ink"], width=2)
            c.create_polygon(cx + sgn * 15, y - 82, cx + sgn * 12, y - 92,
                             cx + sgn * 7, y - 84, fill=p["ear"], outline="")

        # голова
        c.create_oval(cx - 21, y - 90, cx + 21, y - 50,
                      fill=p["fur"], outline=p["ink"], width=2)

        # полоски
        for i, dx in enumerate((-8, 0, 8)):
            c.create_line(cx + dx - 3, y - 88 + i % 2, cx + dx + 3, y - 84,
                          fill=p["fur2"], width=2, capstyle="round")

        # глаза
        ey = y - 70
        if self.blink > 0:
            for sgn in (-1, 1):
                c.create_line(cx + sgn * 9 - 4, ey, cx + sgn * 9 + 4, ey,
                              fill=p["ink"], width=2, capstyle="round")
        else:
            for sgn in (-1, 1):
                ex = cx + sgn * 9
                c.create_oval(ex - 4, ey - 5, ex + 4, ey + 5,
                              fill=p["ink"], outline="")
                c.create_oval(ex - 1, ey - 4, ex + 2, ey - 1,
                              fill="#ffffff", outline="")

        # нос и рот
        ny = y - 61
        c.create_polygon(cx - 3, ny, cx + 3, ny, cx, ny + 3,
                         fill="#e0736e", outline="")
        c.create_arc(cx - 7, ny, cx, ny + 6, start=200, extent=140,
                     style="arc", outline=p["ink"], width=1.5)
        c.create_arc(cx, ny, cx + 7, ny + 6, start=200, extent=140,
                     style="arc", outline=p["ink"], width=1.5)

        # усы
        for sgn in (-1, 1):
            for dy in (-3, 1, 5):
                c.create_line(cx + sgn * 6, ny + dy * 0.5,
                              cx + sgn * 24, ny + dy,
                              fill=p["ink"], width=1)

    def _draw_broom(self, cx, base, bob):
        c, p = self.c, self.p
        swing = math.sin(self.t / 2.2) * 14
        hx, hy = cx + 20, base + bob - 58          # верх черенка, у плеча
        tx, ty = cx + 46 + swing, base             # метёлка на полу
        c.create_line(hx, hy, tx, ty, fill=p["broom"], width=5, capstyle="round")
        c.create_polygon(tx - 9, ty - 11, tx + 9, ty - 11, tx + 14, ty + 3,
                         tx - 14, ty + 3,
                         fill=p["bristle"], outline=p["ink"], width=1.5)
        for i in range(-2, 3):                     # прутья
            c.create_line(tx + i * 5, ty - 8, tx + i * 6, ty + 2,
                          fill=p["broom"], width=1)
        # лапа поверх черенка — будто держит
        px, py = hx + (tx - hx) * 0.3, hy + (ty - hy) * 0.3
        c.create_oval(px - 7, py - 6, px + 7, py + 6,
                      fill=p["belly"], outline=p["ink"], width=1.5)

    def _draw_paper(self, pa, cx, base, w):
        c, p = self.c, self.p
        x0, y0 = cx + 46, base - 14   # из-под метёлки
        x1, y1 = w * 0.74, base - 34
        t = pa["pos"]
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t - math.sin(t * math.pi) * 42
        s = pa["size"]
        tilt = pa["spin"] * 40 * (1 - t)
        c.create_polygon(x - s, y - s + tilt, x + s, y - s - tilt,
                         x + s, y + s - tilt, x - s, y + s + tilt,
                         fill=p["paper"], outline=p["ink"], width=1.5)
        c.create_line(x - s + 3, y - 2, x + s - 3, y - 2, fill=p["dust"], width=1)

    def _draw_folder(self, fx, base):
        c, p = self.c, self.p
        pop = self.folder_pop
        top = base - 46 - pop
        c.create_polygon(fx - 26, top + 6, fx - 10, top + 6, fx - 5, top,
                         fx + 26, top, fx + 26, top + 12, fx - 26, top + 12,
                         fill=p["folder2"], outline=p["ink"], width=2)
        c.create_rectangle(fx - 26, top + 10, fx + 26, base - 2,
                           fill=p["folder"], outline=p["ink"], width=2)
        if self.delivered:
            c.create_text(fx, base - 24, text=str(self.delivered),
                          fill=p["ink"], font=("Helvetica", 13, "bold"))

    def _draw_bubble(self, cx, base, bob):
        c, p = self.c, self.p
        y = base + bob - 96
        for i, r in enumerate((3, 5)):
            c.create_oval(cx + 18 + i * 8, y + 16 - i * 8 - r,
                          cx + 18 + i * 8 + r * 2, y + 16 - i * 8 + r,
                          fill=p["paper"], outline=p["ink"], width=1.5)
        c.create_oval(cx + 30, y - 16, cx + 78, y + 10,
                      fill=p["paper"], outline=p["ink"], width=2)
        phase = (self.t // 6) % 4
        for i in range(3):
            fill = p["ink"] if i < phase else p["dust"]
            c.create_oval(cx + 42 + i * 11, y - 6, cx + 48 + i * 11, y,
                          fill=fill, outline="")

    def _draw_spark(self, s):
        c, p = self.c, self.p
        r = s["r"] * (s["life"] / 20.0)
        x, y = s["x"], s["y"]
        c.create_polygon(x, y - r * 2, x + r * 0.5, y - r * 0.5, x + r * 2, y,
                         x + r * 0.5, y + r * 0.5, x, y + r * 2,
                         x - r * 0.5, y + r * 0.5, x - r * 2, y,
                         x - r * 0.5, y - r * 0.5,
                         fill=p["spark"], outline="")


# ---------------------------------------------------------------------------
# Окно
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.palette = DARK if is_dark_mode() else LIGHT

        root.title("Ассистент рабочего стола")
        root.geometry("660x640")
        root.minsize(560, 520)
        try:
            root.configure(bg=self.palette["bg"])
        except tk.TclError:
            pass

        # --- котик ---
        self.canvas = tk.Canvas(root, height=155, highlightthickness=0,
                                bg=self.palette["bg"])
        self.canvas.pack(fill="x", padx=0, pady=(0, 2))
        self.cat = Cat(self.canvas, self.palette)

        # --- ввод ---
        top = ttk.Frame(root)
        top.pack(fill="x", padx=12, pady=6)
        self.entry = ttk.Entry(top, font=("Helvetica", 14))
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda e: self.run())
        self.entry.focus()
        ttk.Button(top, text="Выполнить",
                   command=self.run).pack(side="left", padx=(8, 0))

        quick = ttk.Frame(root)
        quick.pack(fill="x", padx=12)
        for label, cmd in (("Убрать стол", "убери рабочий стол"),
                           ("Разобрать загрузки", "разбери загрузки"),
                           ("Скрины 7 дн.", "удали скрины старше 7 дней"),
                           ("Дубликаты", "найди дубликаты")):
            ttk.Button(quick, text=label,
                       command=lambda c=cmd: self.run(c)).pack(side="left",
                                                               padx=(0, 6))

        self.log_box = scrolledtext.ScrolledText(root, font=("Menlo", 12),
                                                 wrap="word", height=12)
        self.log_box.pack(fill="both", expand=True, padx=12, pady=6)

        bottom = ttk.Frame(root)
        bottom.pack(fill="x", padx=12, pady=(0, 10))
        ttk.Button(bottom, text="Отменить последнее",
                   command=self.undo).pack(side="left")
        ttk.Button(bottom, text="Что я умею",
                   command=lambda: self.log(HELP_TEXT)).pack(side="left", padx=6)
        ttk.Button(bottom, text="Настройки доступа",
                   command=open_privacy_settings).pack(side="left", padx=6)
        self.status = ttk.Label(bottom, text="")
        self.status.pack(side="right")

        self.busy = False
        self.mode_text = (("ИИ-режим включён (" + AI_PROVIDER + ")")
                          if AI_KEY else "режим правил")
        self.status.config(text=self.mode_text)
        self.log("Готов. Текущий режим: " + self.mode_text + ".")
        if not AI_KEY:
            self.log("Нажми «Что я умею», чтобы увидеть список команд.\n")

        self._check_startup_access()
        self.tick()

    def _check_startup_access(self):
        """Предупредить о нехватке прав ДО первой команды, а не после
        первой попытки что-то сделать — так понятнее, что происходит."""
        denied = check_access()
        if denied:
            self.log("⚠️  Нет доступа к папкам: " + ", ".join(denied) + ".")
            self.log(PRIVACY_HINT.format(name=" / ".join(denied)))
            self.log("Кнопка «Настройки доступа» откроет нужный раздел.\n")

    # --- цикл анимации и приём сообщений из потоков ----------------------

    def tick(self):
        """Единственное место, где что-то происходит в главном потоке."""
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log(payload)
            elif kind == "plan":
                self.present(payload)
            elif kind == "error":
                self.set_busy(False)
                self.cat.set_state(
                    "idle",
                    "не понял" if payload.startswith("Не понял") else "не получилось")
                self.log(payload)
            elif kind == "done":
                self.finish(payload)
        self.cat.tick()
        self.root.after(Cat.STEP_MS, self.tick)

    def log(self, text):
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")

    def set_busy(self, busy):
        self.busy = busy
        try:
            self.entry.config(state="disabled" if busy else "normal")
        except tk.TclError:
            pass
        if not busy:
            self.status.config(text=self.mode_text)

    # --- сценарий --------------------------------------------------------

    def run(self, preset=None):
        if self.busy:
            self.log("Погоди, ещё убираю.")
            return
        text = preset if preset is not None else self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.log("\n> " + text)

        self.set_busy(True)
        self.cat.set_state("thinking", "прикидываю…")
        self.status.config(text="думаю…")

        def worker():
            try:
                plan = rule_plan(text)
                if plan is None:
                    if not AI_KEY:
                        self.queue.put(("error",
                                        "Не понял команду. Нажми «Что я умею» — "
                                        "там список.\nСвободный текст заработает "
                                        "после подключения ключа."))
                        return
                    plan = ai_plan(text)
                self.queue.put(("plan", plan))
            except PermissionError:
                self.queue.put(("error",
                                PRIVACY_HINT.format(name="Рабочий стол / Загрузки")))
            except Exception as exc:
                self.queue.put(("error", "Не получилось: " + str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def present(self, plan):
        self.log(plan.describe())
        if plan.count_files() == 0:
            self.set_busy(False)
            self.cat.set_state("idle", "и так порядок")
            return

        if not messagebox.askyesno(
                "Подтверждение",
                "Выполнить? Затронуто файлов: " + str(plan.count_files()) +
                ".\nВсё удаляемое уходит в Корзину, откат — кнопкой внизу."):
            self.log("Отменено.")
            self.set_busy(False)
            self.cat.set_state("idle", "как скажешь")
            return

        self.cat.set_state("work", "убираю…")
        self.status.config(text="работаю…")

        def worker():
            done = execute(plan, lambda m: self.queue.put(("log", m)))
            # чтобы котик успел подмести, а не мигнул на один кадр
            time.sleep(max(0.0, 1.6 - done * 0.02))
            self.queue.put(("done", done))

        threading.Thread(target=worker, daemon=True).start()

    def finish(self, done):
        self.set_busy(False)
        self.log("Готово. Обработано файлов: " + str(done) + ".")
        self.cat.set_state("happy", "готово!")
        self.entry.focus()
        self.root.after(2600, lambda: self.cat.set_state("idle", "жду команду")
                        if not self.busy else None)

    def undo(self):
        if self.busy:
            self.log("Погоди, ещё убираю.")
            return
        self.cat.set_state("work", "возвращаю на место…")
        self.set_busy(True)

        def worker():
            n = undo_last(lambda m: self.queue.put(("log", m)))
            time.sleep(1.2)
            if n:
                left = operations_left()
                message = "Возвращено на место: " + str(n) + "."
                if left:
                    message += " Можно отменить ещё раз (осталось: " + str(left) + ")."
                self.queue.put(("log", message))
            self.queue.put(("done", n))

        threading.Thread(target=worker, daemon=True).start()


def main():
    if sys.platform != "darwin":
        print("Скрипт рассчитан на macOS.")
    root = tk.Tk()
    try:
        ttk.Style().theme_use("aqua")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
