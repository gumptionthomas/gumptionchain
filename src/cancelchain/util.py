import datetime
from urllib.parse import urlparse, urlunparse

ISO8601 = '%Y-%m-%dT%H:%M:%SZ'
COMPACT_ISO8601 = '%Y%m%dT%H%M%SZ'


def host_address(url):
    parsed = urlparse(url)
    hostname = f'{parsed.hostname}'
    if parsed.port:
        hostname = f'{parsed.hostname}:{parsed.port}'
    return (
        urlunparse((parsed.scheme, f'{hostname}', '', '', '', '')),
        parsed.username,
    )


def iso_2_dt(s, fmt=ISO8601):
    dt = datetime.datetime.strptime(s, fmt)
    return dt.replace(tzinfo=datetime.UTC)


def dt_2_iso(dt, fmt=ISO8601):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    dt = dt.astimezone(tz=datetime.UTC)
    return dt.strftime(fmt)


def ciso_2_dt(s):
    return iso_2_dt(s, fmt=COMPACT_ISO8601)


def dt_2_ciso(dt):
    return dt_2_iso(dt, fmt=COMPACT_ISO8601)


def now():
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0)


def now_iso():
    return dt_2_iso(now())
