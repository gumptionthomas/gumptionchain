# EGU #189 — Verify + Public Provenance Migration & UI Seam — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the verify capability and the public (unauthed) provenance read from `gumption-hub` into base `gumptionchain`, and build the base↔extension template-override seam that lets the hub re-skin base pages instead of reimplementing them.

**Architecture:** Base's `browser` blueprint becomes self-contained when embedded (carries its own `template_folder` + `static_folder`). Base ships a `/verify` page and a `GET /transaction/<txid>/provenance.json` read built on existing base primitives (`gumptionchain.attestation`, `gumptionchain.message`, `clients/wallet/gc-attestation.mjs`). A consumer re-skins by dropping its own `base.html` into app-level `templates/` (Flask resolves app templates before blueprint templates). The proof store + OG cards stay in the hub.

**Tech Stack:** Flask + Flask-SQLAlchemy, Jinja2, vanilla ESM (Web Crypto), `uv` tooling, `pytest`, `node --test`.

**Spec:** `docs/superpowers/specs/2026-06-07-egu-189-verify-migration-design.md`

---

## File structure

**Base repo (`gumptionchain`):**
- Modify: `src/gumptionchain/browser.py` — blueprint folders; `verify_view`; provenance JSON route.
- Create: `src/gumptionchain/provenance.py` — `lookup_provenance` (unauthed in-process read).
- Modify: `src/gumptionchain/templates/base.html` — block contract (`page_container`→`content`).
- Modify: `src/gumptionchain/templates/{index,chains,block,transaction}.html` — same block rename.
- Create: `src/gumptionchain/templates/verify.html` — verify-only page with optional include hook.
- Create: `src/gumptionchain/static/js/verify-glue.mjs` — verify glue (base's own).
- Create: `src/gumptionchain/static/js/verify-glue.test.mjs` — node test for the adapter.
- Create: `src/gumptionchain/static/wallet/*.mjs` — vendored wallet runtime (from `clients/wallet/`).
- Create: `scripts/sync_wallet.py` — vendoring helper.
- Create: `docs/ui-extension-seam.md` — the documented seam.
- Modify: `.github/workflows/tests.yml` — extend the `node --test` glob.
- Create: `tests/test_provenance_public.py`, `tests/test_verify_page.py`, `tests/test_wallet_vendored.py`, `tests/test_ui_seam.py`.
- Modify: `tests/test_browser.py` — (unchanged behaviorally; existing tests guard the rename).

**Hub repo (`../gumption-hub`):** see PR 4.

This plan is organized as **4 PRs**. Base PRs (1–3) land first; the hub PR (4) depends on them via the local path dependency.

---

## PR 1 — Seam foundation

Branch: `feat/egu-189-ui-seam-foundation`

Establishes the seam mechanics: blueprint-carried template/static folders, the normalized block contract, the wallet-JS vendoring, and the seam documentation. No new pages yet. Base standalone keeps working.

### Task 1.1: Blueprint carries its own template + static folders

**Files:**
- Modify: `src/gumptionchain/browser.py:20`

- [ ] **Step 1: Change the blueprint construction**

Replace:

```python
blueprint = Blueprint('browser', __name__)
```

with:

```python
blueprint = Blueprint(
    'browser',
    __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/static/gumptionchain',
)
```

- [ ] **Step 2: Run the existing browser tests to confirm nothing broke**

Run: `uv run pytest tests/test_browser.py -q`
Expected: PASS (templates still found; standalone app unaffected).

- [ ] **Step 3: Commit**

```bash
git add src/gumptionchain/browser.py
git commit -m "feat(browser): blueprint carries template_folder + static_folder

Makes base self-contained when embedded in a consumer app (which owns the
Flask app's template/static folders). Namespaced static_url_path avoids
clashing with a consumer's own /static. Refs #189"
```

### Task 1.2: Normalize the block contract (`page_container` → `content`)

**Files:**
- Modify: `src/gumptionchain/templates/base.html`
- Modify: `src/gumptionchain/templates/index.html`
- Modify: `src/gumptionchain/templates/chains.html`
- Modify: `src/gumptionchain/templates/block.html`
- Modify: `src/gumptionchain/templates/transaction.html`

- [ ] **Step 1: Rename the block in `base.html`**

In `src/gumptionchain/templates/base.html`, change the opening tag of the page block:

```jinja
  {% block content -%}
```

(was `{% block page_container -%}`). Leave its body and `{%- endblock %}` unchanged.

- [ ] **Step 2: Rename the block in the four page templates**

In each of `index.html`, `chains.html`, `block.html`, `transaction.html`, change the single opening line:

```jinja
{% block content -%}
```

(was `{% block page_container -%}`). Leave bodies and `{%- endblock %}` unchanged.

- [ ] **Step 3: Run the browser tests to confirm the rename is transparent**

Run: `uv run pytest tests/test_browser.py -q`
Expected: PASS — these tests assert on rendered content (block hashes, "No chain"), so a passing run proves the rename preserved rendering.

- [ ] **Step 4: Commit**

```bash
git add src/gumptionchain/templates/
git commit -m "refactor(templates): rename page block page_container -> content

Defines the base block contract (title/head/nav/content/footer/scripts)
used by the extension skin seam. Pure rename; rendering unchanged. Refs #189"
```

### Task 1.3: Wallet-JS vendoring script

**Files:**
- Create: `scripts/sync_wallet.py`

- [ ] **Step 1: Write the sync script**

```python
"""Copy the runtime wallet ESM modules from this gumptionchain checkout's
clients/wallet into the served static dir. Excludes *.test.mjs and *-cli.mjs
(dev-only).

Usage: uv run python scripts/sync_wallet.py [--source .]
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

DEST = (
    Path(__file__).resolve().parent.parent / 'src/gumptionchain/static/wallet'
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default='.')
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

- [ ] **Step 2: Run it to vendor the modules**

Run: `uv run python scripts/sync_wallet.py`
Expected: prints `vendored gc-attestation.mjs`, `vendored gc-message.mjs`, … and creates `src/gumptionchain/static/wallet/*.mjs`.

- [ ] **Step 3: Confirm the runtime closure is present**

Run: `ls src/gumptionchain/static/wallet/`
Expected: includes at least `gc-attestation.mjs gc-message.mjs gc-errors.mjs gc-crypto.mjs gc-sig.mjs gc-wallet.mjs index.mjs` and **no** `*.test.mjs` / `*-cli.mjs`.

- [ ] **Step 4: Commit (script + vendored files together)**

```bash
git add scripts/sync_wallet.py src/gumptionchain/static/wallet/
git commit -m "build(static): vendor wallet ESM into served static dir

Source of truth stays clients/wallet; vendored copies are committed so the
wheel is reproducible. sync_wallet.py is a dev convenience. Refs #189"
```

### Task 1.4: Test that the wallet vendoring is complete

**Files:**
- Create: `tests/test_wallet_vendored.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

WALLET_DIR = Path('src/gumptionchain/static/wallet')
REQUIRED = [
    'gc-attestation.mjs',
    'gc-message.mjs',
    'gc-errors.mjs',
    'gc-wallet.mjs',
    'gc-crypto.mjs',
    'gc-sig.mjs',
    'index.mjs',
]


def test_runtime_wallet_modules_are_vendored():
    for name in REQUIRED:
        assert (WALLET_DIR / name).is_file(), f'missing vendored {name}'


def test_no_test_or_cli_modules_vendored():
    for p in WALLET_DIR.glob('*.mjs'):
        assert not p.name.endswith('.test.mjs')
        assert not p.name.endswith('-cli.mjs')
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_wallet_vendored.py -q`
Expected: PASS (Task 1.3 already vendored the files). If it fails with a missing module, re-run `uv run python scripts/sync_wallet.py`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_wallet_vendored.py
git commit -m "test(static): assert wallet runtime modules are vendored

A missing vendor fails CI rather than 404ing at runtime. Refs #189"
```

### Task 1.5: Seam test — a consumer's `base.html` re-skins base pages

**Files:**
- Create: `tests/test_ui_seam.py`

- [ ] **Step 1: Write the failing test**

```python
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
        assert b'SKINNED' in resp.data       # consumer skin won over blueprint
        assert b'No chain' in resp.data       # base index content still rendered
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_ui_seam.py -q`
Expected: PASS — proves the app-overrides-blueprint resolution that the whole seam relies on. (If it FAILS with the blueprint's default skin instead of `SKINNED`, the blueprint folders from Task 1.1 are misconfigured.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_ui_seam.py
git commit -m "test(browser): consumer base.html re-skins base pages (seam)

Locks in the Flask app-templates-before-blueprint-templates resolution the
extension skin seam depends on. Refs #189"
```

### Task 1.6: Document the seam

**Files:**
- Create: `docs/ui-extension-seam.md`

- [ ] **Step 1: Write the doc**

```markdown
# Base ↔ extension UI seam

A vanilla `gumptionchain` node ships functional browser pages. An extension
(e.g. gumption-hub) themes and extends them instead of reimplementing them.

## Mechanism

The `browser` blueprint carries its own `template_folder` and `static_folder`,
so its pages and assets are available even when the blueprint is registered
into a *consumer's* Flask app. Flask resolves **app-level templates before
blueprint templates**, so a consumer overrides base templates by filename.

## Block contract (`templates/base.html`)

| Block     | Purpose                         |
|-----------|---------------------------------|
| `title`   | page `<title>`                  |
| `head`    | extra `<head>` (CSS/meta)       |
| `nav`     | navbar contents                 |
| `content` | main page body                  |
| `footer`  | footer contents                 |
| `scripts` | end-of-body JS                  |

A conformant consumer skin should define every block (even if empty); base
page templates confine themselves to `content`/`title`/`scripts`.

## Override rules

1. **Re-skin everything** — drop your own `base.html` in app `templates/`.
2. **Re-skin one page** — drop a same-named page template (e.g. `index.html`);
   it renders on base's route with base's view data.
3. **Inject into a page without replacing it** — provide the page's optional
   include partial (e.g. `verify/extra.html`). Base pages use
   `{% include "verify/extra.html" ignore missing %}`; base ships no such file.
   (Jinja blocks can't be filled by a non-descendant, so injection uses
   optional includes, not empty blocks.)
4. **Add new pages** — register your own blueprint.
5. **Link to shared pages** by base endpoint name, e.g.
   `url_for('browser.verify_view')`.

## Assets

Reference base-bundled assets via `url_for('browser.static', filename=...)`
(served from `/static/gumptionchain`), which resolves standalone or embedded.
Wallet ESM is vendored into `static/wallet/` from `clients/wallet/` via
`scripts/sync_wallet.py`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/ui-extension-seam.md
git commit -m "docs(ui): document the base<->extension template seam

Refs #189"
```

### Task 1.7: Open PR 1

- [ ] **Step 1: Push and open the PR**

```bash
git push -u origin feat/egu-189-ui-seam-foundation
gh pr create --fill --base main
```

- [ ] **Step 2: Merge when green** (per CLAUDE.md `mwg`)

Run: `gh pr checks <N> --watch` then `gh pr merge <N> --squash --delete-branch`.

---

## PR 2 — Public provenance read

Branch: `feat/egu-189-public-provenance`

### Task 2.1: `lookup_provenance` module

**Files:**
- Create: `src/gumptionchain/provenance.py`
- Test: `tests/test_provenance_public.py`

- [ ] **Step 1: Write the failing test**

```python
import httpx

from gumptionchain.block import Block


def test_lookup_provenance_returns_dict_for_canonical_txn(
    app, add_chain_block, subject, wallet
):
    from gumptionchain.provenance import lookup_provenance

    with app.app_context():
        assert lookup_provenance('a' * 64) is None
        c, _ = add_chain_block()
        c.to_db()
        t = c.create_support(wallet, 1, subject)
        t.seal()
        t.sign()
        b = Block()
        b.add_txn(t)
        add_chain_block(chain=c, block=b)
        prov = lookup_provenance(t.txid)
        assert prov is not None
        assert 'status' in prov
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_provenance_public.py::test_lookup_provenance_returns_dict_for_canonical_txn -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'gumptionchain.provenance'`.

- [ ] **Step 3: Write the module**

```python
from __future__ import annotations

from typing import Any, cast

from gumptionchain.api import node_lc_dao
from gumptionchain.models import ChainDAO


def lookup_provenance(txid: str) -> dict[str, Any] | None:
    """Public, in-process provenance lookup — the same code path the authed
    /api/transaction/<txid> view uses, minus authentication. Returns the
    #176a provenance dict, or None if the txn is unknown.
    """
    _, lc, _ = node_lc_dao()
    if lc is not None:
        return cast('dict[str, Any] | None', lc.transaction_provenance(txid))
    return cast('dict[str, Any] | None', ChainDAO.pending_provenance(txid))
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_provenance_public.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gumptionchain/provenance.py tests/test_provenance_public.py
git commit -m "feat(provenance): unauthed in-process provenance lookup

Lifts the hub's lookup_provenance into base; same code path as the authed
/api/transaction view, minus auth. Refs #189"
```

### Task 2.2: Public provenance JSON route

**Files:**
- Modify: `src/gumptionchain/browser.py` (imports + new route)
- Test: `tests/test_provenance_public.py`

- [ ] **Step 1: Write the failing test (append to the test file)**

```python
def test_provenance_json_route(app, add_chain_block, subject, test_client, wallet):
    with app.app_context():
        resp = test_client.get('/transaction/' + 'a' * 64 + '/provenance.json')
        assert resp.status_code == httpx.codes.NOT_FOUND
        assert resp.is_json
        assert resp.get_json()['error'] == 'transaction not found'

        c, _ = add_chain_block()
        c.to_db()
        t = c.create_support(wallet, 1, subject)
        t.seal()
        t.sign()
        b = Block()
        b.add_txn(t)
        add_chain_block(chain=c, block=b)

        resp = test_client.get(f'/transaction/{t.txid}/provenance.json')
        assert resp.status_code == httpx.codes.OK
        assert resp.is_json
        data = resp.get_json()
        assert data['txid'] == t.txid
        assert 'status' in data
        # unauthed: no GC-* headers were sent
        assert 'GC-Signature' not in resp.request.headers
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_provenance_public.py::test_provenance_json_route -q`
Expected: FAIL with 404 from Werkzeug routing (route not registered) — note the body will be HTML, not JSON, so `resp.is_json` is False.

- [ ] **Step 3: Add the imports and route in `browser.py`**

Add to the imports near the top:

```python
from flask import Blueprint, abort, current_app, jsonify, render_template

from gumptionchain.provenance import lookup_provenance
```

(Extend the existing `from flask import ...` line to include `jsonify`; add the `lookup_provenance` import alongside the other `gumptionchain.*` imports.)

Add the route (place it after `transaction_view`):

```python
@blueprint.route('/transaction/<mill_hash:txid>/provenance.json')
def transaction_provenance_view(txid: str) -> Any:
    try:
        prov = lookup_provenance(txid)
    except HTTPException as e:
        return e
    except Exception as e:
        current_app.logger.exception(e)
        abort(500)
    if prov is None:
        return jsonify({'error': 'transaction not found'}), 404
    # Route param (mill_hash-validated) is authoritative for txid; unpack prov
    # first so a stray 'txid' key can't override the request path.
    return jsonify({**prov, 'txid': txid})
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_provenance_public.py -q`
Expected: PASS.

- [ ] **Step 5: Run lint + the full browser suite**

Run: `uv run ruff check src tests && uv run pytest tests/test_browser.py tests/test_provenance_public.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/browser.py tests/test_provenance_public.py
git commit -m "feat(browser): public GET /transaction/<txid>/provenance.json

Unauthed JSON provenance read for client-side verify; same data the HTML
transaction view already exposes. Refs #189"
```

### Task 2.3: Open PR 2

- [ ] **Step 1: Push, open, merge when green**

```bash
git push -u origin feat/egu-189-public-provenance
gh pr create --fill --base main
```
Then `gh pr checks <N> --watch` → `gh pr merge <N> --squash --delete-branch`.

---

## PR 3 — Verify page

Branch: `feat/egu-189-verify-page`

### Task 3.1: `verify-glue.mjs` + node test

**Files:**
- Create: `src/gumptionchain/static/js/verify-glue.mjs`
- Create: `src/gumptionchain/static/js/verify-glue.test.mjs`
- Modify: `.github/workflows/tests.yml:26`

- [ ] **Step 1: Write the failing node test**

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nodeFetchProvenance } from './verify-glue.mjs';

const TX = 'a'.repeat(64);

test('nodeFetchProvenance returns null on 404', async () => {
  globalThis.fetch = async () => ({ status: 404, ok: false });
  assert.equal(await nodeFetchProvenance('')(TX), null);
});

test('nodeFetchProvenance throws on non-ok, non-404', async () => {
  globalThis.fetch = async () => ({ status: 500, ok: false });
  await assert.rejects(
    () => nodeFetchProvenance('')(TX),
    /provenance fetch failed: 500/,
  );
});

test('nodeFetchProvenance encodes the txid path segment', async () => {
  let captured;
  globalThis.fetch = async (url) => {
    captured = url;
    return { status: 200, ok: true, json: async () => ({}) };
  };
  await nodeFetchProvenance('http://x')('a/b?c#d');
  assert.equal(
    captured,
    'http://x/transaction/a%2Fb%3Fc%23d/provenance.json',
  );
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --test src/gumptionchain/static/js/verify-glue.test.mjs`
Expected: FAIL — `Cannot find module .../verify-glue.mjs`.

- [ ] **Step 3: Write `verify-glue.mjs`**

```javascript
// Base verify glue: run verifyStake (from the vendored wallet module) over a
// proof, fetching provenance from the node's public endpoint. Pure logic in
// runVerify (fetchProvenance injectable for tests); renderVerdict lights the
// DOM verdict.
import { verifyStake } from '../wallet/gc-attestation.mjs';

// Adapter: node public provenance endpoint. 404 -> null (unknown txn); other
// failures propagate so they are NOT misreported as 'txn-not-found'.
export function nodeFetchProvenance(origin = '') {
  return async (txid) => {
    // Encode the path segment so a malformed txid (containing /, ?, #, …)
    // can't reshape the request into an unintended path/query.
    const resp = await fetch(
      `${origin}/transaction/${encodeURIComponent(txid)}/provenance.json`,
    );
    if (resp.status === 404) return null;
    if (!resp.ok) {
      throw new Error(`provenance fetch failed: ${resp.status}`);
    }
    return resp.json();
  };
}

export async function runVerify(proof, { fetchProvenance, minConfirmations } = {}) {
  return verifyStake(proof, {
    fetchProvenance: fetchProvenance ?? nodeFetchProvenance(),
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

- [ ] **Step 4: Run it to verify it passes**

Run: `node --test src/gumptionchain/static/js/verify-glue.test.mjs`
Expected: PASS (3 tests). The import of `../wallet/gc-attestation.mjs` resolves against the vendored modules from PR 1.

- [ ] **Step 5: Extend the CI glob to run the new test**

In `.github/workflows/tests.yml`, change line 26 from:

```yaml
      - run: node --test clients/wallet/*.test.mjs
```

to:

```yaml
      - run: node --test clients/wallet/*.test.mjs src/gumptionchain/static/js/*.test.mjs
```

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/static/js/ .github/workflows/tests.yml
git commit -m "feat(static): verify-glue.mjs + node test for the provenance adapter

runVerify wraps verifyStake; nodeFetchProvenance targets base's canonical
/transaction/<txid>/provenance.json (origin injectable). Refs #189"
```

### Task 3.2: `verify.html` + `verify_view`

**Files:**
- Create: `src/gumptionchain/templates/verify.html`
- Modify: `src/gumptionchain/browser.py` (add `verify_view`)
- Test: `tests/test_verify_page.py`

- [ ] **Step 1: Write the failing test**

```python
import httpx


def test_verify_page_renders(app, test_client):
    with app.app_context():
        resp = test_client.get('/verify')
        assert resp.status_code == httpx.codes.OK
        body = str(resp.data)
        assert 'proof-input' in body           # the textarea is present
        assert 'verify-glue.mjs' in body       # glue module is wired in
        assert '/static/gumptionchain/' in body  # served from blueprint static
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_verify_page.py -q`
Expected: FAIL with 404 (route not registered).

- [ ] **Step 3: Add `verify_view` to `browser.py`**

Place after `transaction_provenance_view`:

```python
@blueprint.route('/verify')
def verify_view() -> Any:
    return render_template('verify.html', title='Verify')
```

- [ ] **Step 4: Write `verify.html`**

```jinja
{% extends "base.html" %}

{% block title %}Verify a stake{% endblock %}

{% block content -%}
<div class="container-fluid">
  <div class="row my-3"><div class="col">
    <div class="card bg-light"><div class="card-body">
      <div class="card-title h5">Verify a stake</div>
      <p>Paste a <code>gc-msg-v1</code> stake attestation to check it against the chain.</p>
      <textarea id="proof-input" class="form-control" rows="8"
                placeholder='{"scheme":"gc-msg-v1", ...}'></textarea>
      <button id="verify-btn" class="btn btn-primary mt-2">Verify</button>
      <div id="verdict" class="mt-3" hidden>
        <div id="verdict-seal" class="seal-dot">&#10003;</div>
        <ul class="list-unstyled mt-2">
          <li data-check="signature">Signature</li>
          <li data-check="onchain">On-chain</li>
          <li data-check="consistent">Consistent</li>
        </ul>
        <pre id="verdict-reasons"></pre>
      </div>
    </div></div>
  </div></div>
  {% include "verify/extra.html" ignore missing %}
</div>
{%- endblock %}

{% block scripts -%}
{{ super() }}
<script type="module">
  import { runVerify, renderVerdict } from "{{ url_for('browser.static', filename='js/verify-glue.mjs') }}";
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
      const msg = e instanceof Error ? e.message : String(e);
      document.getElementById('verdict-reasons').textContent = 'Invalid proof: ' + msg;
      verdict.hidden = false;
    }
  });
</script>
{%- endblock %}
```

- [ ] **Step 5: Run it to verify it passes**

Run: `uv run pytest tests/test_verify_page.py -q`
Expected: PASS.

- [ ] **Step 6: Run lint + the seam test (verify must also re-skin)**

Run: `uv run ruff check src tests && uv run pytest tests/test_ui_seam.py tests/test_verify_page.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/gumptionchain/browser.py src/gumptionchain/templates/verify.html tests/test_verify_page.py
git commit -m "feat(browser): GET /verify page (verify-only)

Paste a gc-msg-v1 attestation -> 3-check verdict. Exposes an optional
verify/extra.html include hook for extensions (e.g. the hub's demo-proof
UI). No proof-store coupling. Refs #189"
```

### Task 3.3: Full suite + open PR 3

- [ ] **Step 1: Run the full Python suite + format check**

Run: `uv run ruff format --check src tests && uv run pytest -q`
Expected: PASS.

- [ ] **Step 2: Push, open, merge when green**

```bash
git push -u origin feat/egu-189-verify-page
gh pr create --fill --base main
```
Then `gh pr checks <N> --watch` → `gh pr merge <N> --squash --delete-branch`.

---

## PR 4 — Hub consumes the migrated pages

Repo: **`../gumption-hub`** (separate repo; depends on PRs 1–3 being available via the local path dependency).
Branch: `feat/egu-189-consume-base-verify`

> Run all commands below from the `../gumption-hub` checkout. Verify the path
> dependency resolves to a gumptionchain that already has `/verify` +
> provenance (`uv run python -c "import gumptionchain.provenance"` should
> succeed) before starting.

### Task 4.1: Flip `register_browser` on

**Files:**
- Modify: `src/gumption_hub/app.py`

- [ ] **Step 1: Change the factory**

In `create_hub_app`, change:

```python
    create_app(app=app, config_map=config_map, register_browser=False)
```

to:

```python
    create_app(app=app, config_map=config_map, register_browser=True)
```

Update the docstring line "default chain UI suppressed" → "default chain UI enabled and re-skinned".

- [ ] **Step 2: (expected to fail) run the page tests — landing route now collides**

Run: `uv run pytest tests/test_pages.py -q`
Expected: FAIL or error — base now registers `/` (index_view) and the hub also defines `/` (landing). This collision is resolved in Task 4.2. Proceed.

### Task 4.2: Replace the hub landing route with an `index.html` override

**Files:**
- Modify: `src/gumption_hub/hub.py` (remove `landing` route)
- Create: `src/gumption_hub/templates/index.html` (landing body)
- Delete: `src/gumption_hub/templates/landing.html`
- Modify: `src/gumption_hub/templates/base.html` (relink brand/nav)

- [ ] **Step 1: Remove the `landing` route from `hub.py`**

Delete:

```python
@hub_bp.route('/')
def landing() -> str:
    return render_template('landing.html')
```

- [ ] **Step 2: Create `templates/index.html` (overrides base's index page)**

```jinja
{% extends 'base.html' %}
{% block content %}
<div class="text-center py-5">
  <h1 class="display-4">The Extended Gumption Universe</h1>
  <p class="lead">Stake your gumption. Prove it anywhere.</p>
  <p>GumptionChain is the nerve of a family of games and tools.</p>
  <a class="btn btn-gold" href="{{ url_for('browser.verify_view') }}">Verify a stake</a>
  <a class="btn btn-outline-gold" href="{{ url_for('hub.about') }}">What is the EGU?</a>
</div>
{% endblock %}
```

- [ ] **Step 3: Delete `landing.html`**

Run: `git rm src/gumption_hub/templates/landing.html`

- [ ] **Step 4: Relink the skin in `base.html`**

In `src/gumption_hub/templates/base.html`:
- brand link: `href="{{ url_for('hub.landing') }}"` → `href="{{ url_for('browser.index_view') }}"`
- Verify nav link: `href="{{ url_for('hub.verify_page') }}"` → `href="{{ url_for('browser.verify_view') }}"`

- [ ] **Step 5: Run page tests — landing now served at `/` via the override**

Run: `uv run pytest tests/test_pages.py -q`
Expected: may still reference old endpoints/strings; update assertions next. The `/` route should now return 200 with "Extended Gumption Universe".

### Task 4.3: Delete duplicated verify + provenance, repoint proof_page

**Files:**
- Modify: `src/gumption_hub/hub.py` (remove `verify_page`, `tx_provenance` routes)
- Delete: `src/gumption_hub/templates/verify.html`
- Delete: `src/gumption_hub/static/js/verify-glue.mjs`
- Delete: `src/gumption_hub/provenance.py`
- Modify: `src/gumption_hub/hub.py` (proof_page import)

- [ ] **Step 1: Remove the hub's verify route**

Delete from `hub.py`:

```python
@hub_bp.route('/verify')
def verify_page() -> str:
    return render_template('verify.html')
```

- [ ] **Step 2: Remove the ad-hoc provenance route**

Delete from `hub.py`:

```python
@hub_bp.route('/tx/<mill_hash:txid>/provenance.json')
def tx_provenance(txid: str) -> ResponseReturnValue:
    prov = provenance.lookup_provenance(txid)
    if prov is None:
        return jsonify({'error': 'transaction not found'}), 404
    return jsonify({**prov, 'txid': txid})
```

- [ ] **Step 3: Repoint the server-side provenance use in `proof_page`**

Change the import at the top of `hub.py` from `from gumption_hub import og, proofs, provenance` to `from gumption_hub import og, proofs` and add `from gumptionchain.provenance import lookup_provenance`. In `proof_page` and `proof_og`, change `provenance.lookup_provenance(...)` → `lookup_provenance(...)`.

- [ ] **Step 4: Delete the now-dead files**

```bash
git rm src/gumption_hub/templates/verify.html \
       src/gumption_hub/static/js/verify-glue.mjs \
       src/gumption_hub/provenance.py
```

- [ ] **Step 5: Run the suite to surface remaining references**

Run: `uv run pytest -q`
Expected: failures only in tests that referenced the deleted routes/modules — fixed in Task 4.5.

### Task 4.4: Re-add the hub's demo-proof UI as the verify include hook

**Files:**
- Create: `src/gumption_hub/templates/verify/extra.html`

- [ ] **Step 1: Create the include partial (the demo-proof section removed from the migrated verify page)**

```jinja
<details class="mt-4"><summary>Create a demo proof &amp; share it</summary>
  <input id="demo-subject" class="form-control mt-2" placeholder="subject (e.g. goblins)">
  <input id="demo-amount" class="form-control mt-2" type="number" placeholder="grains (e.g. 300)">
  <input id="demo-txid" class="form-control mt-2" placeholder="txid (64 hex)">
  <button id="demo-btn" class="btn btn-outline-gold mt-2">Sign &amp; submit</button>
  <div id="demo-link" class="mt-2"></div>
</details>
<script type="module">
  import { Wallet } from "{{ url_for('browser.static', filename='wallet/gc-wallet.mjs') }}";
  import { signStakeAttestation } from "{{ url_for('browser.static', filename='wallet/gc-attestation.mjs') }}";
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

Note: the partial is included inside base verify.html's `content` block, so its script is inline (includes can't fill the `scripts` block). Module scripts run anywhere in the body. Assets come from `browser.static` (base-served), so no hub wallet vendoring is required for this page.

- [ ] **Step 2: Verify the include is picked up**

Run: `uv run pytest tests/test_verify_page.py -q` (after Task 4.5 updates it) — or manually confirm `/verify` now contains `demo-btn`.

### Task 4.5: Update hub tests for the flipped routes

**Files:**
- Modify: `tests/test_verify_page.py`, `tests/test_provenance.py`, `tests/test_pages.py`

- [ ] **Step 1: Update `test_pages.py`** — landing now renders at `/` via the index override; drop any assertion on a `hub.landing` endpoint. Assert `/` returns 200 and contains "Extended Gumption Universe", and `/verify` returns 200 (served by base) and contains both `proof-input` (base content) and `demo-btn` (the hub include hook).

```python
def test_landing_served_at_root(client):
    resp = client.get('/')
    assert resp.status_code == 200
    assert b'Extended Gumption Universe' in resp.data


def test_verify_served_by_base_with_hub_extra(client):
    resp = client.get('/verify')
    assert resp.status_code == 200
    assert b'proof-input' in resp.data   # base verify content
    assert b'demo-btn' in resp.data       # hub demo-proof include hook
```

- [ ] **Step 2: Update `test_provenance.py`** — replace any `GET /tx/<txid>/provenance.json` calls with `GET /transaction/<txid>/provenance.json` (base's canonical path). Keep the 404-JSON and 200-dict assertions.

- [ ] **Step 3: Update `test_verify_page.py`** — if it asserted hub-owned verify rendering, point it at base's served page (textarea `proof-input`) plus the hub include (`demo-btn`).

- [ ] **Step 4: Run the full hub suite + lint**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(ui): consume base verify + provenance; re-skin via seam

register_browser=True; landing moves to an index.html override; hub's verify
route/template/glue + ad-hoc /tx provenance route deleted in favor of base's
/verify and /transaction/<txid>/provenance.json. Demo-proof UI re-added as the
verify/extra.html include hook. proof_page uses gumptionchain.provenance. Refs gumptionthomas/gumptionchain#189"
```

### Task 4.6: Manual smoke + open PR 4

- [ ] **Step 1: Manual smoke (user runs the dev server in a separate terminal)**

Per the hub's `MANUAL-VERIFICATION.md`: load `/` (re-skinned landing), `/verify` (base page + hub demo section), submit a demo proof → `/proof/<hash>` permalink still works, and `/transaction/<txid>/provenance.json` returns JSON.

- [ ] **Step 2: Push, open, merge when green**

```bash
git push -u origin feat/egu-189-consume-base-verify
gh pr create --fill --base main
```
Then `gh pr checks <N> --watch` → `gh pr merge <N> --squash --delete-branch`.

---

## Self-review notes (coverage check against the spec)

- **Seam (spec §1):** Task 1.1 (blueprint folders), 1.2 (block contract), 1.5 (override test), 1.6 (docs). ✔
- **What migrates (spec §2):** provenance (2.1/2.2), verify view+template (3.2), verify-glue (3.1), wallet vendor (1.3/1.4). Proof store / OG explicitly untouched. ✔
- **Data flow (spec §3):** provenance route returns `{**prov, 'txid'}` with 404 JSON; verify-glue default path `/transaction/<txid>/provenance.json`; verifyStake unchanged (base). ✔
- **Static/packaging (spec §4):** `browser.static` at `/static/gumptionchain`; vendored modules committed; `uv_build` ships `static/` automatically (no config change); CSP already permits `'self'`+jsdelivr (no change). ✔
- **Hub consumption (spec §5):** register_browser flip (4.1), `/` collision via index override (4.2), delete dupes + repoint proof_page (4.3), demo-proof via include hook (4.4), test updates (4.5). ✔
- **Risks (spec):** `page_container`→`content` rename guarded by existing `test_browser.py`; hub skin block-coverage constraint handled by verify.html confining to `content`/`title`/`scripts` and the seam test's override base.html defining all blocks. ✔
