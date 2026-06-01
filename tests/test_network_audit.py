"""Demonstration tests for the 2026-06-01 P2P/networking threat-model audit.

Each test below demonstrates one audit finding and is marked
``@pytest.mark.xfail(strict=True)`` -- strict mode means the test MUST fail
today (the gap is real) and forces the marker's removal when the finding is
remediated (the xfail would otherwise "unexpectedly pass" and error the
suite). See docs/superpowers/audits/2026-06-01-network-p2p-audit.md.

Availability findings use a *bounded-observation* convention: drive the
uncapped behavior only up to a small, safe bound and assert the missing cap
is observable. No test exhausts real memory, disk, or wall-clock.
"""

from cancelchain.database import db
from cancelchain.models import ChainFillBlock

# Per-finding tests (and any further imports: pytest, Block, Node, ...) are
# appended below this scaffold. Shared fixtures (app, *_wallet,
# requests_proxy, remote_requests_proxy, mill_block, host, time_stepper) come
# from tests/conftest.py.


def staged_chain_fill_count(app):
    """Count ChainFillBlock rows currently staged -- used by availability
    tests that assert fill_chain stages an attacker-controlled number of
    blocks with no depth cap.

    NB: this project uses SQLAlchemy 2.0 with a plain ``DeclarativeBase``
    (``SQLAlchemy(model_class=Base)``), NOT ``db.Model`` -- so the legacy
    ``Model.query`` attribute does NOT exist here (it raises AttributeError).
    Use the 2.0 count idiom (mirrors ``tests/_sa_helpers._count``).
    """
    with app.app_context():
        return (
            db.session.scalar(
                db.select(db.func.count()).select_from(ChainFillBlock)
            )
            or 0
        )
