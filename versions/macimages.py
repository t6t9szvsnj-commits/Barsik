"""Картинки в прозрачных окнах на Маке — силами самого macOS.

Tk 8.6 на macOS не рисует картинки на canvas с прозрачным фоном
(systemTransparent): фигуры и текст видно, картинку — нет. Так на Маке
пропал кот, стекло панели и гортензия, а CI проверял только, что кадры
загрузились. Снимки экрана в CI (tools/mac/look.py) это подтвердили: окно
обычное, фон синий — кот есть; фон прозрачный — пусто. Tk 9 картинки рисует,
но оставляет за котом след из старых кадров и не убирает рамку у панели.

Поэтому на Маке картинки кладутся слоями Core Animation в отдельный вид ПОД
вид Tk в том же окне. Tk рисует поверх (сердечки, «?», бумажку, кнопки), его
прозрачный фон пропускает картинку, а щелчки по-прежнему получает Tk.
Работает через Objective-C runtime из ctypes — без сторонних библиотек.

Если что-то не получилось (не Мак, нет окна, другая версия системы), всё
возвращает «нет», и программа рисует картинки, как раньше, через Tk.
"""

import ctypes
import ctypes.util
import sys

_objc = None
_send_addr = None
_protos = {}
_sels = {}
_images = {}            # общий кэш NSImage: ключ -> указатель


class _Rect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double),
                ("w", ctypes.c_double), ("h", ctypes.c_double)]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _Affine(ctypes.Structure):
    _fields_ = [(n, ctypes.c_double) for n in ("a", "b", "c", "d", "tx", "ty")]


def available():
    """Можно ли рисовать картинки слоями. Грузит библиотеки один раз."""
    global _objc, _send_addr
    if sys.platform != "darwin":
        return False
    if _objc is not None:
        return True
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc") or "/usr/lib/libobjc.dylib")
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/QuartzCore.framework/QuartzCore")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        _send_addr = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        _objc = objc
        return True
    except (OSError, AttributeError):
        return False


def _cls(name):
    return _objc.objc_getClass(name.encode())


def _send(obj, sel, *args, res=ctypes.c_void_p, types=()):
    """objc_msgSend с точной сигнатурой: на arm64 иначе нельзя."""
    key = (res, types)
    fn = _protos.get(key)
    if fn is None:
        fn = ctypes.CFUNCTYPE(res, ctypes.c_void_p, ctypes.c_void_p, *types)(_send_addr)
        _protos[key] = fn
    s = _sels.get(sel)
    if s is None:
        s = _sels[sel] = _objc.sel_registerName(sel.encode())
    return fn(obj, s, *args)


class _Pool:
    """Свой autorelease pool: вызовы идут не только изнутри цикла Tk."""

    def __enter__(self):
        self.pool = _send(_send(_cls("NSAutoreleasePool"), "alloc"), "init")

    def __exit__(self, *exc):
        _send(self.pool, "drain")


def _nsstring(text):
    return _send(_cls("NSString"), "stringWithUTF8String:", text.encode("utf-8"),
                 types=(ctypes.c_char_p,))


def image(key, path=None, data=None):
    """NSImage из файла или из байтов PNG; одна на ключ на всю программу."""
    found = _images.get(key)
    if found:
        return found
    with _Pool():
        img = _send(_cls("NSImage"), "alloc")
        if data is not None:
            nsdata = _send(_cls("NSData"), "dataWithBytes:length:", data, len(data),
                           types=(ctypes.c_char_p, ctypes.c_ulong))
            img = _send(img, "initWithData:", nsdata, types=(ctypes.c_void_p,))
        else:
            img = _send(img, "initWithContentsOfFile:", _nsstring(str(path)),
                        types=(ctypes.c_void_p,))
    if img:
        _images[key] = img
    return img


def cocoa_center(left, top, width, height, view_height):
    """Середина слоя в координатах Cocoa: у вида NSView ось y идёт снизу
    вверх, а у нас, как на canvas, — сверху вниз. setGeometryFlipped на
    слое вида AppKit молча перебивает: с ним гортензия оказалась у Барсика на
    лбу, а сам он висел над панелью. Поэтому переворачиваем сами."""
    return left + width / 2.0, view_height - (top + height / 2.0)


def _find_window(title):
    app = _send(_cls("NSApplication"), "sharedApplication")
    windows = _send(app, "windows")
    for i in range(_send(windows, "count", res=ctypes.c_ulong)):
        win = _send(windows, "objectAtIndex:", i, types=(ctypes.c_ulong,))
        name = _send(win, "title")
        text = _send(name, "UTF8String", res=ctypes.c_char_p) if name else None
        if text and text.decode("utf-8", "replace") == title:
            return win
    return None


class Layers:
    """Слои с картинками под содержимым одного окна Tk.

    Координаты — как на canvas: от левого верхнего угла окна, вниз — плюс
    (в координаты Cocoa их переводит cocoa_center).
    Слои создаются в порядке первого show(): кто раньше, тот ниже.
    """

    def __init__(self, tk_win, width, height, title=""):
        self.ok = False
        self.slots = {}
        if not available():
            return
        marker = "barsik-layers-%x" % id(self)
        try:
            tk_win.title(marker)
            tk_win.update_idletasks()
        except Exception:
            return
        with _Pool():
            win = _find_window(marker)
        # окно нашли по временному заголовку; настоящий виден в меню «Окно»
        try:
            tk_win.title(title)
        except Exception:
            pass
        with _Pool():
            if not win:
                return
            content = _send(win, "contentView")
            frame = _send(content, "superview")
            if not content or not frame:
                return
            view = _send(_send(_cls("NSView"), "alloc"), "initWithFrame:",
                         _Rect(0, 0, width, height), types=(_Rect,))
            _send(view, "setWantsLayer:", True, types=(ctypes.c_bool,))
            root = _send(view, "layer")
            # NSWindowBelow = -1: под видом Tk, чтобы его рисунки были сверху
            _send(frame, "addSubview:positioned:relativeTo:", view, -1, content,
                  types=(ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p))
        self.view, self.root = view, root
        self.height = height
        self.ok = True

    def _slot(self, name):
        layer = self.slots.get(name)
        if layer is None:
            layer = _send(_send(_cls("CALayer"), "layer"), "retain")
            _send(self.root, "addSublayer:", layer, types=(ctypes.c_void_p,))
            self.slots[name] = layer
        return layer

    def begin(self):
        with _Pool():
            _send(_cls("CATransaction"), "begin")
            # без этого Core Animation плавно «перетекает» между кадрами
            _send(_cls("CATransaction"), "setDisableActions:", True, types=(ctypes.c_bool,))

    def commit(self):
        with _Pool():
            _send(_cls("CATransaction"), "commit")

    def show(self, name, img, left, top, width, height, mirror=False):
        layer = self._slot(name)
        _send(layer, "setContents:", img, types=(ctypes.c_void_p,))
        _send(layer, "setBounds:", _Rect(0, 0, width, height), types=(_Rect,))
        _send(layer, "setPosition:", _Point(*cocoa_center(left, top, width, height, self.height)),
              types=(_Point,))
        # отражение вокруг середины слоя: картинка остаётся на том же месте
        _send(layer, "setAffineTransform:", _Affine(-1.0 if mirror else 1.0, 0, 0, 1.0, 0, 0),
              types=(_Affine,))
        _send(layer, "setHidden:", False, types=(ctypes.c_bool,))

    def hide(self, name):
        layer = self.slots.get(name)
        if layer is not None:
            _send(layer, "setHidden:", True, types=(ctypes.c_bool,))
