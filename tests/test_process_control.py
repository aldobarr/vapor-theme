import os
import subprocess
import sys
import time
import unittest

from tests.integration import process_control


class ProcessControlTests(unittest.TestCase):
    @unittest.skipUnless(
        hasattr(os, "killpg"),
        "POSIX process groups are exercised by Linux CI",
    )
    def test_timeout_terminates_then_kills_the_process_group(self) -> None:
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            process_control.run_bounded(
                [
                    sys.executable,
                    "-c",
                    (
                        "import signal, time; "
                        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                        "print('bounded child ready', flush=True); "
                        "time.sleep(60)"
                    ),
                ],
                timeout=0.1,
            )

        self.assertIn("bounded child ready", raised.exception.output)
        self.assertLess(
            time.monotonic() - started,
            8,
            "bounded runner did not force-kill the SIGTERM-resistant group",
        )


if __name__ == "__main__":
    unittest.main()
