"""Copy the runtime wallet ESM modules from this gumptionchain checkout's
clients/wallet into the served static dir. Excludes *.test.mjs and *-cli.mjs
(dev-only).

Usage: uv run python scripts/sync_wallet.py [--source .]
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

DEST = (
    Path(__file__).resolve().parent.parent / 'src/gumptionchain/static/wallet'
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default='.')
    args = parser.parse_args()
    src = Path(args.source).resolve() / 'clients/wallet'
    if not src.is_dir():
        msg = f'wallet source not found: {src}'
        raise SystemExit(msg)
    DEST.mkdir(parents=True, exist_ok=True)
    for mjs in sorted(src.glob('*.mjs')):
        if mjs.name.endswith('.test.mjs') or mjs.name.endswith('-cli.mjs'):
            continue
        shutil.copy2(mjs, DEST / mjs.name)
        print(f'vendored {mjs.name}')  # noqa: T201


if __name__ == '__main__':
    main()
