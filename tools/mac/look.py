"""Снимок экрана настоящего Мака: видно ли Барсик. Для CI (job «look»).

    python3 tools/mac/look.py <папка для снимков> [команда запуска Барсик...]

Открывает рядом пробные окна — картинка на прозрачном окне, на обычном,
фигуры, отражённая картинка, окно класса help — и снимает экран
`screencapture`. Если указана команда, ещё и запускает настоящего Барсика
(например, собранное приложение с --demo) и снимает его отдельно.
"""
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE / "versions"))
import desktop_cat as dc  # noqa: E402

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
FRAME = HERE / "versions" / "sprites" / "barsik" / "sit_00.png"


def shot(name):
    path = OUT / (name + ".png")
    r = subprocess.run(["screencapture", "-x", str(path)], capture_output=True, text=True)
    print("снимок", name, "код", r.returncode, r.stderr.strip(), flush=True)


root = tk.Tk()
root.withdraw()
img = tk.PhotoImage(master=root, file=str(FRAME))
flip = tk.PhotoImage(master=root)
root.tk.call(flip, "copy", img, "-subsample", -1, 1)
print("кадр", img.width(), "x", img.height(), flush=True)

variants = []


def probe(label, x, transparent=True, style=None, image=img, shapes=False):
    win = tk.Toplevel(root)
    win.withdraw()
    if style:
        try:
            root.tk.call("::tk::unsupported::MacWindowStyle", "style", str(win), *style)
        except tk.TclError as e:
            print(label, "style:", e)
    win.attributes("-topmost", True)
    bg = "#ffffff"
    if transparent:
        try:
            win.attributes("-transparent", True)
            win.configure(bg="systemTransparent")
            bg = "systemTransparent"
        except tk.TclError as e:
            print(label, "transparent:", e)
    c = tk.Canvas(win, width=280, height=240, highlightthickness=0, bd=0, bg=bg)
    c.pack()
    if shapes:
        c.create_oval(40, 40, 240, 200, fill="#e8a060", outline="#141414", width=3)
    else:
        c.create_image(140, 120, image=image)
    c.create_rectangle(4, 4, 276, 30, fill="#ffffff", outline="#141414")
    c.create_text(140, 17, text=label, fill="#141414", font=("Helvetica", 12, "bold"))
    win.geometry("280x240+{}+{}".format(x, 120))
    win.deiconify()
    variants.append(win)


probe("A прозр. + кадр", 20)
probe("B обычное + кадр", 320, transparent=False)
probe("C прозр. + фигуры", 620, shapes=True)
probe("D прозр. + отражённый", 920, image=flip)
probe("E help/noActivates + кадр", 20 + 0, style=("help", "noActivates"))
variants[-1].geometry("+20+400")
# сплошная картинка без прозрачных пикселей: рисует ли Tk картинки вообще
solid = tk.PhotoImage(master=root, width=160, height=120)
solid.put("#e8a060", to=(0, 0, 160, 120))
probe("G прозр. + сплошная", 620, image=solid)
variants[-1].geometry("+620+400")
probe("H обычное + сплошная", 920, transparent=False, image=solid)
variants[-1].geometry("+920+400")
# картинка в Label, а не на Canvas
lw = tk.Toplevel(root)
lw.attributes("-topmost", True)
try:
    lw.attributes("-transparent", True)
    lw.configure(bg="systemTransparent")
    lbg = "systemTransparent"
except tk.TclError:
    lbg = "#ffffff"
tk.Label(lw, image=img, bg=lbg, text="I Label + кадр", compound="top").pack()
lw.geometry("+1220+120")
print("Tk", root.tk.call("info", "patchlevel"), flush=True)
# где именно ломается: прозрачное окно или прозрачный фон canvas
def split(label, x, y, win_transparent, canvas_transparent):
    w = tk.Toplevel(root)
    w.attributes("-topmost", True)
    if win_transparent:
        w.attributes("-transparent", True)
        w.configure(bg="systemTransparent")
    c = tk.Canvas(w, width=200, height=170, highlightthickness=0, bd=0,
                  bg="systemTransparent" if canvas_transparent else "#9fcbe4")
    c.pack()
    c.create_image(100, 95, image=img)
    c.create_text(100, 12, text=label, fill="#141414", font=("Helvetica", 11, "bold"))
    w.geometry("+{}+{}".format(x, y))
split("J окно прозр, фон синий", 20, 700, True, False)
split("K окно обычн, фон прозр", 240, 700, False, True)
# как в программе: окно из make_overlay
win, transparent = dc.make_overlay(root, 280, 240)
c = tk.Canvas(win, width=280, height=240, highlightthickness=0, bd=0,
              bg="systemTransparent" if transparent else "#ffffff")
c.pack()
c.create_image(140, 120, image=img)
c.create_text(140, 17, text="F make_overlay + кадр", fill="#141414", font=("Helvetica", 12, "bold"))
win.geometry("+320+400")
win.deiconify()

for _ in range(30):
    root.update()
    time.sleep(0.05)
shot("1-probes")
root.destroy()

if len(sys.argv) > 2:
    cmd = sys.argv[2:]
    print("запускаю:", cmd, flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for i, wait in enumerate((4, 6, 8)):
        time.sleep(wait)
        shot("2-barsik-%d" % i)
    proc.terminate()
    try:
        out, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    print("вывод Барсика:\n" + (out or "(пусто)"), flush=True)
