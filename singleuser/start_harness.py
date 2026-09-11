"""Start Harness and exchange its launch token locally behind Jupyter auth."""
import argparse
import http.cookiejar
import os
from pathlib import Path
import re
import signal
import subprocess
import urllib.request

DSH_HOME = Path(os.environ.get('DSH_HOME', '/home/demo/.dsh'))
DISPATCHER_PATCH_DEST = DSH_HOME / 'patches' / 'dispatcher-provider.yaml'
DISPATCHER_PATCH_SEED = Path('/opt/demo/dispatcher-provider.yaml')


def ensure_dispatcher_patch():
    """Seed the default Dispatcher provider into the user's persisted home
    the first time their container boots. Never overwrites an existing
    copy, so a user's own edits (or their own added providers) survive
    every restart and every image update -- see README.md."""
    if DISPATCHER_PATCH_DEST.exists() or not DISPATCHER_PATCH_SEED.exists():
        return
    DISPATCHER_PATCH_DEST.parent.mkdir(parents=True, exist_ok=True)
    DISPATCHER_PATCH_DEST.write_text(DISPATCHER_PATCH_SEED.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    cookie_file = Path(f'/tmp/dsh-proxy-{args.port}.cookie')
    cookie_file.unlink(missing_ok=True)
    ensure_dispatcher_patch()
    cmd = ['dsh', '--profile', 'web']
    if DISPATCHER_PATCH_DEST.exists():
        cmd += ['--patch', str(DISPATCHER_PATCH_DEST)]
    cmd += ['--no-open', '--host', '127.0.0.1', '--port', str(args.port)]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    def stop(signum, frame):
        process.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for line in process.stdout:
            match = re.search(r'http://127\.0\.0\.1:' + str(args.port) + r'/\?token=([^\s]+)', line)
            if match:
                jar = http.cookiejar.CookieJar()
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
                with opener.open(match.group(0), timeout=15) as response:
                    response.read()
                cookie = '; '.join(f'{item.name}={item.value}' for item in jar)
                if not cookie:
                    raise RuntimeError('Harness did not issue a browser session cookie')
                fd = os.open(cookie_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, 'w') as output:
                    output.write(cookie)
                print('DeepSeek Harness ready behind JupyterHub authentication', flush=True)
            else:
                print(line, end='', flush=True)
        raise SystemExit(process.wait())
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        cookie_file.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
