from flask import render_template_string

from gumptionchain.api_client import ApiClient

_MACRO = (
    "{% from '_pagination.html' import render_pagination %}"
    "{{ render_pagination(page, 'browser.blocks_view') }}"
)


class _FakePage:
    def __init__(self, *, pages, page=1, prev=False, nxt=False):
        self.pages = pages
        self.page = page
        self.has_prev = prev
        self.has_next = nxt
        self.prev_num = page - 1
        self.next_num = page + 1

    def iter_pages(self):
        return list(range(1, self.pages + 1))


def test_blocks_list_empty(test_client):
    resp = test_client.get('/blocks')
    assert resp.status_code == 200
    assert b'No blocks' in resp.data


def test_blocks_list_shows_mined_blocks(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        mill_block(signing_key)
        _m, b2 = mill_block(signing_key)
        tip_hash = b2.block_hash

        resp = app.test_client().get('/blocks')
        assert resp.status_code == 200
        assert tip_hash.encode() in resp.data
        assert b'/block/' in resp.data
        # rows are clickable into the block detail page
        assert b'clickable' in resp.data


def test_blocks_list_shows_transaction_count(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    # A block carrying a staking txn shows a non-zero Txns count (the coinbase
    # plus the staked transaction), exercising the precomputed tx_counts map.
    with app.app_context():
        m, _b1 = mill_block(signing_key)
        txn = m.longest_chain.create_opposition(signing_key, 300, subject)
        txn.sign()
        ApiClient(host, signing_key).post_transaction(txn)
        mill_block(signing_key)

        resp = app.test_client().get('/blocks')
        assert resp.status_code == 200
        # the confirming block has 2 txns (coinbase + the opposition stake)
        assert b'<td>2</td>' in resp.data


def test_pagination_macro_renders_when_multipage(app):
    with app.test_request_context():
        html = render_template_string(
            _MACRO, page=_FakePage(pages=3, page=2, prev=True, nxt=True)
        )
    assert 'class="pagination"' in html
    assert 'page=1' in html  # Previous / first page link
    assert 'page=3' in html  # Next / last page link
    assert 'active' in html  # the current page is marked active


def test_pagination_macro_hidden_when_single_page(app):
    with app.test_request_context():
        html = render_template_string(_MACRO, page=_FakePage(pages=1))
    assert 'class="pagination"' not in html
