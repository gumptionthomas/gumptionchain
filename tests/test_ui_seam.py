from flask import Flask

from gumptionchain import create_app
from gumptionchain.database import db
from gumptionchain.payload import encode_subject
from gumptionchain.wallet import Wallet

_CONSUMER_BASE = (
    '<!doctype html><html><head>'
    '<title>{% block title %}{% endblock %}</title>'
    '{% block head %}{% endblock %}</head><body>'
    '<div id="custom-skin">SKINNED</div>'
    '{% block nav %}{% endblock %}'
    '<main>{% block content %}{% endblock %}</main>'
    '{% block footer %}{% endblock %}'
    '{% block scripts %}{% endblock %}'
    '</body></html>'
)


def _consumer_app(tmp_path):
    tdir = tmp_path / 'templates'
    tdir.mkdir()
    (tdir / 'base.html').write_text(_CONSUMER_BASE)
    consumer = Flask('consumer_app', template_folder=str(tdir))
    db_uri = f'sqlite:///{tmp_path / "seam.sqlite"}'
    return create_app(
        app=consumer,
        config_map={
            'TESTING': True,
            'SECRET_KEY': 'x',
            'SQLALCHEMY_DATABASE_URI': db_uri,
            'NODE_HOST': 'http://localhost',
            'READER_ADDRESSES': ['*'],
        },
    )


def test_consumer_base_html_reskins_base_pages(tmp_path):
    # A consumer app (like the hub) creates its own Flask with an app-level
    # templates/ dir. Flask resolves app templates before blueprint templates,
    # so the consumer's base.html must re-skin base's pages.
    tdir = tmp_path / 'templates'
    tdir.mkdir()
    (tdir / 'base.html').write_text(
        '<!doctype html><html><head>'
        '<title>{% block title %}{% endblock %}</title>'
        '{% block head %}{% endblock %}</head><body>'
        '<div id="custom-skin">SKINNED</div>'
        '{% block nav %}{% endblock %}'
        '<main>{% block content %}{% endblock %}</main>'
        '{% block footer %}{% endblock %}'
        '{% block scripts %}{% endblock %}'
        '</body></html>'
    )
    consumer = Flask('consumer_app', template_folder=str(tdir))
    db_uri = f'sqlite:///{tmp_path / "seam.sqlite"}'
    app = create_app(
        app=consumer,
        config_map={
            'TESTING': True,
            'SECRET_KEY': 'x',
            'SQLALCHEMY_DATABASE_URI': db_uri,
            'NODE_HOST': 'http://localhost',
            'READER_ADDRESSES': ['*'],
        },
    )
    with app.app_context():
        db.create_all()
        client = app.test_client()
        resp = client.get('/')
        assert resp.status_code == 200
        assert b'SKINNED' in resp.data  # consumer skin won over blueprint
        assert b'No chain' in resp.data  # base index content still rendered


def test_consumer_base_html_reskins_blocks_page(tmp_path):
    # Same seam check for the blocks list page: the consumer's base.html must
    # re-skin it while the blueprint's blocks.html content still renders.
    tdir = tmp_path / 'templates'
    tdir.mkdir()
    (tdir / 'base.html').write_text(
        '<!doctype html><html><head>'
        '<title>{% block title %}{% endblock %}</title>'
        '{% block head %}{% endblock %}</head><body>'
        '<div id="custom-skin">SKINNED</div>'
        '{% block nav %}{% endblock %}'
        '<main>{% block content %}{% endblock %}</main>'
        '{% block footer %}{% endblock %}'
        '{% block scripts %}{% endblock %}'
        '</body></html>'
    )
    consumer = Flask('consumer_app', template_folder=str(tdir))
    db_uri = f'sqlite:///{tmp_path / "seam.sqlite"}'
    app = create_app(
        app=consumer,
        config_map={
            'TESTING': True,
            'SECRET_KEY': 'x',
            'SQLALCHEMY_DATABASE_URI': db_uri,
            'NODE_HOST': 'http://localhost',
            'READER_ADDRESSES': ['*'],
        },
    )
    with app.app_context():
        db.create_all()
        client = app.test_client()
        resp = client.get('/blocks')
        assert resp.status_code == 200
        assert b'SKINNED' in resp.data  # consumer skin won over blueprint
        assert b'No blocks' in resp.data  # base blocks content still rendered


def test_consumer_base_html_reskins_subjects_page(tmp_path):
    # Seam check for the subjects leaderboard index page.
    app = _consumer_app(tmp_path)
    with app.app_context():
        db.create_all()
        client = app.test_client()
        resp = client.get('/subjects')
        assert resp.status_code == 200
        assert b'SKINNED' in resp.data  # consumer skin won over blueprint
        # base subjects content still rendered
        assert b'No subjects staked yet' in resp.data


def test_consumer_base_html_reskins_subject_detail_page(tmp_path):
    # Seam check for the per-subject detail page (valid encoded subject).
    app = _consumer_app(tmp_path)
    with app.app_context():
        db.create_all()
        client = app.test_client()
        resp = client.get(f'/subject/{encode_subject("goblins")}')
        assert resp.status_code == 200
        assert b'SKINNED' in resp.data  # consumer skin won over blueprint
        assert b'goblins' in resp.data  # base subject content still rendered


def test_consumer_base_html_reskins_addresses_page(tmp_path):
    # Seam check for the addresses leaderboard index page.
    app = _consumer_app(tmp_path)
    with app.app_context():
        db.create_all()
        client = app.test_client()
        resp = client.get('/addresses')
        assert resp.status_code == 200
        assert b'SKINNED' in resp.data  # consumer skin won over blueprint
        # base addresses content still rendered
        assert b'No addresses with a balance yet' in resp.data


def test_consumer_base_html_reskins_address_detail_page(tmp_path):
    # Seam check for the per-address detail page (valid GC...GC address).
    app = _consumer_app(tmp_path)
    with app.app_context():
        db.create_all()
        client = app.test_client()
        resp = client.get(f'/address/{Wallet().address}')
        assert resp.status_code == 200
        assert b'SKINNED' in resp.data  # consumer skin won over blueprint


def test_consumer_base_html_reskins_mempool_page(tmp_path):
    # Seam check for the mempool/pending pool page.
    app = _consumer_app(tmp_path)
    with app.app_context():
        db.create_all()
        client = app.test_client()
        resp = client.get('/mempool')
        assert resp.status_code == 200
        assert b'SKINNED' in resp.data  # consumer skin won over blueprint
        # base mempool content still rendered
        assert b'Mempool is empty' in resp.data
