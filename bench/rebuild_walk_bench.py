"""Benchmark for ChainDAO._rebuild_longest_chain_blocks() walk perf.

Generates a synthetic chain of N blocks (bypassing milling and
consensus validation — we only care about the walk + insert pattern)
and times the rebuild. Sweeps over multiple N values to characterize
how the iterative walk scales.

Used to decide whether Phase 6.7 (batched-fetch) is worth implementing.
See docs/superpowers/ROADMAP.md "Phase 6.7" for context.

Usage:
    uv run python bench/rebuild_walk_bench.py
    uv run python bench/rebuild_walk_bench.py --sizes 1000 10000 100000

The script's module-level env-var setup runs ONCE on import; importing
this module from anywhere besides a `__main__` entry point will leak
a temp SQLite DB file at /tmp until process exit.
"""

from __future__ import annotations

import argparse
import atexit
import datetime
import os
import tempfile
import time
from pathlib import Path

# Module-level env setup: must run before cancelchain imports so the
# Flask app factory reads our test secret + SQLite URI rather than
# whatever's in the shell. Mkstemp returns a file descriptor + path;
# we close the fd immediately and let SQLAlchemy own the file.
os.environ.setdefault('FLASK_SECRET_KEY', 'a' * 32)
_TMPDB_FD, _TMPDB_PATH = tempfile.mkstemp(suffix='.db')
os.close(_TMPDB_FD)
os.environ['FLASK_SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{_TMPDB_PATH}'

# Register cleanup at interpreter exit so the tmp DB file is removed
# even if main() never runs (e.g., module imported by an IDE / linter
# / dependency scanner) or exits via an uncaught exception.
# Path.unlink(missing_ok=True) is idempotent, so this is safe to run
# alongside the explicit cleanup in main()'s finally block.
atexit.register(lambda: Path(_TMPDB_PATH).unlink(missing_ok=True))

from cancelchain import create_app  # noqa: E402
from cancelchain.database import db  # noqa: E402
from cancelchain.models import (  # noqa: E402
    BlockDAO,
    ChainDAO,
    LongestChainBlockDAO,
)


def prefill_synthetic_chain(n: int) -> int:
    """Insert N synthetic BlockDAO rows linked tip→genesis.

    Returns the tip block_id. The synthetic blocks don't satisfy real
    consensus rules; this benchmark exercises the materialization
    walk + insert pattern, not block validation.

    Performance note: passes prev_dao explicitly so BlockDAO.__init__
    short-circuits the BlockDAO.get(prev_hash) lookup. Each row gets
    one db.session.flush() to populate its auto-generated PK so the
    next row's prev relationship binds correctly.
    """
    prev_dao: BlockDAO | None = None
    last_dao: BlockDAO | None = None
    base_ts = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)
    for i in range(n):
        block_hash = f'h{i:08d}'.ljust(64, '0')[:64]
        prev_hash = f'h{i - 1:08d}'.ljust(64, '0')[:64] if i > 0 else ''
        row = BlockDAO(
            block_hash=block_hash,
            version='1',
            idx=i,
            prev_hash=prev_hash,
            timestamp=base_ts + datetime.timedelta(minutes=i),
            merkle_root='',
            proof_of_work=0,
            target='F' * 64,
            prev_dao=prev_dao,
        )
        db.session.add(row)
        db.session.flush()
        prev_dao = row
        last_dao = row

    db.session.commit()
    assert last_dao is not None
    return int(last_dao.id)


def build_chain_dao_for_tip(tip_block_id: int) -> int:
    """Create a ChainDAO row pointing at the tip block, return its id.

    Also wipes any pre-existing longest_chain_block rows so the
    rebuild starts from an empty materialization.
    """
    db.session.query(LongestChainBlockDAO).delete()
    db.session.commit()

    tip = db.session.query(BlockDAO).filter_by(id=tip_block_id).one()
    chain_dao = ChainDAO(block_hash=tip.block_hash, block_dao=tip)
    db.session.add(chain_dao)
    db.session.commit()
    return int(chain_dao.id)


def time_rebuild(chain_dao_id: int) -> tuple[float, int]:
    """Time _rebuild_longest_chain_blocks in wall-clock seconds.

    Returns (seconds, materialized_row_count). Closes the SQLAlchemy
    session before re-fetching the ChainDAO so the walk's current.prev
    accesses fire fresh DB queries rather than reading from the
    prefill phase's identity map (which would underreport real cost).
    """
    db.session.close()
    chain_dao = db.session.query(ChainDAO).filter_by(id=chain_dao_id).one()

    start = time.perf_counter()
    chain_dao._rebuild_longest_chain_blocks()
    db.session.commit()
    elapsed = time.perf_counter() - start

    count = db.session.query(LongestChainBlockDAO).count()
    return elapsed, count


def wipe_tables() -> None:
    """Drop all rows from block, chain, longest_chain_block."""
    db.session.query(LongestChainBlockDAO).delete()
    db.session.query(ChainDAO).delete()
    db.session.query(BlockDAO).delete()
    db.session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Benchmark ChainDAO._rebuild_longest_chain_blocks()'
    )
    parser.add_argument(
        '--sizes',
        type=int,
        nargs='+',
        default=[1_000, 10_000, 100_000],
        help='Chain lengths to benchmark (default: 1k, 10k, 100k)',
    )
    args = parser.parse_args()
    if any(n <= 0 for n in args.sizes):
        parser.error('--sizes values must be positive integers')

    app = create_app()
    with app.app_context():
        db.create_all()
        header = (
            f'{"n":>10}  {"prefill_s":>10}  '
            f'{"rebuild_s":>10}  {"per_step_ms":>13}  {"rows":>10}'
        )
        print(header)
        print('-' * len(header))
        try:
            for n in args.sizes:
                wipe_tables()

                t0 = time.perf_counter()
                tip_id = prefill_synthetic_chain(n)
                t_prefill = time.perf_counter() - t0

                chain_dao_id = build_chain_dao_for_tip(tip_id)
                t_rebuild, rows = time_rebuild(chain_dao_id)

                per_step_ms = t_rebuild * 1000 / n
                print(
                    f'{n:>10,}  {t_prefill:>10.3f}  '
                    f'{t_rebuild:>10.3f}  {per_step_ms:>13.3f}  '
                    f'{rows:>10,}'
                )
                assert rows == n, f'expected {n} materialized rows, got {rows}'
        finally:
            # Close session + dispose engine BEFORE unlinking the
            # SQLite file. On platforms with stricter file locking
            # (notably Windows), unlinking an in-use SQLite file
            # raises PermissionError.
            db.session.remove()
            db.engine.dispose()
            Path(_TMPDB_PATH).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
