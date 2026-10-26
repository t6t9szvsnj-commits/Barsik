# -*- mode: python ; coding: utf-8 -*-
# Рецепт PyInstaller для Барсик.app. Собирается только на Маке:
#
#     python3 -m PyInstaller --noconfirm tools/app/barsik.spec
#
# В CI это делает .github/workflows/app.yml. Копий кода здесь нет: приложение
# каждый раз собирается из versions/, поэтому оно не разъедется с версиями.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
VERSIONS = os.path.join(ROOT, "versions")

a = Analysis(
    [os.path.join(VERSIONS, "desktop_barsik.py")],
    pathex=[VERSIONS],
    datas=[(os.path.join(VERSIONS, "sprites", "barsik"), os.path.join("sprites", "barsik"))],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    # имя файла внутри приложения латиницей: кириллица в CFBundleExecutable —
    # лишний риск, а пользователь его всё равно не видит
    name="Barsik",
    console=False,
    # и для Маков на M1–M4, и для Intel: какой у пользователя Мак, заранее неизвестно
    target_arch="universal2",
)
coll = COLLECT(exe, a.binaries, a.datas, name="Barsik")
app = BUNDLE(
    coll,
    name="Барсик.app",
    icon=os.path.join(SPECPATH, "barsik.icns"),
    bundle_identifier="com.t6t9szvsnj.barsik",
    info_plist={
        "CFBundleName": "Барсик",
        "CFBundleDisplayName": "Барсик",
        "CFBundleShortVersionString": "3.0",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        # Тексты системных запросов разрешений. Без описания для AppleEvents
        # macOS может молча отказать в доступе к Finder, и Барсик не узнает,
        # где лежат иконки.
        "NSAppleEventsUsageDescription":
            "Барсик спрашивает у Finder, где на столе лежат иконки, чтобы "
            "подходить к нужному файлу.",
        "NSDesktopFolderUsageDescription":
            "Барсик разбирает файлы на Рабочем столе — только когда ты попросишь "
            "и подтвердишь план.",
        "NSDownloadsFolderUsageDescription":
            "Барсик разбирает папку «Загрузки» — только когда ты попросишь и "
            "подтвердишь план.",
    },
)
