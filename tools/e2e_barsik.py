"""Сквозная проверка версии 3 на настоящих файлах — всё, кроме глаз.

    python3 tools/e2e_barsik.py        (нужен экран: Мак или Xvfb)

Запускает настоящего Барсика (не демо) во временной «домашней папке» с
настоящими файлами и проходит путь, который пройдёт пользователь:

  1. «убери рабочий стол» → подтверждение → Барсик разносит файлы;
  2. «Вернуть как было» → всё на прежних местах, содержимое то же;
  3. свободная фраза → план от ИИ (поддельный сервер в стиле OpenRouter),
     среди шагов — опасный, программа обязана его отбросить;
  4. отмена плана от ИИ;
  5. гортензия: Барсик уходит за край экрана, приносит цветок, тот ложится
     на стол отдельным окном, щелчок по нему — цветок забрали.

Время ускорено (кадр 1 мс вместо 40), иначе Барсик носил бы файлы минуту.
Настоящую домашнюю папку скрипт не трогает: HOME подменяется до импорта
движка, и это проверяется, прежде чем что-то двигать.
"""
import hashlib
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

HOME = Path(tempfile.mkdtemp(prefix="barsik-e2e-"))
os.environ["HOME"] = str(HOME)
# до поддельного сервера ИИ на этой же машине — напрямую, мимо прокси
os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"
for name in ("ASSISTANT_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
             "ASSISTANT_MODEL", "ASSISTANT_BASE_URL"):
    os.environ.pop(name, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "versions"))
import tkinter as tk  # noqa: E402

import desktop_assistant as engine  # noqa: E402
import desktop_cat as dc  # noqa: E402
import desktop_barsik as dr  # noqa: E402

if not str(engine.DESKTOP).startswith(str(HOME)):
    sys.exit("СТОП: движок смотрит не во временную папку, а в " + str(engine.DESKTOP))

dc.FRAME_MS = 1
DESKTOP = engine.DESKTOP
FILES = ["Снимок экрана 2026-09-20 в 10.00.00.png", "договор.pdf", "котик.jpg",
         "отчёт.xlsx", "мем.gif", "заметки.txt", "архив.zip",
         "ремонт-смета.pdf", "ремонт-плитка.jpg"]
results = []


def check(what, ok, detail=""):
    results.append(ok)
    print(("  да   " if ok else "  НЕТ  ") + what + (("  — " + detail) if detail and not ok else ""),
          flush=True)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot():
    """{содержимое: путь} для всех файлов на столе (в том числе во вложенных)."""
    return {digest(p): p for p in DESKTOP.rglob("*") if p.is_file()}


DESKTOP.mkdir(parents=True)
(HOME / "Downloads").mkdir()
for i, name in enumerate(FILES):
    (DESKTOP / name).write_text("файл №%d: %s\n" % (i, name) * (i + 1), encoding="utf-8")
ORIGINAL = snapshot()

# --- поддельный сервер ИИ -----------------------------------------------------

seen_requests = []
AI_PLAN = {
    "comment": "складываю всё про ремонт в папку Ремонт",
    "steps": [
        {"op": "mkdir", "dst": str(DESKTOP / "Ремонт")},
        {"op": "move", "src": str(DESKTOP / "ремонт-смета.pdf"),
         "dst": str(DESKTOP / "Ремонт" / "ремонт-смета.pdf")},
        {"op": "move", "src": str(DESKTOP / "ремонт-плитка.jpg"),
         "dst": str(DESKTOP / "Ремонт" / "ремонт-плитка.jpg")},
        # опасный шаг: вынести файл за пределы разрешённых папок
        {"op": "move", "src": str(DESKTOP / "договор.pdf"),
         "dst": str(HOME / "Library" / "договор.pdf")},
    ],
}


class FakeOpenRouter(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen_requests.append((self.path, dict(self.headers), body))
        answer = {"choices": [{"finish_reason": "stop",
                               "message": {"content": json.dumps(AI_PLAN, ensure_ascii=False)}}]}
        data = json.dumps(answer).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


server = http.server.HTTPServer(("127.0.0.1", 0), FakeOpenRouter)
threading.Thread(target=server.serve_forever, daemon=True).start()

# --- прогон ---------------------------------------------------------------------

root = tk.Tk()
app = dr.BarsikApp(root, dr.load_meta(), demo=False, flower=dr.load_flower())
steps = []
if sys.platform == "darwin":
    # Tk 8.6 на Маке не рисует картинки на прозрачном фоне — их рисует macOS
    check("кадры Барсика рисует macOS (слои под окном)", app.cat.artist.native is not None)
    check("стекло панели рисует macOS", getattr(app.bar, "layers", None) is not None)


def wait(condition, then, timeout_ms=90000, what=""):
    left = [timeout_ms]

    def poll():
        if condition():
            then()
        elif left[0] <= 0:
            status = app.bar.canvas.itemcget(app.bar.status, "text")
            check("успел за отведённое время: " + what, False,
                  "застрял; Барсик говорит %r, на панели %r" % (app.brain.say, status))
            finish()
        else:
            left[0] -= 20
            root.after(20, poll)
    poll()


def confirm_when_asked(after_job):
    wait(lambda: app.confirm is not None,
         lambda: (app._confirm_yes(), wait(lambda: not app.busy, after_job, what="работа")),
         what="подтверждение")


def stage_rules():
    print("1. «убери рабочий стол»", flush=True)
    app.submit("убери рабочий стол")
    confirm_when_asked(after_rules)


def after_rules():
    now = snapshot()
    check("ни один файл не потерялся и не изменился", set(now) == set(ORIGINAL))
    loose = [p.name for p in DESKTOP.iterdir() if p.is_file()]
    check("на столе не осталось разбросанных файлов", not loose, ", ".join(loose))
    check("всё легло в папку «Разобрать»",
          all(engine.SORT_ROOT_NAME in p.parts for p in now.values()))
    check("ничего не ушло в Корзину", not (HOME / ".Trash").exists()
          or not any((HOME / ".Trash").iterdir()))
    print("2. «Вернуть как было»", flush=True)
    app.undo()
    wait(lambda: not app.busy, after_undo_rules, what="откат")


def after_undo_rules():
    now = snapshot()
    check("после отката каждый файл на своём месте с тем же содержимым",
          {k: str(v) for k, v in now.items()} == {k: str(v) for k, v in ORIGINAL.items()})
    stage_ai()


def stage_ai():
    print("3. свободная фраза через ИИ (поддельный OpenRouter)", flush=True)
    engine.AI_KEY = "sk-or-not-a-real-key"
    engine.AI_PROVIDER = "openai"
    engine.AI_BASE = "http://127.0.0.1:%d" % server.server_port
    engine.AI_MODEL = "anthropic/claude-sonnet-5"
    phrase = "сложи всё про ремонт в папку Ремонт"
    check("фразу не понимают правила — значит, спросят ИИ", engine.rule_plan(phrase) is None)
    app.submit(phrase)
    confirm_when_asked(after_ai)


def after_ai():
    check("запрос ушёл на сервер ИИ", len(seen_requests) == 1)
    if seen_requests:
        path, headers, body = seen_requests[0]
        check("адрес как у OpenRouter (/chat/completions)", path == "/chat/completions", path)
        check("модель из настроек", body.get("model") == "anthropic/claude-sonnet-5")
        check("потолок ответа 12000", body.get("max_tokens") == 12000)
        check("ключ передан", headers.get("Authorization") == "Bearer sk-or-not-a-real-key")
        check("в запросе список файлов со стола",
              "ремонт-смета.pdf" in body["messages"][1]["content"])
    repair = DESKTOP / "Ремонт"
    check("файлы про ремонт в папке «Ремонт»",
          (repair / "ремонт-смета.pdf").exists() and (repair / "ремонт-плитка.jpg").exists())
    check("опасный шаг отброшен: договор остался на столе",
          (DESKTOP / "договор.pdf").exists() and not (HOME / "Library" / "договор.pdf").exists())
    check("остальные файлы не тронуты",
          all((DESKTOP / n).exists() for n in FILES if not n.startswith("ремонт")))
    print("4. отмена плана от ИИ", flush=True)
    app.undo()
    wait(lambda: not app.busy, after_undo_ai, what="откат ИИ")


def after_undo_ai():
    now = snapshot()
    check("после отката всё снова на своих местах",
          {k: str(v) for k, v in now.items()} == {k: str(v) for k, v in ORIGINAL.items()})
    stage_flower()


def stage_flower():
    print("5. гортензия", flush=True)
    check("картинки гортензии на месте", app.flower is not None)
    if app.flower is None:
        return finish()
    app.brain.gift_due = True
    wait(lambda: app.brain.state == dr.AWAY, flower_away, what="уход за край экрана")


def flower_away():
    root.update_idletasks()
    x, w = app.cat.win.winfo_rootx(), app.cat.win.winfo_width()
    screen_w = root.winfo_screenwidth()
    # macOS мог бы не пустить окно за край — тогда Барсик «искал» бы цветок
    # у всех на виду
    check("окно Барсика и правда за краем экрана", x + w <= 0 or x >= screen_w,
          "окно на x=%d, ширина %d, экран %d" % (x, w, screen_w))
    wait(lambda: app.gift is not None, flower_laid, what="цветок принесён")


def flower_laid():
    root.update_idletasks()
    win = app.gift.win
    x, y = win.winfo_rootx(), win.winfo_rooty()
    check("цветок лежит на экране",
          0 <= x <= root.winfo_screenwidth() - win.winfo_width()
          and 0 <= y <= root.winfo_screenheight() - win.winfo_height(), "x=%d y=%d" % (x, y))
    check("Барсик вернулся на панель", (app.brain.x, app.brain.y) == app.screen.perch()
          or app.brain.state == dr.PRESENT)
    check("файлы гортензия не тронула", {k: str(v) for k, v in snapshot().items()}
          == {k: str(v) for k, v in ORIGINAL.items()})
    app.take_flower()
    check("щелчок — цветок забрали", app.gift is None)
    finish()


def finish():
    app.quit()


root.after(300, stage_rules)
root.mainloop()
server.shutdown()
shutil.rmtree(HOME, ignore_errors=True)

passed = sum(results)
print("\nпроверок пройдено: %d из %d" % (passed, len(results)))
sys.exit(0 if results and all(results) else 1)
