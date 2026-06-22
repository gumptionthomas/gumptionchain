import datetime
from unittest.mock import patch

from gumptionchain.database import db
from gumptionchain.models import PendingTxnDAO, SubmissionDAO


def _pending(txid):
    PendingTxnDAO(
        txid=txid,
        timestamp=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        json_data='{}',
    ).commit()


def test_record_is_first_submitter_wins(app):
    with app.app_context():
        SubmissionDAO.record('txA', 'GCappOneGC')
        SubmissionDAO.record('txA', 'GCappTwoGC')  # later submitter ignored
        rows = db.session.execute(db.select(SubmissionDAO)).scalars().all()
        assert len(rows) == 1
        assert rows[0].txid == 'txA'
        assert rows[0].transactor_address == 'GCappOneGC'


def test_record_swallows_integrity_error_on_race(app):
    # Simulate the concurrent same-txid race: a row already exists, but the
    # existence check misses (patched to None), so record proceeds to insert
    # a duplicate txid → IntegrityError, which must be swallowed (first-
    # submitter-wins), not raised, leaving the session usable.
    with app.app_context():
        SubmissionDAO.record('txZ', 'GCfirstGC')
        with patch.object(db.session, 'scalar', return_value=None):
            SubmissionDAO.record('txZ', 'GCsecondGC')  # must not raise
        rows = db.session.execute(db.select(SubmissionDAO)).scalars().all()
        assert len(rows) == 1
        assert rows[0].transactor_address == 'GCfirstGC'


def test_pending_count_only_counts_still_pending(app):
    with app.app_context():
        _pending('txA')
        SubmissionDAO.record('txA', 'GCappOneGC')
        SubmissionDAO.record('txB', 'GCappOneGC')  # not in pending_txn
        SubmissionDAO.record('txC', 'GCappTwoGC')
        _pending('txC')
        assert SubmissionDAO.pending_count('GCappOneGC') == 1
        assert SubmissionDAO.pending_count('GCappTwoGC') == 1
        assert SubmissionDAO.pending_count('GCnobodyGC') == 0


def test_transactor_leaderboard_ranks_by_count(app):
    with app.app_context():
        for txid in ('t1', 't2', 't3'):
            SubmissionDAO.record(txid, 'GCbusyGC')
        SubmissionDAO.record('t4', 'GCquietGC')
        rows = db.session.execute(SubmissionDAO.transactor_leaderboard()).all()
        by_addr = {r.address: r for r in rows}
        assert by_addr['GCbusyGC'].count == 3
        assert by_addr['GCquietGC'].count == 1
        assert rows[0].address == 'GCbusyGC'
        assert by_addr['GCbusyGC'].last_submit_at is not None


def _seed_sortable(app):
    # Distinct counts so count ordering is unambiguous; addresses chosen so
    # their lexical order (alpha < bravo < charlie) differs from BOTH the
    # count-desc (bravo:3, alpha:2, charlie:1) and count-asc orders, making
    # the address sort a meaningful, independent assertion.
    counts = {'GCalphaGC': 2, 'GCbravoGC': 3, 'GCcharlieGC': 1}
    n = 0
    for address, ct in counts.items():
        for _ in range(ct):
            n += 1
            SubmissionDAO.record(f'tx{n}', address)


def test_transactor_leaderboard_sort_by_count_desc(app):
    with app.app_context():
        _seed_sortable(app)
        rows = db.session.execute(
            SubmissionDAO.transactor_leaderboard(
                sort_by='count', direction='desc'
            )
        ).all()
        assert [r.address for r in rows] == [
            'GCbravoGC',
            'GCalphaGC',
            'GCcharlieGC',
        ]
        assert rows[0].count == 3


def test_transactor_leaderboard_sort_by_count_asc(app):
    with app.app_context():
        _seed_sortable(app)
        rows = db.session.execute(
            SubmissionDAO.transactor_leaderboard(
                sort_by='count', direction='asc'
            )
        ).all()
        assert [r.address for r in rows] == [
            'GCcharlieGC',
            'GCalphaGC',
            'GCbravoGC',
        ]
        assert rows[0].count == 1


def test_transactor_leaderboard_sort_by_address_asc(app):
    with app.app_context():
        _seed_sortable(app)
        rows = db.session.execute(
            SubmissionDAO.transactor_leaderboard(
                sort_by='address', direction='asc'
            )
        ).all()
        assert [r.address for r in rows] == [
            'GCalphaGC',
            'GCbravoGC',
            'GCcharlieGC',
        ]


def test_transactor_leaderboard_bogus_sort_falls_back_to_count_desc(app):
    with app.app_context():
        _seed_sortable(app)
        rows = db.session.execute(
            SubmissionDAO.transactor_leaderboard(
                sort_by='bogus', direction='desc'
            )
        ).all()
        assert [r.address for r in rows] == [
            'GCbravoGC',
            'GCalphaGC',
            'GCcharlieGC',
        ]
