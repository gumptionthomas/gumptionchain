import httpx


def test_base_renders_theme_scaffolding(app, test_client):
    # Any page that extends base.html carries the dark-mode scaffolding.
    with app.app_context():
        resp = test_client.get('/')
    assert resp.status_code == httpx.codes.OK
    body = resp.get_data(as_text=True)
    # No-JS default theme is set on <html>.
    assert 'data-bs-theme="light"' in body
    # FOUC guard reads the stored override key before first paint.
    assert 'gc-theme' in body
    # The toggle button and its module are present.
    assert 'id="theme-toggle"' in body
    assert 'js/theme.mjs' in body


def test_base_dark_override_neutralizes_fixed_light_utilities(app, test_client):
    # Bootstrap's .bg-light / .text-dark are fixed colors that ignore
    # data-bs-theme, so shared templates using them render a white card (and
    # invisible dark links) in dark mode. A base-only override recolors them
    # under [data-bs-theme="dark"] without touching those shared templates.
    with app.app_context():
        body = test_client.get('/').get_data(as_text=True)
    assert '[data-bs-theme="dark"] .bg-light' in body
    assert '[data-bs-theme="dark"] a.text-dark' in body


def test_base_uses_bootstrap_5_3_3(app, test_client):
    # The bump to 5.3.3 is what makes data-bs-theme work; pin it so a stray
    # revert to 5.1.3 (no native dark mode) fails loudly.
    with app.app_context():
        body = test_client.get('/').get_data(as_text=True)
    assert 'bootstrap@5.3.3/dist/css/bootstrap.min.css' in body
    assert 'bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js' in body
    assert 'bootstrap@5.1.3' not in body
