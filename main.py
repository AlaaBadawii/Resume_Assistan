'''Root entrypoint for the local Resume Assistant Gradio application.'''

from __future__ import annotations

import argparse
import os
import platform
import sys
from pathlib import Path

from resume_assistant.cli import DEFAULT_MASTER_PATH, PROJECT_ROOT
from resume_assistant.ui import DEFAULT_PORT, launch_app


def is_wsl() -> bool:
    '''Detect Windows Subsystem for Linux to avoid noisy xdg-open browser spawns.'''
    if 'WSL_DISTRO_NAME' in os.environ or 'WSL_INTEROP' in os.environ:
        return True
    release = platform.release().lower()
    return 'microsoft' in release or 'wsl' in release


def load_dotenv_file(env_path: Path) -> None:
    '''Load KEY=VALUE pairs from a local .env file into os.environ without printing secrets.'''
    if not env_path.is_file() or env_path.is_symlink():
        return
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].strip()
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        if not key or any(ch.isspace() for ch in key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='main.py',
        description='Launch the local Resume Assistant Gradio web UI (127.0.0.1 only).',
    )
    parser.add_argument(
        '--port',
        type=int,
        default=DEFAULT_PORT,
        help=f'Local loopback port to bind (default: {DEFAULT_PORT}).',
    )
    parser.add_argument(
        '--no-browser',
        action='store_true',
        help='Do not automatically open a browser tab on startup.',
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.port <= 65535:
        print('error: --port must be between 1 and 65535', file=sys.stderr)
        return 2

    load_dotenv_file(PROJECT_ROOT / '.env')

    if not DEFAULT_MASTER_PATH.is_file() or DEFAULT_MASTER_PATH.is_symlink():
        print(
            'error: private Master Resume not found at data/private/Master_Resume.docx',
            file=sys.stderr,
        )
        return 2

    open_browser = (not args.no_browser) and (not is_wsl())
    print(f'Open in your browser: http://127.0.0.1:{args.port}')
    launch_app(port=args.port, open_browser=open_browser)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
