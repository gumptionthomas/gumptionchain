import os

from gumptionchain import create_app

app = create_app()


if __name__ == '__main__':
    # Dev-only entrypoint: production serves via gunicorn (see Dockerfile).
    # Binding all interfaces is intentional so the dev/container server is
    # reachable from outside the container.
    port = int(os.environ.get('PORT', '8080'))
    app.run(debug=True, host='0.0.0.0', port=port)  # noqa: S104
