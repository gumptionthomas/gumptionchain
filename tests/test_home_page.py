import datetime
import re

from gumptionchain.api_client import ApiClient
from gumptionchain.block import expiry_cutoff
from gumptionchain.browser import explorer_home_context
from gumptionchain.models import PendingTxnDAO
from gumptionchain.util import now


def _stake_opposition(host, chain, signing_key, amount, subject):
    txn = chain.create_opposition(signing_key, amount, subject)
    txn.sign()
    ApiClient(host, signing_key).post_transaction(txn)
    return txn


def test_explorer_home_context_empty_chain(app):
    # The public seam helper (#244) is safe before any block exists.
    with app.app_context():
        assert explorer_home_context() == {
            'lc': None,
            'subject_count': 0,
            'total_staked': 0,
            'pending_count': 0,
        }


def test_explorer_home_context_seeded_chain(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    # explorer_home_context() returns exactly the four keys index_view
    # passes to the template, computed the same way (#244).
    with app.app_context():
        m, _b1 = mill_block(signing_key)
        _stake_opposition(host, m.longest_chain, signing_key, 300, subject)
        m, tip = mill_block(signing_key)  # confirms the stake
        _stake_opposition(host, m.longest_chain, signing_key, 200, subject)

        ctx = explorer_home_context()
        assert set(ctx) == {
            'lc',
            'subject_count',
            'total_staked',
            'pending_count',
        }
        assert ctx['lc'].last_block.block_hash == tip.block_hash
        assert ctx['subject_count'] == 1
        assert ctx['total_staked'] == 300
        assert ctx['pending_count'] == 1


def test_home_empty_chain(test_client):
    resp = test_client.get('/')
    assert resp.status_code == 200
    assert b'No chain' in resp.data


def test_base_links_node_favicon(test_client):
    # The base node ships the green "G" node favicon by default; the hub
    # overrides the favicon block (or shadows base.html) for its own.
    resp = test_client.get('/')
    assert resp.status_code == 200
    assert b'img/favicon-node.svg' in resp.data


def test_home_shows_stats_and_recent_blocks(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b1 = mill_block(signing_key)
        _stake_opposition(host, m.longest_chain, signing_key, 300, subject)
        _m, tip = mill_block(signing_key)

        resp = app.test_client().get('/')
        assert resp.status_code == 200
        body = resp.data
        # stats strip labels
        assert b'Height' in body or b'Blocks' in body
        assert b'Transactions' in body
        assert b'Subjects' in body
        # links into the explorer
        assert b'/blocks' in body
        assert b'/subjects' in body
        # the chain tip hash appears in the recent-blocks table
        assert tip.block_hash.encode() in body


def test_home_shows_pending_count(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake_opposition(host, m.longest_chain, signing_key, 300, subject)

        resp = app.test_client().get('/')
        assert resp.status_code == 200
        body = resp.data
        # a Pending stat card with the pool count (1 pending txn)
        assert b'Pending' in body
        assert b'>1<' in body


def test_home_pending_count_excludes_confirmed_and_expired(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        confirmed = _stake_opposition(
            host, m.longest_chain, signing_key, 300, subject
        )
        m, _b = mill_block(signing_key)  # confirms + prunes `confirmed`
        # re-insert the confirmed txn (simulates re-gossip after mining)
        PendingTxnDAO(
            txid=confirmed.txid,
            timestamp=confirmed.timestamp_dt,
            json_data=confirmed.to_json(),
        ).commit()
        # an expired row (the /mempool view hides it; so must the badge)
        PendingTxnDAO(
            txid='a' * 64,
            timestamp=now() - datetime.timedelta(hours=8),
            json_data='{}',
        ).commit()
        # one live, unconfirmed txn
        _stake_opposition(host, m.longest_chain, signing_key, 200, subject)

        # raw count sees all three; the badge count sees only the live one
        assert PendingTxnDAO.count() == 3
        assert (
            PendingTxnDAO.unconfirmed_count(expired=expiry_cutoff(now())) == 1
        )

        resp = app.test_client().get('/')
        assert resp.status_code == 200
        # Use a regex anchored to the Pending card's HTML structure to
        # avoid collision: subject_count=1 and pending_count=1 both render
        # ">1<" in the page. Matching "Pending" label + value div uniquely
        # identifies the Pending stat card regardless of other card values.
        assert re.search(
            rb'Pending</div>\s*<div[^>]*>\s*1\s*</div>',
            resp.data,
        ), 'Pending badge should show 1 (live unconfirmed only)'
        # also verify the raw pool size (3) is NOT shown in the badge
        assert not re.search(
            rb'Pending</div>\s*<div[^>]*>\s*3\s*</div>',
            resp.data,
        ), 'Pending badge must not show the raw pool count (3)'
