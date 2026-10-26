"""Снимок Барсика с гортензией в зубах на настоящем Маке. Для CI (job «look»).

    python3 tools/mac/carry.py <папка для снимков>

Барсик ставится в заданную позу (несёт цветок, вправо и влево; кладёт цветок)
и не двигается: так на снимке видно, где цветок относительно морды.
"""
import subprocess
import sys
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE / "versions"))
import desktop_cat as dc  # noqa: E402
import desktop_barsik as dr  # noqa: E402

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

root = tk.Tk()
app = dr.BarsikApp(root, dr.load_meta(), demo=True, flower=dr.load_flower())
app.alive = False                       # свой цикл не крутим: поза замерла
b = app.brain
print("слои macOS у Барсика:", app.cat.artist.native is not None, flush=True)

poses = [("carry-right", dc.CARRY, 1, 6), ("carry-left", dc.CARRY, -1, 6),
         ("present-right", dr.PRESENT, 1, 6), ("walk-right-noflower", dc.WALK, 1, 6)]
for name, state, facing, frame in poses:
    b.x, b.y = 700, 600
    b.state, b.facing, b.frame, b.t = state, facing, frame, frame
    b.phase = 1.2
    b.carrying = None if "noflower" in name else dr.FLOWER
    b.say = ""
    app.cat.render(b)
    app.bubble.update("", 0, 0)
    for _ in range(10):
        root.update()
    root.after(300)
    root.update()
    x, y = app.cat.win.winfo_rootx(), app.cat.win.winfo_rooty()
    path = OUT / ("carry-%s.png" % name)
    # область окна Барсика (с запасом), в точках экрана
    subprocess.run(["screencapture", "-x", "-R%d,%d,%d,%d" % (x - 20, y - 20, app.cat.W + 40,
                                                            app.cat.H + 40), str(path)])
    print("снимок", name, "окно", x, y, flush=True)
root.destroy()
