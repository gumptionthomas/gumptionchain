from flask import render_template


class _FakeChainsPage:
    # Minimal stub: empty items (skip the chain-row loop) but >1 page so the
    # pagination macro renders. Carries the attributes db.paginate provides.
    pages = 2
    page = 1
    items = ()
    has_prev = False
    has_next = True
    prev_num = 0
    next_num = 2

    def iter_pages(self):
        return [1, 2]


def test_chains_pagination_links_to_chains_view(app):
    # Regression for #202: the pager must link to the chains list, not home.
    with app.test_request_context():
        html = render_template('chains.html', chains_page=_FakeChainsPage())
    assert 'class="pagination"' in html
    assert '/chains?page=2' in html  # Next / page-2 link targets chains_view
    assert '/?page=' not in html  # the old bug pointed the pager at index_view


def test_chains_index_empty(test_client):
    resp = test_client.get('/chains')
    assert resp.status_code == 200
    assert b'No chains' in resp.data
