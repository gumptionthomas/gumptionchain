import os

from cancelchain.config import EnvAppSettings


def test_environ_settings():
    s = EnvAppSettings.from_env()
    assert s.READER_ADDRESSES == [
        'CCB9JajrPayCVUqRU7RrDAVfZ1QPj135moCyrKkNwMwEtRCC',
        'CC3QfbBDAEktCNPzcTg8DPz4a1qY5zMKvenQjr5nFoaKXaCC',
    ]


def test_flask_config(config_app):
    # `config_app` builds `create_app()` with no overrides, so
    # `SECRET_KEY` should reflect whatever `Flask.config.from_prefixed_env`
    # loaded from `FLASK_SECRET_KEY` (set in `tests/.test.env`).
    assert config_app.config.get('SECRET_KEY') == os.environ['FLASK_SECRET_KEY']
