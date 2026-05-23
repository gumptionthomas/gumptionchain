import requests
from celery import Celery

celery = Celery(__name__)


def init_tasks(app):
    celery.conf.update(app.config)

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery


@celery.task()
def post_process(url, data, headers=None):
    r = requests.post(url, headers=headers, data=data, timeout=360)
    r.raise_for_status()
