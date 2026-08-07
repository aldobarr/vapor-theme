from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.integration import plasma_lifecycle_check as lifecycle


class PlasmaLifecycleTests(unittest.TestCase):
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
