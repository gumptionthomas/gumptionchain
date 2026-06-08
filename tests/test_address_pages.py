from gumptionchain.database import db
from gumptionchain.wallet import Wallet

# ---- data-layer delegates ----------------------------------------------


def test_wallet_leaderboard_includes_milled_address(
    app, host, mill_block, requests_proxy, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        lc = m.longest_chain
        rows = db.session.execute(lc.wallet_leaderboard()).all()
        by_addr = {row.address: row.ct for row in rows}
        assert wallet.address in by_addr
        assert by_addr[wallet.address] == lc.balance(wallet.address)


def test_address_holdings_ordered_amount_desc(
    app, host, mill_block, requests_proxy, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        m, _b = mill_block(wallet)
        lc = m.longest_chain
        flows = list(db.session.scalars(lc.address_holdings(wallet.address)))
        assert len(flows) >= 1
        amounts = [f.amount for f in flows]
        assert amounts == sorted(amounts, reverse=True)
        # all holdings belong to the queried address
        assert all(f.address == wallet.address for f in flows)


def test_address_holdings_empty_for_unknown_address(
    app, host, mill_block, requests_proxy, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        lc = m.longest_chain
        unknown = Wallet().address
        flows = list(db.session.scalars(lc.address_holdings(unknown)))
        assert flows == []
