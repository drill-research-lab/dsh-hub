import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'singleuser'))
import harness_proxy
from harness_proxy import rewrite_response, request_headers


class HarnessProxyTest(unittest.TestCase):
    def test_user_scoped_resources(self):
        response = SimpleNamespace(
            headers={'Content-Type': 'text/html', 'ETag': 'old'},
            body=b'<base href="/"><script src="/plugins/a.js"></script><script>fetch("/open-in-app/apps")</script>',
        )
        with patch.dict(os.environ, {'JUPYTERHUB_SERVICE_PREFIX': '/user/alice/'}):
            rewrite_response(response)
        self.assertIn(b'<base href="/user/alice/harness/">', response.body)
        self.assertIn(b'"/user/alice/harness/plugins/a.js"', response.body)
        self.assertIn(b'"/user/alice/harness/open-in-app/apps"', response.body)
        self.assertNotIn('ETag', response.headers)

    def test_binary_and_external_urls_unchanged(self):
        binary = SimpleNamespace(headers={'Content-Type': 'image/png'}, body=b'\xff\x00')
        rewrite_response(binary)
        self.assertEqual(binary.body, b'\xff\x00')
        response = SimpleNamespace(headers={'Content-Type': 'application/javascript'}, body=b'fetch("https://example.com/api"); const path="/home/demo"; const channel="/api";')
        rewrite_response(response)
        self.assertEqual(response.body, b'fetch("https://example.com/api"); const path="/home/demo"; const channel="/api";')


class RequestHeadersTest(unittest.TestCase):
    def _cookie_file(self, port):
        return Path(f'/tmp/dsh-proxy-{port}.cookie')

    def setUp(self):
        self.port = 65000 + (os.getpid() % 1000)
        self.cookie_file = self._cookie_file(self.port)
        self.cookie_file.unlink(missing_ok=True)
        patcher = patch.multiple(harness_proxy, COOKIE_WAIT_TIMEOUT=0.4, COOKIE_WAIT_INTERVAL=0.02)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.cookie_file.unlink, missing_ok=True)

    def test_cookie_already_present(self):
        self.cookie_file.write_text('session=abc')
        headers = request_headers(self.port)
        self.assertEqual(headers['Cookie'], 'session=abc')
        self.assertEqual(headers['Host'], f'127.0.0.1:{self.port}')

    def test_waits_for_cookie_written_after_a_delay(self):
        def write_late():
            time.sleep(0.1)
            self.cookie_file.write_text('session=late')

        threading.Thread(target=write_late).start()
        headers = request_headers(self.port)
        self.assertEqual(headers['Cookie'], 'session=late')

    def test_gives_up_after_timeout_if_cookie_never_appears(self):
        started = time.monotonic()
        headers = request_headers(self.port)
        elapsed = time.monotonic() - started
        self.assertEqual(headers['Cookie'], '')
        self.assertLess(elapsed, 2)


if __name__ == '__main__':
    unittest.main()
