from gumptionchain.api_client import ApiClient
from gumptionchain.database import db
from gumptionchain.payload import encode_subject


def _stake(host, chain, wallet, *, oppose=None, support=None):
    """Create + post + return a staking txn for one subject/kind."""
    if oppose is not None:
        subject, amount = oppose
        txn = chain.create_opposition(wallet, amount, subject)
    else:
        subject, amount = support
        txn = chain.create_support(wallet, amount, subject)
    txn.sign()
    ApiClient(host, wallet).post_transaction(txn)
    return txn


def test_subject_leaderboard_orders_by_total_and_splits_kinds(
    app, host, mill_block, requests_proxy, subject, wallet
):
    other = encode_subject('other')
    with app.app_context():
        m, _b = mill_block(wallet)
        lc = m.longest_chain
        # subject: 300 opposition + 150 support = 450
        _stake(host, lc, wallet, oppose=(subject, 300))
        mill_block(wallet)
        lc = m.longest_chain
        _stake(host, lc, wallet, support=(subject, 150))
        mill_block(wallet)
        lc = m.longest_chain
        # other: 100 opposition = 100
        _stake(host, lc, wallet, oppose=(other, 100))
        mill_block(wallet)

        chain_dao = m.longest_chain.to_dao()
        rows = db.session.execute(chain_dao.subject_leaderboard()).all()
        by_subject = {r.subject: r for r in rows}

        s = by_subject[subject]
        assert s.opposition == 300
        assert s.support == 150
        assert s.total == 450

        o = by_subject[other]
        assert o.opposition == 100
        assert o.support == 0
        assert o.total == 100

        totals = [r.total for r in rows]
        assert totals == sorted(totals, reverse=True)
        # subject (450) ranks before other (100)
        assert rows[0].subject == subject


def test_subject_leaderboard_excludes_rescinded(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        lc = m.longest_chain
        _stake(host, lc, wallet, oppose=(subject, 300))
        mill_block(wallet)

        lc = m.longest_chain
        rescind = lc.create_rescind(wallet, 200, subject, 'opposition')
        rescind.sign()
        ApiClient(host, wallet).post_transaction(rescind)
        mill_block(wallet)

        chain_dao = m.longest_chain.to_dao()
        rows = db.session.execute(chain_dao.subject_leaderboard()).all()
        by_subject = {r.subject: r for r in rows}
        # 300 staked, 200 rescinded -> 100 live opposition remains
        assert by_subject[subject].opposition == 100
        assert by_subject[subject].total == 100


def test_subject_leaderboard_limit(
    app, host, mill_block, requests_proxy, subject, wallet
):
    other = encode_subject('other')
    with app.app_context():
        m, _b = mill_block(wallet)
        lc = m.longest_chain
        _stake(host, lc, wallet, oppose=(subject, 300))
        mill_block(wallet)
        lc = m.longest_chain
        _stake(host, lc, wallet, oppose=(other, 100))
        mill_block(wallet)

        chain_dao = m.longest_chain.to_dao()
        rows = db.session.execute(chain_dao.subject_leaderboard(limit=1)).all()
        assert len(rows) == 1
        # the higher-total subject wins the single slot
        assert rows[0].subject == subject


def test_chain_delegates_and_stats(
    app, host, mill_block, requests_proxy, subject, wallet
):
    other = encode_subject('other')
    with app.app_context():
        m, _b = mill_block(wallet)
        lc = m.longest_chain
        _stake(host, lc, wallet, oppose=(subject, 300))
        mill_block(wallet)
        lc = m.longest_chain
        _stake(host, lc, wallet, support=(other, 150))
        mill_block(wallet)

        lc = m.longest_chain
        # delegate returns the same rows as the DAO
        chain_rows = db.session.execute(lc.subject_leaderboard()).all()
        dao_rows = db.session.execute(lc.to_dao().subject_leaderboard()).all()
        assert {r.subject for r in chain_rows} == {r.subject for r in dao_rows}

        assert lc.subject_count == 2
        assert lc.total_staked == 450
        # transaction_count counts every txn in the canonical chain
        assert lc.transaction_count >= 4  # coinbases + 2 stakes

        recent = lc.recent_blocks(2)
        assert len(recent) == 2
        # newest-first
        assert recent[0].idx > recent[1].idx
        assert recent[0].block_hash == lc.last_block.block_hash
