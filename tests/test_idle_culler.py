"""Unit test for hub/idle_culler.py's pure timestamp-parsing helper.

Skipped automatically if the `redis` package isn't importable -- idle_culler
imports common/dispatch_queue.py, which needs redis.asyncio even though this
particular helper doesn't touch Redis at all. This dev sandbox's .venv has
no pip, so it always skips here; run it in an environment with
`pip install redis` for real coverage.
"""
import os
from pathlib import Path
import sys
import unittest

# idle_culler.py reads these at import time (JupyterHub always sets them for
# a real Service); stub them in so the module can be imported for testing
# its pure helper without actually running under the Hub.
os.environ.setdefault('JUPYTERHUB_API_URL', 'http://hub:8081/hub/api')
os.environ.setdefault('JUPYTERHUB_API_TOKEN', 'test-token')

# idle_culler.py does `from dispatch_queue import Queue` assuming it's been
# placed on sys.path (true inside the container, where it's copied to
# /srv/jupyterhub alongside idle_culler.py itself) -- add common/ here too
# so the import succeeds outside the container as well.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hub'))

try:
    from idle_culler import _parse_last_activity
    IDLE_CULLER_IMPORTABLE = True
except ImportError:
    IDLE_CULLER_IMPORTABLE = False


@unittest.skipUnless(IDLE_CULLER_IMPORTABLE, 'redis package not available')
class ParseLastActivityTest(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(_parse_last_activity(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_parse_last_activity(''))

    def test_zulu_suffix_parses_as_utc(self):
        # JupyterHub's isoformat() helper emits a trailing "Z" for UTC.
        timestamp = _parse_last_activity('2026-01-01T00:00:00.000000Z')
        self.assertAlmostEqual(timestamp, 1767225600.0, places=3)

    def test_explicit_offset_parses(self):
        timestamp = _parse_last_activity('2026-01-01T00:00:00+00:00')
        self.assertAlmostEqual(timestamp, 1767225600.0, places=3)


if __name__ == '__main__':
    unittest.main()
