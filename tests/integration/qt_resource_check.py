from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exercise Qt image and icon discovery for Vapor."
    )
    parser.add_argument("data_home", type=Path)
    return parser


def _icon_theme_search_paths(
    data_home: Path,
    *,
    inherited_paths: Iterable[str],
) -> list[str]:
    configured_data_dirs = os.environ.get("XDG_DATA_DIRS")
    data_dirs = (
        configured_data_dirs.split(os.pathsep)
        if configured_data_dirs
        else ["/usr/local/share", "/usr/share"]
    )
    candidates = [
        str(data_home / "icons"),
        *(str(Path(data_dir) / "icons") for data_dir in data_dirs if data_dir),
        *inherited_paths,
    ]
    return list(dict.fromkeys(candidates))


def run_probe(data_home: Path) -> None:
    from PyQt6.QtGui import QGuiApplication, QIcon, QImageReader

    application = QGuiApplication(["vapor-qt-resource-check"])
    wallpaper = (
        data_home / "wallpapers" / "Vapor" / "contents" / "images" / "3940x2160.jxl"
    )
    reader = QImageReader(str(wallpaper))
    reader.setDecideFormatFromContent(True)
    if not reader.canRead():
        formats = ", ".join(
            bytes(name).decode("ascii", errors="replace")
            for name in QImageReader.supportedImageFormats()
        )
        raise RuntimeError(f"Qt cannot decode Convergence JXL; formats: {formats}")
    image = reader.read()
    if image.isNull() or image.width() != 3940 or image.height() != 2160:
        raise RuntimeError(
            "Qt decoded Convergence to an invalid image "
            f"{image.width()}x{image.height()}"
        )

    QIcon.setThemeSearchPaths(
        _icon_theme_search_paths(
            data_home,
            inherited_paths=QIcon.themeSearchPaths(),
        )
    )
    QIcon.setThemeName("hicolor")
    icon = QIcon.fromTheme("vapor-bazzite")
    if icon.isNull() or icon.pixmap(64, 64).isNull():
        raise RuntimeError("QIcon.fromTheme did not resolve vapor-bazzite")
    del application


def main() -> int:
    arguments = _parser().parse_args()
    try:
        run_probe(arguments.data_home)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("Qt discovered the Vapor JXL wallpaper and launcher icon")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
