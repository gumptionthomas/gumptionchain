from flask import render_template_string


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


def _render(app, template, path, **ctx):
    with app.test_request_context(path):
        return render_template_string(template, **ctx)


def test_default_page_param_renders_page_links(app):
    template = (
        "{% from '_pagination.html' import render_pagination %}"
        "{{ render_pagination(page, 'browser.blocks_view') }}"
    )
    html = _render(
        app,
        template,
        '/blocks',
        page=_FakePage(pages=3, page=2, prev=True, nxt=True),
    )
    assert 'class="pagination"' in html
    assert 'page=1' in html
    assert 'page=3' in html
    assert 'active' in html


def test_custom_page_param_preserves_other_query_and_path_args(app):
    # A page with a path arg (subject) and two paginated lists: the second list
    # uses page_param='txn_page' and must preserve the first list's current
    # `page` query arg plus the path arg across its links. (subject_view stands
    # in for any path-arg route; address_view is added in a later task.)
    template = (
        "{% from '_pagination.html' import render_pagination %}"
        "{{ render_pagination(page, 'browser.subject_view', 'txn_page') }}"
    )
    subj = 'Z29ibGlucw'  # encode_subject('goblins'), a valid encoded subject
    html = _render(
        app,
        template,
        f'/subject/{subj}?page=2&txn_page=1',
        page=_FakePage(pages=3, page=1, prev=False, nxt=True),
    )
    # txn_page is the param being driven by this list
    assert 'txn_page=2' in html
    # the sibling list's current page is preserved — asserted on the txn_page=3
    # link so 'page=2' can't be a substring of a 'txn_page=2' match (& is
    # HTML-escaped to &amp; in the rendered output)
    assert 'page=2&amp;txn_page=3' in html
    # the path arg is preserved in the generated URLs
    assert f'/subject/{subj}' in html
    # no link carries the page_param twice (the original value must be popped,
    # not appended alongside the override)
    for link in html.split('href="')[1:]:
        url = link.split('"')[0]
        assert url.count('txn_page=') <= 1


def test_single_page_renders_nothing(app):
    template = (
        "{% from '_pagination.html' import render_pagination %}"
        "{{ render_pagination(page, 'browser.subject_view', 'txn_page') }}"
    )
    subj = 'Z29ibGlucw'
    html = _render(app, template, f'/subject/{subj}', page=_FakePage(pages=1))
    assert 'class="pagination"' not in html
    assert html.strip() == ''
