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

Base also ships `_pagination.html`, a macro template providing
`render_pagination(page, endpoint)` for Bootstrap pagination; the blocks and
subjects lists import it via `{% from "_pagination.html" import render_pagination %}`.

## Override rules

1. **Re-skin everything** — drop your own `base.html` in app `templates/`.
2. **Re-skin one page** — drop a same-named page template (e.g. `index.html`);
   it renders on base's route with base's view data.
3. **Inject into a page without replacing it** — provide the page's optional
   include partial (e.g. `verify/extra.html`). Base pages will use
   `{% include "verify/extra.html" ignore missing %}` (added with the verify
   page); base ships no such file. (Jinja blocks can't be filled by a
   non-descendant, so injection uses optional includes, not empty blocks.)
   The home page exposes `index/extra.html` and the subjects index exposes
   `subjects/extra.html` for the same purpose.
4. **Add new pages** — register your own blueprint.
5. **Link to shared pages** by base endpoint name, e.g.
   `url_for('browser.verify_view')`.

## Assets

Reference base-bundled assets via `url_for('browser.static', filename=...)`
(served from `/static/gumptionchain`), which resolves standalone or embedded.
Wallet ESM is vendored into `static/wallet/` from `clients/wallet/` via
`scripts/sync_wallet.py`.

## CSP considerations

The verify page (and any base page that wires up client JS) uses an inline
`<script type="module">`. Base's default Content-Security-Policy (in
`application.py`) includes `'unsafe-inline'` in `script-src`, so this works out
of the box. Per the CSP spec, `'unsafe-inline'` is **ignored** by the browser
once a `nonce-*` or `hash-*` source is present in the same directive — so a
consumer that hardens its own CSP with a nonce/hash must also cover base's
inline scripts (add a matching nonce/hash, or `'strict-dynamic'`), or those
pages' JS will be silently blocked.
