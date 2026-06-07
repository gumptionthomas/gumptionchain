# EGU #5 — gumption-hub Foundation + Verify Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a new `gumption-hub` repo that embeds `gumptionchain` as a non-milling node and ships the "Verified on GumptionChain" verify card (`/verify` + `/proof/<hash>` + Bluesky OG unfurl).

**Architecture:** A new Flask app owns its UI and calls `gumptionchain.create_app(app=<own Flask>, register_browser=False)` to become a full node (the thecancelbutton pattern). It adds a `hub` blueprint (`/`, `/about`, `/verify`, `POST /proof`, `GET /proof/<hash>`, `GET /proof/<hash>/og.png`, `GET /tx/<txid>/provenance.json`), a content-addressed `stored_proof` table, an SVG→PNG OG card via `cairosvg`, and the existing browser wallet ESM (vendored) running `verifyStake` client-side against the hub's own public provenance endpoint.

**Tech Stack:** Python 3.12+, Flask 3, Flask-SQLAlchemy 2.0, uv + uv_build, `gumptionchain` (path/git dependency), `cairosvg` (SVG→PNG), Bootstrap 5.3.3 + Inter/Righteous (the 2B2F design system, ported from `acquire-llm`), vanilla ESM wallet modules, `node --test` for JS, pytest for Python. Zero npm.

**Spec:** `docs/superpowers/specs/2026-06-06-egu-5-hub-foundation-verify-card-design.md` (in the gumptionchain repo).

**Where this runs:** Tasks 1 scaffolds the repo from the gumptionchain session (or by hand). **Tasks 2+ are implemented in a Claude Code session rooted at `/home/gumptionthomas/Development/gumption-hub`.** All paths below are relative to that repo unless prefixed with `gumptionchain/`.

**Important environment note:** Always run shell commands from the gumption-hub repo root (`cd /home/gumptionthomas/Development/gumption-hub`) — an earlier `cd` into a sibling dir persists across calls.

---

## File Structure

```
gumption-hub/
├── CLAUDE.md                         # hub-specific conventions
├── README.md
├── pyproject.toml                    # uv_build; depends on gumptionchain + cairosvg
├── .gitignore
├── .python-version                   # 3.12
├── scripts/
│   └── sync_wallet.py                # copy runtime wallet .mjs from ../gumptionchain
├── src/gumption_hub/
│   ├── __init__.py                   # create_hub_app() + CLI
│   ├── app.py                        # the app factory body
│   ├── hub.py                        # the 'hub' Blueprint (all hub routes)
│   ├── models.py                     # StoredProof (on gumptionchain's db)
│   ├── proofs.py                     # content-hash + store/get helpers
│   ├── provenance.py                 # in-process public provenance lookup
│   ├── og.py                         # SVG-jinja → PNG via cairosvg
│   ├── templates/
│   │   ├── base.html                 # 2B2F base layout (nav, footer, theme)
│   │   ├── landing.html              # / (EGU front door, minimal)
│   │   ├── about.html                # /about (minimal)
│   │   ├── verify.html               # /verify (paste & check)
│   │   ├── proof.html                # /proof/<hash> (card + live verify)
│   │   ├── 404.html                  # styled not-found
│   │   └── og/
│   │       └── proof.svg.jinja       # the ledger card as SVG (for OG png)
│   └── static/
│       ├── css/hub.css               # ported 2B2F design system + hub bits
│       ├── js/verify-glue.mjs        # proof → verifyStake → DOM verdict
│       ├── fonts/                     # Righteous + Inter (for fontconfig/OG)
│       └── wallet/                    # VENDORED runtime .mjs (sync_wallet.py)
└── tests/
    ├── conftest.py                   # hub app fixture (mirrors gumptionchain)
    ├── test_app.py
    ├── test_pages.py
    ├── test_provenance.py
    ├── test_proofs.py
    ├── test_proof_api.py
    ├── test_proof_page.py
    ├── test_og.py
    ├── test_verify_page.py
    ├── test_wallet_vendored.py
    └── js/verify-glue.test.mjs
```

---

## Task 1: Scaffold the gumption-hub repo

**Files:**
- Create: `/home/gumptionthomas/Development/gumption-hub/` (new repo)
- Create: `pyproject.toml`, `CLAUDE.md`, `README.md`, `.gitignore`, `.python-version`, `src/gumption_hub/__init__.py`

*Run from the gumptionchain session (writes outside cwd may prompt for permission).*

- [ ] **Step 1: Create the directory and git repo**

```bash
mkdir -p /home/gumptionthomas/Development/gumption-hub/src/gumption_hub
cd /home/gumptionthomas/Development/gumption-hub
git init
echo "3.12" > .python-version
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[build-system]
requires = ["uv_build>=0.5,<1.0"]
build-backend = "uv_build"

[project]
name = "gumption-hub"
version = "0.1.0"
description = "The Extended Gumption Universe hub + canonical GumptionChain node"
readme = "README.md"
license = "MIT"
requires-python = ">=3.12"
authors = [{ name = "Thomas Bohmbach Jr", email = "tom@gumption.com" }]
dependencies = [
  "gumptionchain",
  "cairosvg>=2.7",
]

[project.scripts]
gumption-hub = "gumption_hub:cli"

[tool.uv.sources]
gumptionchain = { path = "../gumptionchain", editable = true }

[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-cov>=5.0",
  "pytest-dotenv>=0.5",
  "time-machine>=2.14",
  "ruff>=0.7",
  "mypy>=1.13",
  "pre-commit>=4.0",
]

[tool.ruff]
target-version = "py312"
line-length = 80

[tool.ruff.lint]
select = ["A","B","C","DTZ","E","EM","F","FBT","I","ICN","ISC","N","PLC","PLE","PLR","PLW","Q","RUF","S","SIM","T","TID","UP","W","YTT"]

[tool.ruff.lint.flake8-quotes]
inline-quotes = "single"

[tool.ruff.format]
quote-style = "single"

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S101"]

[tool.mypy]
strict = true
files = ["src/gumption_hub"]

[tool.pytest.ini_options]
env_files = ["tests/.test.env"]
```

> Note: `[tool.uv.sources]` path-dep is for local dev. For CI/deploy, swap to a pinned git dependency: `gumptionchain = { git = "https://github.com/gumptionthomas/gumptionchain", rev = "<sha>" }` (Task 13).

- [ ] **Step 3: Write `src/gumption_hub/__init__.py` (minimal, expanded in Task 2)**

```python
from __future__ import annotations

import click
from flask.cli import FlaskGroup

from gumption_hub.app import create_hub_app


@click.group(cls=FlaskGroup, create_app=create_hub_app, add_version_option=False)
def cli() -> None:
    """gumption-hub management CLI."""
```

- [ ] **Step 4: Write `CLAUDE.md`** (hub conventions — mirror gumptionchain's bar)

```markdown
# CLAUDE.md — gumption-hub

The Extended Gumption Universe (EGU) hub. A Flask app that embeds the
`gumptionchain` package and runs as a **non-milling node** (peers = millers,
Postgres in prod), while serving the EGU front door and the "Verified on
GumptionChain" verify card.

## Commands
- `uv sync` — install (uses the ../gumptionchain path dependency locally)
- `uv run pytest` — tests
- `uv run ruff check src tests && uv run ruff format --check src tests`
- `uv run mypy`
- `uv run python scripts/sync_wallet.py` — refresh the vendored wallet ESM
- `node --test tests/js` — JS glue tests (zero npm)

## Conventions
- Never push to main; branch + PR; Conventional Commits; squash-merge --delete-branch.
- ruff + mypy strict + pytest are CI gates. Zero npm.
- The app embeds gumptionchain via `create_app(app=<own Flask>, register_browser=False)`.
  Never re-implement node/consensus logic here — import it.
- `/api/*` is gumptionchain's authed peer protocol; the hub adds only public
  read endpoints + UI. Provenance for the verifier is the hub's public
  `/tx/<txid>/provenance.json`, never the authed `/api/transaction/<txid>`.
```

- [ ] **Step 5: Write `.gitignore` and `README.md`**

```bash
cat > .gitignore <<'EOF'
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
instance/
*.sqlite
.env
EOF
cat > README.md <<'EOF'
# gumption-hub

The Extended Gumption Universe hub + canonical GumptionChain node.
See `docs` in the gumptionchain repo for the design spec.
EOF
```

- [ ] **Step 6: Sync and verify it imports**

Run:
```bash
cd /home/gumptionthomas/Development/gumption-hub
uv sync
```
Expected: resolves `gumptionchain` from `../gumptionchain`, installs `cairosvg`.

- [ ] **Step 7: Create the GitHub repo and commit**

```bash
cd /home/gumptionthomas/Development/gumption-hub
git add -A
git commit -m "chore: scaffold gumption-hub (embeds gumptionchain)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
gh repo create gumptionthomas/gumption-hub --private --source=. --remote=origin --push
```

---

## Task 2: App factory embedding gumptionchain + test harness

**Files:**
- Create: `src/gumption_hub/app.py`
- Create: `src/gumption_hub/hub.py` (stub blueprint)
- Create: `tests/.test.env`, `tests/conftest.py`, `tests/test_app.py`

- [ ] **Step 1: Write the failing boot test**

`tests/test_app.py`:
```python
def test_hub_app_boots_with_node_api(test_client):
    # The embedded gumptionchain node registers /api/* — its presence proves
    # the hub booted as a node, not just a bare Flask app.
    rules = {r.rule for r in test_client.application.url_map.iter_rules()}
    assert any(rule.startswith('/api/') for rule in rules)


def test_hub_blueprint_registered(test_client):
    rules = {r.rule for r in test_client.application.url_map.iter_rules()}
    assert '/' in rules
```

- [ ] **Step 2: Write `tests/.test.env`** (loaded by pytest-dotenv)

```bash
FLASK_SECRET_KEY=test-secret-key-for-egu5-hub-32bytes
GC_READER_ADDRESSES=["*"]
```

- [ ] **Step 3: Write `tests/conftest.py`** (mirrors gumptionchain's app fixture)

```python
from tempfile import NamedTemporaryFile, TemporaryDirectory

import pytest
from gumptionchain.database import db
from gumptionchain.wallet import Wallet

from gumption_hub.app import create_hub_app

READER_WALLET = Wallet()
TEST_SECRET_KEY = 'test-secret-key-for-egu5-hub-32bytes'


@pytest.fixture
def app():
    with (
        NamedTemporaryFile(suffix='.sqlite') as db_file,
        TemporaryDirectory() as walletdir,
    ):
        READER_WALLET.to_file(walletdir=walletdir)
        app = create_hub_app(
            config_map={
                'TESTING': True,
                'WTF_CSRF_ENABLED': False,
                'SECRET_KEY': TEST_SECRET_KEY,
                'SQLALCHEMY_DATABASE_URI': f'sqlite:///{db_file.name}',
                'NODE_HOST': 'http://localhost:8080',
                'PEERS': [],
                'WALLET_DIR': walletdir,
                'READER_ADDRESSES': ['*'],
            }
        )
        with app.app_context():
            db.create_all()
        yield app


@pytest.fixture
def test_client(app):
    return app.test_client()
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd /home/gumptionthomas/Development/gumption-hub && uv run pytest tests/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: gumption_hub.app` (not written yet).

- [ ] **Step 5: Write `src/gumption_hub/hub.py` (stub)**

```python
from __future__ import annotations

from flask import Blueprint

hub_bp = Blueprint('hub', __name__)


@hub_bp.route('/')
def landing() -> str:
    return 'gumption-hub'
```

- [ ] **Step 6: Write `src/gumption_hub/app.py`**

```python
from __future__ import annotations

from typing import Any

from flask import Flask
from gumptionchain import create_app

from gumption_hub.hub import hub_bp


def create_hub_app(config_map: dict[str, Any] | None = None) -> Flask:
    """Build the EGU hub app: a gumptionchain node (non-milling, default chain
    UI suppressed) with the hub's own blueprint mounted on top.
    """
    app = Flask(__name__)
    create_app(app=app, config_map=config_map, register_browser=False)
    app.register_blueprint(hub_bp)
    return app
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS (2 passed).

- [ ] **Step 8: Commit**

```bash
cd /home/gumptionthomas/Development/gumption-hub
git add -A
git commit -m "feat: app factory embedding gumptionchain as a non-milling node

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Public provenance endpoint

**Files:**
- Create: `src/gumption_hub/provenance.py`
- Modify: `src/gumption_hub/hub.py`
- Create: `tests/test_provenance.py`

The verifier's `fetchProvenance` cannot call the authed `/api/transaction/<txid>`; the hub exposes a public, in-process read.

- [ ] **Step 1: Write the failing test**

`tests/test_provenance.py`:
```python
from unittest.mock import patch

PROV = {
    'address': 'GCxGC', 'status': 'canonical', 'confirmations': 3,
    'height': 41802, 'block_hash': 'abc',
    'outflows': [{'kind': 'opposition', 'subject': 'goblins', 'amount': 300}],
}


def test_provenance_json_returns_lookup(test_client):
    with patch('gumption_hub.provenance.lookup_provenance', return_value=PROV):
        resp = test_client.get('/tx/' + ('a' * 64) + '/provenance.json')
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'canonical'
    assert resp.get_json()['txid'] == 'a' * 64


def test_provenance_json_404_when_unknown(test_client):
    with patch('gumption_hub.provenance.lookup_provenance', return_value=None):
        resp = test_client.get('/tx/' + ('b' * 64) + '/provenance.json')
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_provenance.py -v`
Expected: FAIL — route `/tx/<...>/provenance.json` not found (404 for both, second assertion on first test fails earlier).

- [ ] **Step 3: Write `src/gumption_hub/provenance.py`**

```python
from __future__ import annotations

from typing import Any

from gumptionchain.api import node_lc_dao
from gumptionchain.models import ChainDAO


def lookup_provenance(txid: str) -> dict[str, Any] | None:
    """Public, in-process provenance lookup — the same code path the authed
    /api/transaction/<txid> view uses, minus authentication. Returns the
    #176a provenance dict, or None if the txn is unknown.
    """
    _, lc, _ = node_lc_dao()
    if lc is not None:
        return lc.transaction_provenance(txid)
    return ChainDAO.pending_provenance(txid)
```

- [ ] **Step 4: Add the route to `src/gumption_hub/hub.py`**

Add imports and route:
```python
from flask import Blueprint, jsonify

from gumption_hub import provenance

# ... existing hub_bp + landing ...


@hub_bp.route('/tx/<mill_hash:txid>/provenance.json')
def tx_provenance(txid: str):
    prov = provenance.lookup_provenance(txid)
    if prov is None:
        return jsonify({'error': 'transaction not found'}), 404
    return jsonify({'txid': txid, **prov})
```

> The `mill_hash` URL converter is registered on the app by gumptionchain's `init_app`, so `<mill_hash:txid>` works here.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_provenance.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: public in-process provenance endpoint /tx/<txid>/provenance.json

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Proof storage — content-addressed model + helpers

**Files:**
- Create: `src/gumption_hub/models.py`
- Create: `src/gumption_hub/proofs.py`
- Create: `tests/test_proofs.py`

- [ ] **Step 1: Write the failing test**

`tests/test_proofs.py`:
```python
from gumption_hub import proofs

PROOF = {
    'scheme': 'gc-msg-v1', 'address': 'GCxGC', 'public_key': 'pk',
    'signature': 'sig', 'timestamp': '1700000000',
    'message': '{"txid":"' + 'a' * 64 + '","kind":"opposition",'
               '"subject":"goblins","amount":300}',
}


def test_content_hash_is_deterministic_and_order_independent():
    reordered = dict(reversed(list(PROOF.items())))
    assert proofs.compute_content_hash(PROOF) == proofs.compute_content_hash(reordered)
    assert len(proofs.compute_content_hash(PROOF)) == 64


def test_store_proof_is_idempotent(app):
    with app.app_context():
        a = proofs.store_proof(PROOF)
        b = proofs.store_proof(PROOF)
        assert a.content_hash == b.content_hash
        assert proofs.get_stored_proof(a.content_hash).txid == 'a' * 64
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_proofs.py -v`
Expected: FAIL — `ModuleNotFoundError: gumption_hub.proofs`.

- [ ] **Step 3: Write `src/gumption_hub/models.py`**

```python
from __future__ import annotations

import datetime

from gumptionchain.database import db
from sqlalchemy.orm import Mapped, mapped_column


class StoredProof(db.Model):  # type: ignore[name-defined,misc]
    __tablename__ = 'stored_proof'

    content_hash: Mapped[str] = mapped_column(primary_key=True)
    proof_json: Mapped[str] = mapped_column(nullable=False)
    txid: Mapped[str] = mapped_column(nullable=False, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(nullable=False)
```

> `StoredProof` is registered on gumptionchain's shared `db` metadata, so the test fixture's `db.create_all()` creates `stored_proof` alongside the chain tables. (Prod creation: Task 13's hub `init` command.)

- [ ] **Step 4: Write `src/gumption_hub/proofs.py`**

```python
from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any

from gumptionchain.attestation import parse_stake_attestation
from gumptionchain.database import db

from gumption_hub.models import StoredProof


def canonical_proof_bytes(proof: dict[str, Any]) -> bytes:
    """Deterministic byte encoding of a proof envelope for content-addressing:
    sorted keys, compact separators, UTF-8 preserved. Order-independent.
    """
    return json.dumps(
        proof, sort_keys=True, separators=(',', ':'), ensure_ascii=False
    ).encode('utf-8')


def compute_content_hash(proof: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_proof_bytes(proof)).hexdigest()


def store_proof(proof: dict[str, Any]) -> StoredProof:
    """Store a proof idempotently, keyed by its content hash. The caller is
    responsible for having validated the proof (see the POST /proof view).
    """
    claim = parse_stake_attestation(proof)  # raises BadAttestationError
    content_hash = compute_content_hash(proof)
    existing = db.session.get(StoredProof, content_hash)
    if existing is not None:
        return existing
    row = StoredProof(
        content_hash=content_hash,
        proof_json=json.dumps(proof, separators=(',', ':'), ensure_ascii=False),
        txid=claim['txid'],
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.session.add(row)
    db.session.commit()
    return row


def get_stored_proof(content_hash: str) -> StoredProof | None:
    return db.session.get(StoredProof, content_hash)
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_proofs.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: content-addressed StoredProof model + store/get helpers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: POST /proof endpoint

**Files:**
- Modify: `src/gumption_hub/hub.py`
- Create: `tests/test_proof_api.py`

- [ ] **Step 1: Write the failing test**

`tests/test_proof_api.py`:
```python
from gumptionchain.attestation import sign_stake_attestation
from gumptionchain.wallet import Wallet

CLAIM = {
    'txid': 'a' * 64, 'kind': 'opposition', 'subject': 'goblins', 'amount': 300,
}


def _signed_proof():
    return sign_stake_attestation(Wallet(), CLAIM)


def test_post_proof_stores_and_returns_url(test_client):
    proof = _signed_proof()
    resp = test_client.post('/proof', json=proof)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['url'] == '/proof/' + body['id']
    assert len(body['id']) == 64


def test_post_proof_is_idempotent(test_client):
    proof = _signed_proof()
    first = test_client.post('/proof', json=proof).get_json()['id']
    second = test_client.post('/proof', json=proof).get_json()['id']
    assert first == second


def test_post_proof_rejects_malformed(test_client):
    resp = test_client.post('/proof', json={'not': 'a proof'})
    assert resp.status_code == 400


def test_post_proof_rejects_bad_signature(test_client):
    proof = _signed_proof()
    proof['signature'] = 'AAAA'  # valid base64, wrong signature
    resp = test_client.post('/proof', json=proof)
    assert resp.status_code == 400
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_proof_api.py -v`
Expected: FAIL — `/proof` POST route not found (405/404).

- [ ] **Step 3: Add the route to `src/gumption_hub/hub.py`**

Add imports and route. The cheap submit guard rejects anything that isn't a
structurally valid, authentically signed attestation; on-chain/consistency checks
happen at view time (client-side) against live provenance.

```python
from flask import request

from gumptionchain.attestation import BadAttestationError
from gumptionchain.message import verify_message

from gumption_hub import proofs

# 16 KB is far above any real proof; rejects oversized bodies cheaply.
_MAX_PROOF_BYTES = 16 * 1024


@hub_bp.route('/proof', methods=['POST'])
def submit_proof():
    raw = request.get_data()
    if len(raw) > _MAX_PROOF_BYTES:
        return jsonify({'error': 'proof too large'}), 413
    proof = request.get_json(silent=True)
    if not isinstance(proof, dict):
        return jsonify({'error': 'expected a JSON proof object'}), 400
    try:
        sig = verify_message(proof)
    except Exception:  # noqa: BLE001 — malformed envelope -> bad request
        return jsonify({'error': 'invalid proof'}), 400
    if not sig.get('valid'):
        return jsonify({'error': 'invalid signature'}), 400
    try:
        row = proofs.store_proof(proof)
    except BadAttestationError:
        return jsonify({'error': 'not a stake attestation'}), 400
    return jsonify({'id': row.content_hash, 'url': f'/proof/{row.content_hash}'})
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_proof_api.py -v`
Expected: PASS (4 passed). If `verify_message`'s return key differs, inspect `gumptionchain/src/gumptionchain/message.py::verify_message` and adjust the `.get('valid')` access to match its actual return dict.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: POST /proof — validate + content-address + store, returns share link

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Vendor the wallet ESM

**Files:**
- Create: `scripts/sync_wallet.py`
- Create: `tests/test_wallet_vendored.py`
- Create (generated): `src/gumption_hub/static/wallet/*.mjs`

- [ ] **Step 1: Write the failing test**

`tests/test_wallet_vendored.py`:
```python
from pathlib import Path

WALLET_DIR = Path('src/gumption_hub/static/wallet')
REQUIRED = [
    'gc-attestation.mjs', 'gc-message.mjs', 'gc-errors.mjs',
    'gc-wallet.mjs', 'gc-crypto.mjs', 'gc-sig.mjs', 'index.mjs',
]


def test_runtime_wallet_modules_are_vendored():
    for name in REQUIRED:
        assert (WALLET_DIR / name).is_file(), f'missing vendored {name}'


def test_no_test_or_cli_modules_vendored():
    for p in WALLET_DIR.glob('*.mjs'):
        assert not p.name.endswith('.test.mjs')
        assert not p.name.endswith('-cli.mjs')
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_wallet_vendored.py -v`
Expected: FAIL — directory/files absent.

- [ ] **Step 3: Write `scripts/sync_wallet.py`**

```python
"""Copy the runtime wallet ESM modules from the gumptionchain checkout into
the hub's static dir. Excludes *.test.mjs and *-cli.mjs (dev-only).

Usage: uv run python scripts/sync_wallet.py [--source ../gumptionchain]
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

DEST = Path(__file__).resolve().parent.parent / 'src/gumption_hub/static/wallet'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default='../gumptionchain')
    args = parser.parse_args()
    src = Path(args.source).resolve() / 'clients/wallet'
    if not src.is_dir():
        msg = f'wallet source not found: {src}'
        raise SystemExit(msg)
    DEST.mkdir(parents=True, exist_ok=True)
    for mjs in sorted(src.glob('*.mjs')):
        if mjs.name.endswith('.test.mjs') or mjs.name.endswith('-cli.mjs'):
            continue
        shutil.copy2(mjs, DEST / mjs.name)
        print(f'vendored {mjs.name}')  # noqa: T201


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run the sync script**

Run: `cd /home/gumptionthomas/Development/gumption-hub && uv run python scripts/sync_wallet.py`
Expected: prints `vendored gc-attestation.mjs`, `vendored gc-message.mjs`, etc.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_wallet_vendored.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit (including the vendored files)**

```bash
git add -A
git commit -m "feat: vendor runtime wallet ESM into hub static via sync script

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Verify glue JS module + node test

**Files:**
- Create: `src/gumption_hub/static/js/verify-glue.mjs`
- Create: `tests/js/verify-glue.test.mjs`

- [ ] **Step 1: Write the failing test**

`tests/js/verify-glue.test.mjs`:
```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Wallet } from '../../src/gumption_hub/static/wallet/gc-wallet.mjs';
import { signStakeAttestation } from '../../src/gumption_hub/static/wallet/gc-attestation.mjs';
import { runVerify } from '../../src/gumption_hub/static/js/verify-glue.mjs';

const CLAIM = { txid: 'a'.repeat(64), kind: 'opposition', subject: 'goblins', amount: 300 };

function provenanceFor(address) {
  return {
    txid: CLAIM.txid, address, status: 'canonical', confirmations: 3,
    outflows: [{ kind: 'opposition', subject: 'goblins', amount: 300 }],
  };
}

test('runVerify resolves a valid verdict using injected fetchProvenance', async () => {
  const w = await Wallet.generate();
  const proof = await signStakeAttestation(w, CLAIM);
  const verdict = await runVerify(proof, {
    fetchProvenance: async () => provenanceFor(await w.address()),
  });
  assert.equal(verdict.valid, true);
  assert.deepEqual(verdict.checks, { signature: true, onchain: true, consistent: true });
});

test('runVerify reports txn-not-found when provenance is null', async () => {
  const w = await Wallet.generate();
  const proof = await signStakeAttestation(w, CLAIM);
  const verdict = await runVerify(proof, { fetchProvenance: async () => null });
  assert.equal(verdict.valid, false);
  assert.ok(verdict.reasons.includes('txn-not-found'));
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/gumptionthomas/Development/gumption-hub && node --test tests/js`
Expected: FAIL — cannot find `verify-glue.mjs`.

- [ ] **Step 3: Write `src/gumption_hub/static/js/verify-glue.mjs`**

```javascript
// Hub verify glue: run verifyStake (from the vendored wallet module) over a
// proof, fetching provenance from the hub's public endpoint. Pure logic in
// runVerify (fetchProvenance injectable for tests); bindProofPage wires it to
// the DOM on /proof and /verify.
import { verifyStake } from '../wallet/gc-attestation.mjs';

// Adapter: hub public provenance endpoint. 404 -> null (unknown txn);
// other failures propagate so they are NOT misreported as 'txn-not-found'.
export function hubFetchProvenance(origin = '') {
  return async (txid) => {
    const resp = await fetch(`${origin}/tx/${txid}/provenance.json`);
    if (resp.status === 404) return null;
    if (!resp.ok) {
      throw new Error(`provenance fetch failed: ${resp.status}`);
    }
    return resp.json();
  };
}

export async function runVerify(proof, { fetchProvenance, minConfirmations } = {}) {
  return verifyStake(proof, {
    fetchProvenance: fetchProvenance ?? hubFetchProvenance(),
    minConfirmations,
  });
}

// Light up the three checks + overall seal in the DOM. Expects elements with
// data-check="signature|onchain|consistent" and id="verdict-seal".
export function renderVerdict(verdict, root = document) {
  for (const key of ['signature', 'onchain', 'consistent']) {
    const el = root.querySelector(`[data-check="${key}"]`);
    if (el) {
      el.classList.toggle('check-pass', !!verdict.checks[key]);
      el.classList.toggle('check-fail', !verdict.checks[key]);
    }
  }
  const seal = root.querySelector('#verdict-seal');
  if (seal) seal.classList.toggle('verified', verdict.valid);
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/js`
Expected: PASS (2 tests). If imports fail because the vendored wallet uses Web Crypto APIs unavailable in older Node, require Node ≥ 20 (it is in this repo's toolchain, matching gumptionchain's wallet tests).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: verify-glue.mjs — runVerify + hub provenance adapter + DOM render

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: 2B2F design-system port + base layout, landing, about

**Files:**
- Create: `src/gumption_hub/static/css/hub.css`
- Create: `src/gumption_hub/templates/base.html`, `landing.html`, `about.html`, `404.html`
- Modify: `src/gumption_hub/hub.py`
- Create: `tests/test_pages.py`

**Port source:** `gumptionchain/../acquire-llm/src/acquire_llm/static/css/main.css` (gold tokens at lines 464–480; `.btn-gold`/`.btn-outline-gold` 492–526; `.modal--2b2f` 528–557; `Righteous` headings 89–99) and `acquire-llm/src/acquire_llm/templates/base.html` (Bootstrap 5.3.3 CDN, Inter+Righteous Google Fonts, `data-bs-theme`, no-flash script).

- [ ] **Step 1: Write the failing test**

`tests/test_pages.py`:
```python
def test_landing_renders_brand(test_client):
    resp = test_client.get('/')
    assert resp.status_code == 200
    assert b'GumptionChain' in resp.data or b'Gumption' in resp.data


def test_about_renders(test_client):
    assert test_client.get('/about').status_code == 200


def test_unknown_path_styled_404(test_client):
    resp = test_client.get('/no-such-page')
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_pages.py -v`
Expected: FAIL — `/` returns the Task 2 stub string (no brand markup) and `/about` 404s.

- [ ] **Step 3: Write `src/gumption_hub/static/css/hub.css`** (ported 2B2F base)

```css
/* gumption-hub — 2B2F design system (ported from acquire-llm/main.css) */
:root {
  --gold: #d4a520; --gold-dark: #b8960c; --gold-light: #e8c547; --gold-deep: #6b5608;
  --paper: #fbf8f0; --ink: #2a2620;
  --oppose: #9c4b3b; --support: #3f8f4f;
}
body { min-height: 100vh; font-family: 'Inter', system-ui, sans-serif; }
h1, h2, h3, h4, h5, h6 { font-family: 'Righteous', sans-serif; }
a { color: var(--gold-dark); }
a:hover { color: var(--gold); }
.navbar-brand {
  font-family: 'Righteous', sans-serif; text-transform: uppercase;
  letter-spacing: 0.08em; color: var(--gold) !important;
}
.btn-gold {
  font-weight: 600; color: #fff; border: 1px solid var(--gold-dark);
  background: linear-gradient(135deg, var(--gold), var(--gold-dark));
}
.btn-gold:hover { background: linear-gradient(135deg, var(--gold-light), var(--gold)); color: #fff; }
.btn-outline-gold { color: var(--gold-dark); border: 1px solid var(--gold); background: transparent; font-weight: 600; }
[data-bs-theme="dark"] body { background-color: #1a1d21; }
[data-bs-theme="dark"] .btn-gold { color: #000 !important; }

/* Verify checks */
.check-pass { color: var(--support); }
.check-fail { color: var(--oppose); }
.kind-opposition { color: var(--oppose); font-family: 'Righteous', sans-serif; text-transform: uppercase; }
.kind-support { color: var(--support); font-family: 'Righteous', sans-serif; text-transform: uppercase; }

/* Ledger card (composition B) */
.gc-card {
  border: 1px solid var(--gold-dark); border-radius: 10px; overflow: hidden;
  background: radial-gradient(120% 90% at 50% 0%, rgba(212,165,32,.10), rgba(212,165,32,.02) 60%, transparent), var(--paper);
}
.gc-card-head {
  display: flex; align-items: center; gap: .8rem; padding: 1rem 1.5rem;
  background: linear-gradient(135deg, rgba(212,165,32,.14), rgba(212,165,32,.03));
  border-bottom: 1px solid rgba(184,150,12,.4);
}
.gc-card-head h2 { color: var(--gold-deep); text-transform: uppercase; letter-spacing: .04em; margin: 0; font-weight: 400; }
.gc-row { display: flex; gap: 1rem; padding: .85rem 1.5rem; border-bottom: 1px solid rgba(184,150,12,.16); }
.gc-row:last-child { border-bottom: 0; }
.gc-row .k { font-family: 'Righteous', sans-serif; text-transform: uppercase; letter-spacing: .08em; color: var(--gold-deep); width: 9rem; flex: none; font-size: .78rem; }
.gc-row .v { font-weight: 600; }
.gc-row .v .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 400; }
.gc-row .v .tn { font-variant-numeric: tabular-nums; }
.seal-dot {
  width: 2.4rem; height: 2.4rem; border-radius: 50%; flex: none; color: #fff;
  display: flex; align-items: center; justify-content: center; font-size: 1.3rem;
  background: radial-gradient(circle at 38% 32%, var(--gold-light), var(--gold) 50%, var(--gold-dark));
  box-shadow: inset 0 0 0 2px rgba(255,255,255,.3);
}
```

> This is the load-bearing subset. Copy any further components you need (segmented buttons, `modal--2b2f`) verbatim from the acquire-llm source cited above — do not re-derive them.

- [ ] **Step 4: Write `src/gumption_hub/templates/base.html`**

```html
<!DOCTYPE html>
<html lang="en" data-bs-theme="{{ theme | default('light') }}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}Gumption — the Extended Gumption Universe{% endblock %}</title>
  {% block og %}{% endblock %}
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=Righteous&display=swap">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/hub.css') }}">
</head>
<body>
  <nav class="navbar navbar-expand-md mx-3">
    <a class="navbar-brand" href="{{ url_for('hub.landing') }}">Gumption</a>
    <div class="navbar-nav">
      <a class="nav-link" href="{{ url_for('hub.about') }}">About</a>
      <a class="nav-link" href="{{ url_for('hub.verify_page') }}">Verify</a>
    </div>
  </nav>
  <main class="container py-4">{% block content %}{% endblock %}</main>
  <footer class="text-center py-3" style="color:#6c6356">
    <a href="/" style="color:var(--gold-dark);text-decoration:none">gumption.com</a>
    · the Extended Gumption Universe
  </footer>
  {% block scripts %}{% endblock %}
</body>
</html>
```

- [ ] **Step 5: Write `landing.html`, `about.html`, `404.html`**

`landing.html`:
```html
{% extends 'base.html' %}
{% block content %}
<div class="text-center py-5">
  <h1 class="display-4">The Extended Gumption Universe</h1>
  <p class="lead">Stake your gumption. Prove it anywhere.</p>
  <p>GumptionChain is the nerve of a family of games and tools.</p>
  <a class="btn btn-gold" href="{{ url_for('hub.verify_page') }}">Verify a stake</a>
  <a class="btn btn-outline-gold" href="{{ url_for('hub.about') }}">What is the EGU?</a>
</div>
{% endblock %}
```

`about.html`:
```html
{% extends 'base.html' %}
{% block content %}
<div class="content-narrow" style="max-width:46rem;margin:0 auto">
  <h1>About the EGU</h1>
  <p>The Extended Gumption Universe is a family of projects tied together by
  <strong>GumptionChain</strong> — a proof-of-work chain where tokens are staked
  as opposition or support for subjects.</p>
  <h2>Members</h2>
  <ul>
    <li><a href="https://www.toobigtofail.net">Too Big To Fail (2b2f)</a></li>
    <li>More games coming.</li>
  </ul>
</div>
{% endblock %}
```

`404.html`:
```html
{% extends 'base.html' %}
{% block content %}
<div class="text-center py-5">
  <h1>Not found</h1>
  <p>That page or proof doesn't exist.</p>
  <a class="btn btn-outline-gold" href="{{ url_for('hub.landing') }}">Home</a>
</div>
{% endblock %}
```

- [ ] **Step 6: Update `src/gumption_hub/hub.py`** — real landing/about + 404 handler

Replace the stub `landing` and add:
```python
from flask import render_template


@hub_bp.route('/')
def landing() -> str:
    return render_template('landing.html')


@hub_bp.route('/about')
def about() -> str:
    return render_template('about.html')


@hub_bp.app_errorhandler(404)
def not_found(_e):  # noqa: ANN001, ANN202
    return render_template('404.html'), 404
```

> `verify_page` is referenced by the nav/landing templates and is added in Task 9. To keep Task 8 runnable in isolation, add a temporary stub now and flesh it out in Task 9:
```python
@hub_bp.route('/verify')
def verify_page() -> str:
    return render_template('verify.html')
```
And create a minimal `verify.html` (`{% extends 'base.html' %}{% block content %}<h1>Verify</h1>{% endblock %}`), replaced fully in Task 9.

- [ ] **Step 7: Run to verify it passes**

Run: `uv run pytest tests/test_pages.py -v`
Expected: PASS (3 passed).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: 2B2F design-system port + base layout, landing, about, 404

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: /verify paste-and-check page

**Files:**
- Modify: `src/gumption_hub/templates/verify.html` (replace stub)
- Modify: `src/gumption_hub/hub.py` (verify_page already exists from Task 8)
- Create: `tests/test_verify_page.py`

- [ ] **Step 1: Write the failing test**

`tests/test_verify_page.py`:
```python
def test_verify_page_has_input_and_glue(test_client):
    resp = test_client.get('/verify')
    assert resp.status_code == 200
    assert b'id="proof-input"' in resp.data
    assert b'verify-glue.mjs' in resp.data
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_verify_page.py -v`
Expected: FAIL — the stub verify.html lacks the input/glue.

- [ ] **Step 3: Write `src/gumption_hub/templates/verify.html`**

```html
{% extends 'base.html' %}
{% block content %}
<div class="content-narrow" style="max-width:46rem;margin:0 auto">
  <h1>Verify a stake</h1>
  <p>Paste a <code>gc-msg-v1</code> stake attestation to check it against the chain.</p>
  <textarea id="proof-input" class="form-control" rows="8"
            placeholder='{"scheme":"gc-msg-v1", ...}'></textarea>
  <button id="verify-btn" class="btn btn-gold mt-2">Verify</button>
  <div id="verdict" class="mt-3" hidden>
    <div id="verdict-seal" class="seal-dot">&#10003;</div>
    <ul class="list-unstyled mt-2">
      <li data-check="signature">Signature</li>
      <li data-check="onchain">On-chain</li>
      <li data-check="consistent">Consistent</li>
    </ul>
    <pre id="verdict-reasons"></pre>
  </div>
</div>
{% endblock %}
{% block scripts %}
<script type="module">
  import { runVerify, renderVerdict } from "{{ url_for('static', filename='js/verify-glue.mjs') }}";
  document.getElementById('verify-btn').addEventListener('click', async () => {
    const verdict = document.getElementById('verdict');
    try {
      const proof = JSON.parse(document.getElementById('proof-input').value);
      const result = await runVerify(proof);
      renderVerdict(result, document);
      document.getElementById('verdict-reasons').textContent =
        result.reasons.join(', ') || 'all checks passed';
      verdict.hidden = false;
    } catch (e) {
      document.getElementById('verdict-reasons').textContent = 'Invalid proof: ' + e.message;
      verdict.hidden = false;
    }
  });
</script>
{% endblock %}
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_verify_page.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: /verify paste-and-check page wired to verify-glue

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: /proof/<hash> page (card + live verify)

**Files:**
- Create: `src/gumption_hub/templates/proof.html`
- Modify: `src/gumption_hub/hub.py`
- Create: `tests/test_proof_page.py`

- [ ] **Step 1: Write the failing test**

`tests/test_proof_page.py`:
```python
import json

from gumptionchain.attestation import sign_stake_attestation
from gumptionchain.wallet import Wallet

from gumption_hub import proofs

CLAIM = {'txid': 'a' * 64, 'kind': 'opposition', 'subject': 'goblins', 'amount': 300}


def _store(app):
    with app.app_context():
        return proofs.store_proof(sign_stake_attestation(Wallet(), CLAIM)).content_hash


def test_proof_page_renders_card_and_og(app, test_client):
    h = _store(app)
    resp = test_client.get(f'/proof/{h}')
    assert resp.status_code == 200
    assert b'Verified on GumptionChain' in resp.data
    assert b'og:image' in resp.data
    assert f'/proof/{h}/og.png'.encode() in resp.data
    assert b'verify-glue.mjs' in resp.data


def test_unknown_proof_404s(test_client):
    assert test_client.get('/proof/' + 'c' * 64).status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_proof_page.py -v`
Expected: FAIL — `/proof/<hash>` route not found.

- [ ] **Step 3: Add the route to `src/gumption_hub/hub.py`**

```python
import json

from gumptionchain.attestation import parse_stake_attestation
from flask import abort


def _grit(grains: int) -> str:
    g = grains / 100
    return f'{g:.0f}' if g == int(g) else f'{g:.2f}'


@hub_bp.route('/proof/<mill_hash:proof_hash>')
def proof_page(proof_hash: str):
    row = proofs.get_stored_proof(proof_hash)
    if row is None:
        abort(404)
    proof = json.loads(row.proof_json)
    claim = parse_stake_attestation(proof)
    prov = provenance.lookup_provenance(claim['txid'])  # for server render
    return render_template(
        'proof.html',
        proof_hash=proof_hash,
        proof_json=row.proof_json,
        claim=claim,
        grit=_grit(claim['amount']),
        provenance=prov,
    )
```

- [ ] **Step 4: Write `src/gumption_hub/templates/proof.html`**

```html
{% extends 'base.html' %}
{% block og %}
<meta property="og:title" content="Verified on GumptionChain">
<meta property="og:description" content="{{ claim.kind | capitalize }} · {{ grit }} GRIT on &ldquo;{{ claim.subject or claim.address }}&rdquo;">
<meta property="og:type" content="website">
<meta property="og:image" content="{{ request.url_root }}proof/{{ proof_hash }}/og.png">
<meta name="twitter:card" content="summary_large_image">
{% endblock %}
{% block content %}
<div class="content-narrow" style="max-width:46rem;margin:0 auto">
  <p class="lead text-center">&ldquo;{{ grit }} GRIT in
    <span class="kind-{{ claim.kind }}">{{ claim.kind }}</span>
    of &ldquo;{{ claim.subject or claim.address }}.&rdquo;&rdquo;</p>

  <div class="gc-card my-3">
    <div class="gc-card-head">
      <div id="verdict-seal" class="seal-dot">&#10003;</div>
      <h2>Verified on GumptionChain</h2>
    </div>
    <div class="gc-row"><div class="k">Claim</div>
      <div class="v"><span class="kind-{{ claim.kind }}">{{ claim.kind }}</span>
        · <span class="tn">{{ grit }} GRIT</span> on &ldquo;{{ claim.subject or claim.address }}&rdquo;</div></div>
    <div class="gc-row"><div class="k">Signer</div>
      <div class="v"><span class="mono">{{ claim.handle or '' }} {{ claim.txid[:8] }}…</span></div></div>
    <div class="gc-row"><div class="k">Transaction</div>
      <div class="v"><span class="mono">{{ claim.txid[:10] }}…</span>
        {% if provenance and provenance.height %} · <span class="tn">block {{ provenance.height }}</span>{% endif %}</div></div>
  </div>

  <div class="border rounded p-3">
    <strong>Verified live in your browser</strong>
    <ul class="list-unstyled mt-2">
      <li data-check="signature">Signature</li>
      <li data-check="onchain">On-chain</li>
      <li data-check="consistent">Consistent</li>
    </ul>
    <span id="confirmations"></span>
  </div>

  <details class="mt-3"><summary>Raw signed proof</summary>
    <pre>{{ proof_json }}</pre></details>
</div>
{% endblock %}
{% block scripts %}
<script type="module">
  import { runVerify, renderVerdict } from "{{ url_for('static', filename='js/verify-glue.mjs') }}";
  const proof = {{ proof_json | safe }};
  (async () => {
    const verdict = await runVerify(proof);
    renderVerdict(verdict, document);
    if (verdict.provenance && verdict.provenance.confirmations != null) {
      document.getElementById('confirmations').textContent =
        verdict.provenance.confirmations + ' confirmations';
    }
  })();
</script>
{% endblock %}
```

> `{{ proof_json | safe }}` injects the stored proof JSON as a JS object literal. It is content-addressed and JSON, not user-free-text, but if you want belt-and-suspenders use `{{ proof_json | tojson | safe }}` over the Python dict instead.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_proof_page.py -v`
Expected: PASS (2 passed). The OG image URL is rendered as a plain absolute path (`{{ request.url_root }}proof/<hash>/og.png`), so this task is self-contained — the actual image route is implemented next in Task 11.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: /proof/<hash> page — ledger card + OG meta + live verify

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: OG image — SVG template → PNG via cairosvg

**Files:**
- Create: `src/gumption_hub/og.py`
- Create: `src/gumption_hub/templates/og/proof.svg.jinja`
- Modify: `src/gumption_hub/hub.py`
- Create: `tests/test_og.py`

**Technique source:** `acquire-llm/src/acquire_llm/og.py` (`_svg_to_png`, `render_recap_png` — Jinja SVG → `cairosvg.svg2png` → atomic cache-to-disk).

- [ ] **Step 1: Write the failing test**

`tests/test_og.py`:
```python
import struct

from gumptionchain.attestation import sign_stake_attestation
from gumptionchain.wallet import Wallet

from gumption_hub import proofs

CLAIM = {'txid': 'a' * 64, 'kind': 'opposition', 'subject': 'goblins', 'amount': 300}


def _png_size(data: bytes) -> tuple[int, int]:
    # PNG IHDR width/height are big-endian uint32 at byte offsets 16 and 20.
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    width, height = struct.unpack('>II', data[16:24])
    return width, height


def test_proof_og_png_is_1200x630(app, test_client):
    with app.app_context():
        h = proofs.store_proof(sign_stake_attestation(Wallet(), CLAIM)).content_hash
    resp = test_client.get(f'/proof/{h}/og.png')
    assert resp.status_code == 200
    assert resp.mimetype == 'image/png'
    assert _png_size(resp.data) == (1200, 630)


def test_unknown_proof_og_404s(test_client):
    assert test_client.get('/proof/' + 'd' * 64 + '/og.png').status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_og.py -v`
Expected: FAIL — `/proof/<hash>/og.png` route not found.

- [ ] **Step 3: Write `src/gumption_hub/templates/og/proof.svg.jinja`** (card B as SVG, 1200×630)

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#fbf8f0"/>
  <rect x="24" y="24" width="1152" height="582" rx="18" fill="none" stroke="#b8960c" stroke-width="4"/>
  <rect x="0" y="40" width="1200" height="120" fill="#f3e6c4"/>
  <circle cx="120" cy="100" r="40" fill="#d4a520" stroke="#b8960c" stroke-width="3"/>
  <text x="120" y="116" font-family="Righteous" font-size="44" fill="#fff" text-anchor="middle">&#10003;</text>
  <text x="190" y="115" font-family="Righteous" font-size="48" fill="#6b5608">VERIFIED ON GUMPTIONCHAIN</text>

  <text x="80" y="260" font-family="Righteous" font-size="30" fill="#6b5608">CLAIM</text>
  <text x="80" y="310" font-family="Inter" font-weight="600" font-size="46" fill="#2a2620">{{ kind_upper }} · {{ grit }} GRIT on &#8220;{{ subject }}&#8221;</text>

  <text x="80" y="400" font-family="Righteous" font-size="30" fill="#6b5608">SIGNER</text>
  <text x="80" y="448" font-family="Inter" font-weight="600" font-size="40" fill="#2a2620">{{ signer }}</text>

  <text x="80" y="530" font-family="Righteous" font-size="30" fill="#6b5608">TRANSACTION</text>
  <text x="80" y="576" font-family="Inter" font-size="36" fill="#2a2620">{{ txid_short }}{% if height %} · block {{ height }}{% endif %}</text>
</svg>
```

- [ ] **Step 4: Write `src/gumption_hub/og.py`**

```python
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cairosvg
from flask import current_app, render_template

OG_WIDTH = 1200
OG_HEIGHT = 630
PROOF_TEMPLATE_VERSION = 'v1'


def _cache_dir() -> Path:
    configured = current_app.config.get('OG_CACHE_DIR')
    base = Path(configured) if configured else Path(current_app.instance_path) / 'og'
    base.mkdir(parents=True, exist_ok=True)
    return base


def render_proof_png(proof_hash: str, context: dict[str, Any]) -> Path:
    """Render (once) the OG PNG for a proof. Content-addressed → immutable, so a
    cached file is never regenerated. Atomic temp-write-then-rename.
    """
    out = _cache_dir() / f'{proof_hash}.{PROOF_TEMPLATE_VERSION}.png'
    if out.exists():
        return out
    svg = render_template('og/proof.svg.jinja', **context).encode('utf-8')
    png = cairosvg.svg2png(
        bytestring=svg, output_width=OG_WIDTH, output_height=OG_HEIGHT
    )
    tmp = out.with_suffix(out.suffix + f'.{time.time_ns()}.tmp')
    tmp.write_bytes(png)
    tmp.replace(out)
    return out
```

- [ ] **Step 5: Add the route to `src/gumption_hub/hub.py`**

```python
from flask import send_file

from gumption_hub import og


@hub_bp.route('/proof/<mill_hash:proof_hash>/og.png')
def proof_og(proof_hash: str):
    row = proofs.get_stored_proof(proof_hash)
    if row is None:
        abort(404)
    proof = json.loads(row.proof_json)
    claim = parse_stake_attestation(proof)
    prov = provenance.lookup_provenance(claim['txid'])
    context = {
        'kind_upper': claim['kind'].upper(),
        'grit': _grit(claim['amount']),
        'subject': claim.get('subject') or claim.get('address'),
        'signer': claim.get('handle') or (claim['txid'][:8] + '…'),
        'txid_short': claim['txid'][:10] + '…',
        'height': prov.get('height') if prov else None,
    }
    path = og.render_proof_png(proof_hash, context)
    resp = send_file(path, mimetype='image/png')
    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp
```

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/test_og.py -v`
Expected: PASS (2 passed). If cairosvg raises about missing system libs, install cairo locally (`sudo apt-get install libcairo2`); the deploy image handles this in Task 13.

- [ ] **Step 7: Run the full suite + lint + types**

Run:
```bash
uv run pytest && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && node --test tests/js
```
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: /proof/<hash>/og.png — SVG card -> PNG via cairosvg, immutable cache

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 12: Demo hook + MANUAL-VERIFICATION

**Files:**
- Modify: `src/gumption_hub/templates/verify.html` (add a "create & share" demo affordance)
- Create: `MANUAL-VERIFICATION.md`

- [ ] **Step 1: Add a create+submit affordance to `verify.html`**

Below the verify UI in `verify.html`, add a `<details>` demo block that signs a claim with a generated wallet and POSTs it:
```html
<details class="mt-4"><summary>Create a demo proof &amp; share it</summary>
  <input id="demo-subject" class="form-control mt-2" placeholder="subject (e.g. goblins)">
  <input id="demo-amount" class="form-control mt-2" type="number" placeholder="grains (e.g. 300)">
  <input id="demo-txid" class="form-control mt-2" placeholder="txid (64 hex)">
  <button id="demo-btn" class="btn btn-outline-gold mt-2">Sign &amp; submit</button>
  <div id="demo-link" class="mt-2"></div>
</details>
<script type="module">
  import { Wallet } from "{{ url_for('static', filename='wallet/gc-wallet.mjs') }}";
  import { signStakeAttestation } from "{{ url_for('static', filename='wallet/gc-attestation.mjs') }}";
  document.getElementById('demo-btn').addEventListener('click', async () => {
    const w = await Wallet.generate();
    const claim = {
      txid: document.getElementById('demo-txid').value,
      kind: 'opposition',
      subject: document.getElementById('demo-subject').value,
      amount: Number(document.getElementById('demo-amount').value),
    };
    const proof = await signStakeAttestation(w, claim);
    const resp = await fetch('/proof', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(proof),
    });
    const body = await resp.json();
    document.getElementById('demo-link').textContent = body.url || JSON.stringify(body);
  });
</script>
```

- [ ] **Step 2: Write `MANUAL-VERIFICATION.md`**

```markdown
# Manual verification — gumption-hub

1. `uv run gumption-hub run` (separate terminal) with a configured `.env`.
2. Open `/verify`. Expand "Create a demo proof & share it", enter a subject,
   grains, and a txid that exists on your node's canonical chain, then
   "Sign & submit". You get a `/proof/<hash>` link.
3. Open the `/proof/<hash>` link: the ledger card renders, and the three live
   checks resolve (Signature / On-chain / Consistent) against the hub's own
   node via `/tx/<txid>/provenance.json`.
4. View `/proof/<hash>/og.png` directly — a 1200×630 verified card.
5. Paste the same proof JSON into `/verify` — same verdict, no stored link.
```

- [ ] **Step 3: Run the full suite (no behavior regressions)**

Run: `uv run pytest && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: verify-page demo affordance (sign+submit) + MANUAL-VERIFICATION

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 13: Deploy config + CI + prod DB init

**Files:**
- Create: `Dockerfile`, `app.py` (gunicorn entry), `.github/workflows/ci.yml`
- Modify: `src/gumption_hub/__init__.py` (add `init` command)
- Modify: `pyproject.toml` (switch dependency to pinned git for CI)

- [ ] **Step 1: Add a prod DB init command to `src/gumption_hub/__init__.py`**

The chain tables come from gumptionchain's migrations; the hub's single `stored_proof` table is created idempotently with `db.create_all()` (only creates missing tables):
```python
import click as _click
from gumptionchain.database import db as _db


@cli.command('init-hub-db')
def init_hub_db() -> None:
    """Create the hub's own tables (stored_proof). Run after the node's
    `gumptionchain db upgrade` has created the chain schema.
    """
    import gumption_hub.models  # noqa: F401 — register StoredProof on metadata
    _db.create_all()
    _click.echo('hub tables ready')
```

- [ ] **Step 2: Write `app.py` (gunicorn entry)**

```python
from gumption_hub.app import create_hub_app

app = create_hub_app()
```

- [ ] **Step 3: Write `Dockerfile`** (cairo + fonts for cairosvg/fontconfig)

```dockerfile
FROM python:3.12-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 libpango-1.0-0 libpangocairo-1.0-0 fontconfig \
    && rm -rf /var/lib/apt/lists/*
COPY src/gumption_hub/static/fonts/*.ttf /usr/local/share/fonts/
RUN fc-cache -f
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY . .
RUN uv sync --no-dev --frozen
ENV PORT=8080
CMD ["uv", "run", "gunicorn", "--bind", ":8080", "app:app"]
```

> Bundle the Righteous + Inter `.ttf` files into `src/gumption_hub/static/fonts/` (download from Google Fonts) so fontconfig resolves them for the OG renderer; the SVG template references families `Righteous` and `Inter`.

- [ ] **Step 4: Switch the gumptionchain dependency to a pinned git rev for CI**

In `pyproject.toml`, replace the `[tool.uv.sources]` path entry with a pinned git source for reproducible CI/deploy (keep the path dep commented for local dev):
```toml
[tool.uv.sources]
# Local dev: gumptionchain = { path = "../gumptionchain", editable = true }
gumptionchain = { git = "https://github.com/gumptionthomas/gumptionchain", rev = "<current-main-sha>" }
```
Fill `<current-main-sha>` with `git -C ../gumptionchain rev-parse main`.

- [ ] **Step 5: Write `.github/workflows/ci.yml`**

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: sudo apt-get update && sudo apt-get install -y libcairo2 fontconfig
      - run: uv sync --frozen
      - run: uv run ruff check src tests
      - run: uv run ruff format --check src tests
      - run: uv run mypy
      - run: uv run pytest
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: node --test tests/js
```

> Pin the third-party actions to commit SHAs (keep the `# vX` comment) before merging, per gumptionchain's supply-chain practice.

- [ ] **Step 6: Verify the build locally**

Run:
```bash
cd /home/gumptionthomas/Development/gumption-hub
docker build -t gumption-hub-test . && uv run gumption-hub init-hub-db --help
```
Expected: image builds; the CLI command is registered.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: Dockerfile, gunicorn entry, CI, init-hub-db command

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Run the complete gate locally:
```bash
cd /home/gumptionthomas/Development/gumption-hub
uv run pytest && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && node --test tests/js
```
Expected: all green.

- [ ] Dispatch the final code reviewer for the whole implementation, then use superpowers:finishing-a-development-branch to open the PR.

---

## Notes carried from spec self-review (do not re-litigate)

- **Provenance is read via the hub's public `/tx/<txid>/provenance.json`**, never the authed `/api/transaction/<txid>`. Genuine transport errors must propagate (not collapse to `txn-not-found`).
- **OG image renders immutable facts only** (no live confirmation count) so it caches immutably; live confirmations are page-only.
- **Content hash** is `sha256` over canonical (sorted-key, compact) proof JSON — deterministic across JS/Python.
- **Never re-implement node/consensus logic** in the hub — import from gumptionchain. `/api/*` is untouched.
- **Zero npm** — JS is vanilla ESM tested with `node --test`.
```
