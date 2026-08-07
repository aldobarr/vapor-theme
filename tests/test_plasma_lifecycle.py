from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.integration import plasma_accent_render_check as accent_renderer
from tests.integration import plasma_lifecycle_check as lifecycle


class PlasmaLifecycleTests(unittest.TestCase):
    def test_accent_probe_captures_the_qml_item_not_the_window_clear_buffer(
        self,
    ) -> None:
        class Image:
            def __init__(self, is_null: bool) -> None:
                self._is_null = is_null

            def isNull(self) -> bool:
                return self._is_null

        expected = Image(False)

        class GrabResult:
            ready = False

            def image(self) -> Image:
                return expected if self.ready else Image(True)

        grab_result = GrabResult()

        class RootItem:
            requested_size: object | None = None

            def grabToImage(self, size: object) -> GrabResult:
                self.requested_size = size
                return grab_result

        class Application:
            def processEvents(self) -> None:
                grab_result.ready = True

        root = RootItem()
        requested_size = object()

        actual = accent_renderer._capture_item_image(
            root,
            Application(),
            requested_size,
            timeout=0.1,
        )

        self.assertIs(actual, expected)
        self.assertIs(root.requested_size, requested_size)

    def test_accent_probe_pixel_must_match_plasmas_resolved_highlight(self) -> None:
        lifecycle._require_render_matches_resolved(
            {
                "resolved_highlight": "#ff1744",
                "swatch_pixel": "#ff1744",
            },
            label="red Plasma highlight",
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "resolved #ff1744 but captured #ffffff",
        ):
            lifecycle._require_render_matches_resolved(
                {
                    "resolved_highlight": "#ff1744",
                    "swatch_pixel": "#ffffff",
                },
                label="red Plasma highlight",
            )

    def test_accent_renders_are_retained_in_the_diagnostics_directory(self) -> None:
        red_render = {
            "component_pixel": "#ff1744",
            "resolved_highlight": "#ff1744",
            "swatch_pixel": "#ff1744",
        }
        green_render = {
            "component_pixel": "#00c853",
            "resolved_highlight": "#00c853",
            "swatch_pixel": "#00c853",
        }
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {"VAPOR_DIAGNOSTICS_DIR": temporary},
            ),
            patch.object(
                lifecycle,
                "_render_accent",
                side_effect=(red_render, green_render),
            ) as render_accent,
        ):
            lifecycle._exercise_visual_accent_updates(Path("discarded"))

            expected = Path(temporary) / "accent-renders"
            self.assertEqual(
                [call.args[1] for call in render_accent.call_args_list],
                [expected / "red.png", expected / "green.png"],
            )
            self.assertEqual(
                json.loads((expected / "red.json").read_text(encoding="utf-8")),
                red_render,
            )
            self.assertEqual(
                json.loads((expected / "green.json").read_text(encoding="utf-8")),
                green_render,
            )

    def test_wayland_may_enlarge_the_accent_probe_window(self) -> None:
        accent_renderer._require_valid_image_dimensions(
            is_null=False,
            width=192,
            height=150,
        )

    def test_accent_probe_rejects_null_or_undersized_images(self) -> None:
        for is_null, width, height in (
            (True, 192, 150),
            (False, 191, 64),
            (False, 192, 63),
        ):
            with (
                self.subTest(is_null=is_null, width=width, height=height),
                self.assertRaisesRegex(RuntimeError, f"{width}x{height}"),
            ):
                accent_renderer._require_valid_image_dimensions(
                    is_null=is_null,
                    width=width,
                    height=height,
                )

    def test_accent_renderer_stays_connected_to_the_active_wayland_session(
        self,
    ) -> None:
        applied = subprocess.CompletedProcess([], 0, "", "")
        rendered = subprocess.CompletedProcess(
            [],
            0,
            lifecycle.ACCENT_RENDER_MARKER
            + '{"component_pixel":"#ff1744","swatch_pixel":"#ff1744"}\n',
            "",
        )

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"QT_QPA_PLATFORM": "wayland"}, clear=True),
            patch.object(
                lifecycle,
                "_checked_process",
                side_effect=(applied, rendered),
            ) as checked_process,
        ):
            lifecycle._render_accent("#ff1744", Path(temporary) / "accent.png")

        render_environment = checked_process.call_args_list[1].kwargs["environment"]
        self.assertEqual(render_environment["QT_QPA_PLATFORM"], "wayland")
        self.assertEqual(render_environment["QT_QUICK_BACKEND"], "software")


if __name__ == "__main__":
    unittest.main()
