from flask import Flask

from gumptionchain import create_app
from gumptionchain.database import db


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
