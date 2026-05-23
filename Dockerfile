FROM python:3.14.5

ENV PYTHONUNBUFFERED True
ENV APP_HOME /app

WORKDIR ${APP_HOME}

COPY src/cancelchain ${APP_HOME}/cancelchain

RUN pip install --no-cache-dir -r cancelchain/requirements.txt
RUN pip install -e cancelchain

COPY app.py ${APP_HOME}/app.py

# Run the web service on container startup.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
