#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

RENDER_MARKER = "VAPOR_ACCENT_RENDER="
PROBE_WIDTH = 192
PROBE_HEIGHT = 64


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render Plasma's resolved highlight color into a PNG."
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _qml_source() -> str:
    return """\
import QtQuick
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.extras as PlasmaExtras

Rectangle {
    id: root
    width: 192
    height: 64
    color: PlasmaCore.Theme.backgroundColor

    Rectangle {
        x: 8
        y: 8
        width: 48
        height: 48
        color: PlasmaCore.Theme.highlightColor
    }

    Item {
        x: 72
        y: 8
        width: 112
        height: 48

        PlasmaExtras.Highlight {
            anchors.fill: parent
            active: true
            hovered: true
        }
    }
}
"""


def _require_valid_image_dimensions(
    *,
    is_null: bool,
    width: int,
    height: int,
) -> None:
    if is_null or width < PROBE_WIDTH or height < PROBE_HEIGHT:
        raise RuntimeError(
            f"Plasma accent probe rendered an invalid image {width}x{height}"
        )


def run_probe(output: Path) -> dict[str, str]:
    from PyQt6.QtCore import QEventLoop, QUrl
    from PyQt6.QtGui import QColor, QGuiApplication
    from PyQt6.QtQuick import QQuickView, QQuickWindow

    application = QGuiApplication(["vapor-plasma-accent-render-check"])
    QQuickWindow.setSceneGraphBackend("software")
    with tempfile.TemporaryDirectory(prefix="vapor-accent-qml-") as temporary:
        qml = Path(temporary) / "AccentProbe.qml"
        qml.write_text(_qml_source(), encoding="utf-8", newline="\n")

        view = QQuickView()
        view.setSource(QUrl.fromLocalFile(str(qml)))
        if view.status() != QQuickView.Status.Ready:
            errors = "; ".join(error.toString() for error in view.errors())
            raise RuntimeError(f"Plasma accent probe QML did not load: {errors}")
        view.show()

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            application.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            time.sleep(0.02)

        image = view.grabWindow()
        _require_valid_image_dimensions(
            is_null=image.isNull(),
            width=image.width(),
            height=image.height(),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(output), "PNG"):
            raise RuntimeError(f"could not save Plasma accent render to {output}")

        root = view.rootObject()
        if root is None:
            raise RuntimeError("Plasma accent probe has no root object")
        result = {
            "component_pixel": image.pixelColor(128, 32).name(QColor.NameFormat.HexRgb),
            "swatch_pixel": image.pixelColor(32, 32).name(QColor.NameFormat.HexRgb),
        }
        view.close()
    del application
    return result


def main() -> int:
    arguments = _parser().parse_args()
    try:
        result = run_probe(arguments.output)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(RENDER_MARKER + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
