# Web / Browser-UI Threat-Modeled Audit — Design

**Status:** Draft for review
**Date:** 2026-06-02
**Kind:** Security audit (design phase — defines scope, adversary model, methodology, and deliverable shape; the audit itself is run during the implementation plan that follows this spec)

This is the sixth threat-modeled audit of cancelchain, after the [verification-pipeline](../audits/2026-05-29-verification-pipeline-audit.md) (closed 0/0/0/0), [API-authentication](../audits/2026-05-31-api-authentication-audit.md) (closed 0/0/0/0), [P2P/networking](../audits/2026-06-01-network-p2p-audit.md) (closed 0/0/0/0), [wallet/crypto](../audits/2026-06-02-wallet-crypto-audit.md) (closed 0/0/0/0), and [CLI/operator-surface](../audits/2026-06-02-cli-audit.md) (closed 0/0/0/0) audits. It targets the **browser-facing web UI** — `browser.py`, the Jinja templates in `templates/`, and the HTML-response wiring in `application.py`. This is the **last uncovered surface** and the only **remote, unauthenticated** one.

## Motivation

The five prior audits hardened the node's internal subsystems and its two operator/peer interfaces. The browser UI is the one surface reachable by an **anonymous remote visitor over HTTP** — a public block explorer (`/`, `/chains`, `/block/<hash>`, `/transaction/<txid>`) that renders on-chain data into HTML. That makes it the natural home for the **web vulnerability class** none of the prior audits examined: XSS, response-header hardening, clickjacking, information disclosure, and injection through request-controlled path/query inputs.

The explorer is **read-only and intentionally public** — there is no login, no cookie session, and no state-changing route — which sharpens the model: the adversary is a remote visitor (or a transaction author who plants content that later renders in another visitor's browser), and the question is whether *rendering already-validated on-chain data, in response to an anonymous request, in the HTML layer* introduces a web-layer weakness.

Concrete attack *seeds* visible on inspection (candidates, not pre-judged):

- **No HTTP security headers anywhere** — no `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, or HSTS; no `Talisman`, no `after_request` hook, no header wiring in `application.py` (confirmed by grep). The HTML responses ship with browser defaults — no clickjacking defense, no MIME-sniff protection, no CSP defense-in-depth for XSS.
- **`return e` error pattern** — every `browser.py` view ends `except Exception as e: current_app.logger.exception(e); return e` (`browser.py:29-31,41-43,65-67,98-100`). Returning a raw non-`HTTPException` from a Flask view is a latent bug (Flask can't build a response from it) and a potential information-disclosure path depending on `DEBUG`/`PROPAGATE_EXCEPTIONS`.
- **Attacker-authored fields rendered in `transaction.html`** — the outflow `subject`/`forgive`/`support` (`{{ o.subject }}` / `{{ o.subject | human_subject }}`, lines 101-103) AND the transaction `address`/`public_key`/`signature` (lines 28,31,34): a transaction submitter controls their own keypair/address/signature and their chosen subject. These are the fields that reach the DOM. (Jinja autoescape is on by default for `.html`, `human_subject` returns a plain `str`, and no template uses `| safe`/`Markup`, so HTML-text-context XSS is likely *defended* — the audit confirms, doesn't assume.)
- **JS-attribute interpolation context** — `block.html:65` and `transaction.html:56` both render `onclick="window.location='{{ url_for(...) }}'"`: a Jinja value inside a JavaScript string literal inside an HTML event attribute. This is the most-commonly-missed XSS context (HTML escaping ≠ JS-string escaping). Safe here only if `url_for` emits `Markup`-escaped output AND the `MillHashConverter`/url-safe input can't carry `'`/`"` — the audit must verify both, not assume.
- **Malformed `<script>` attribute in `base.html:39`** — `integrity="…RI="crossorigin="anonymous"` has no space between the `integrity` value and `crossorigin`, which can break SRI/CORS parsing for the cross-origin jQuery asset (SRI silently not enforced).

## Scope & trust boundaries

### In scope

- **The browser blueprint** (`src/cancelchain/browser.py`) in full: `index_view`, `chains_view`, `block_view`, `transaction_view`, the `longest_chain` helper, and the `except … return e` error handling.
- **The Jinja templates** (`src/cancelchain/templates/`): `base.html`, `index.html`, `chains.html`, `block.html`, `transaction.html` — every `{{ … }}` interpolation (escaping context, `| safe`/`Markup` usage, attribute vs text vs URL context), the `utc_datetime` / `human_subject` filters, and the external-asset `<link>`/`<script>` tags (SRI/CORS/CDN trust).
- **HTML-response wiring** (`src/cancelchain/application.py`): the absence/presence of response-hardening headers, the URL converters (`AddressConverter`, `MillHashConverter`, `SubjectConverter`) as input constraints on path params, `register_browser`, and any error-handler / `DEBUG` / `PROPAGATE_EXCEPTIONS` config that governs what a browser sees on error.
- **Request-controlled inputs** to the above: the `block_hash`/`txid` path params (constrained by `MillHashConverter`), the `db.paginate` query args (`page`/`per_page`) on `/chains`, and any other query string the views consume.

### Trusted boundaries (reference, do not re-audit)

- **Block/transaction validity.** The data rendered is already validated on-chain — the **verification audit's** domain (closed). A finding that reduces to "invalid data is on the chain" is cross-referenced there; this audit owns how *valid* data is *rendered*.
- **API authentication & the JSON API.** `/api/*` access control is the **auth audit's** domain (closed). The browser views' *intentional public read-only access* is by design (a block explorer), not a finding.
- **Recursive-CTE query cost.** A known profiling-gated roadmap perf item; a resource finding may cross-reference it but the perf work itself is out of scope.

**Framing consequence (the scope razor):**

- "Invalid data is accepted onto the chain" → verification audit. "An `/api/*` request is under-authorized" → auth audit. "A query is slow" → the perf roadmap. Cross-reference; do not re-claim.
- "The browser views are unauthenticated" is **by design** (public explorer), not a finding.
- This audit owns: *"a remote unauthenticated visitor's request to a browser view causes script execution (XSS), a missing/weak response-hardening header, information disclosure, an injection, or resource amplification in the HTML-rendering layer — independent of the validated data it displays and the auth layer beside it."*

### Explicitly out of scope

- The CLI, networking, crypto, verification, and auth layers (cross-referenced, per above).
- Third-party CDN/library CVEs (Bootstrap/jQuery versions) — the supply-chain CVE workflow's domain; the audit owns *how* they're loaded (SRI/CORS/CSP), not their version currency.
- The JSON API as an access-control or data-validity surface (auth/verification audits).
- TLS/transport configuration (a deployment concern; HSTS *as a response header* is in scope, its termination is not).

## Adversary categories

Six lenses for the remote-visitor surface. A single attack may touch more than one.

1. **XSS / content-injection** — script or markup execution via the only attacker-authored rendered field (`subject`, and its `human_subject` decode) or any other interpolation; escaping-context errors (text vs attribute vs URL vs `<title>`), any `| safe`/`Markup`, and whether autoescape is actually on for every rendered template.
2. **Response-header hardening / clickjacking / MIME-sniffing** — missing `Content-Security-Policy`, `X-Frame-Options` (frameability/clickjacking), `X-Content-Type-Options` (sniffing), `Referrer-Policy`, HSTS; plus the SRI/CORS correctness of the CDN `<script>`/`<link>` tags (the malformed `integrity`/`crossorigin` in `base.html:39`).
3. **Information disclosure / error handling** — `return e`, stack traces, `DEBUG`/`PROPAGATE_EXCEPTIONS`, internal paths/state in error responses, over-broad data in rendered pages, server-version/header leakage.
4. **Injection via request inputs** — SQLi, SSTI, or path traversal through `block_hash`/`txid` path params and pagination query args; whether the URL converters + ORM parameterization fully bound them; any template-name or filter input derived from request data.
5. **Resource / DoS (unauthenticated)** — pagination `page`/`per_page` abuse, the N+1 inflow lookups in `transaction_view`, recursive-CTE cost on `/chains`/`/block`, and response-size amplification, all reachable by an anonymous GET.
6. **Session / CSRF / cookie** — confirm there is no state-changing GET, no cookie/session issued (and if any is, its `Secure`/`HttpOnly`/`SameSite` flags), and that CSRF is genuinely N/A given the no-cookie-auth, read-only model.

## Methodology — multi-agent Workflow fan-out

Executed (during the implementation plan) as a Workflow mirroring the prior five audits. **Running it requires the user's explicit opt-in at execution time; this design phase produces only documents.** Three phases: **Discover** (one analyst per adversary category), **Verify** (≥3 adversarial refuters per candidate against the trusted-boundary controls — Jinja autoescape, the URL converters, ORM parameterization, the read-only/no-cookie model, `DEBUG=False` in production), **Synthesize** (dedupe, severities, strengths).

## Severity rubric

Critical/High/Medium/Low, graded on:

- **Reachability** — anonymous remote GET (highest); requires a planted transaction whose content later renders to a victim (stored-XSS shape, still high if it fires); requires misconfiguration the project doesn't ship.
- **Impact** — script execution in a visitor's browser / session-less context, clickjacking, disclosure of internal state, or unauthenticated resource amplification ⇒ higher; a missing defense-in-depth header with no concrete exploit path on this read-only, cookieless app ⇒ Low/Medium.
- **Whether it reduces to a trusted boundary** — data validity, API auth, or the perf roadmap ⇒ cross-reference, not a finding.

Note: a missing security header on a **read-only, cookieless, login-less** explorer has *lower* impact than on a stateful app (no session to steal, no action to forge), so such findings land Low/Medium (defense-in-depth) rather than High — graded honestly, not inflated.

## Deliverable / output format

- **Audit report:** `docs/superpowers/audits/2026-06-02-web-audit.md`, structured like the prior five — executive summary with the `N/N/N/N` headline, per-adversary traces, findings table (id, adversary, severity, description, status, demonstration test), cross-cutting observations (incl. confirmed strengths — Jinja autoescape, the URL converters, ORM parameterization, the read-only/no-cookie model, SRI present on assets), and Recommendations.
- **Demonstration tests:** a new `tests/test_web_audit.py`, one `@pytest.mark.xfail(strict=True)` per finding (strict-xfail while open, passing regression once remediated). Web tests drive routes via the Flask **test client** (`app.test_client()`) and assert on response headers / body, mirroring any existing browser-view tests; **no test makes a real external network request** (CDN assets are asserted by markup, not fetched).
- **Test fixtures:** reuse `tests/conftest.py` `app` + the test client; `mill_block` to populate renderable chain/block/transaction data (incl. a transaction carrying an adversarial subject for the XSS demonstration).

## Close-out flow

Each finding is remediated individually (brainstorm → spec → plan → execution, internal cross-model review to convergence, one Copilot backstop), flipping its strict-xfail demonstration to a passing regression and driving the audit toward **0/0/0/0**. Tracked under "Audit remediation — web findings" in the roadmap.

## Non-goals

- Remediation itself (this spec covers producing the audit; fixes are separate cycles).
- Re-auditing the verification / auth / networking / crypto / CLI layers (cross-referenced only).
- Dependency-CVE currency of Bootstrap/jQuery (supply-chain workflow).
- A front-end redesign or UX work with no security consequence.

## Acceptance criteria for this design

- Scope, trust boundaries, and the scope razor are unambiguous: every candidate is classifiable as in-scope, cross-reference-only, or out-of-scope — with explicit pre-commitment that "the explorer is public/unauthenticated" is by-design, not a finding, and that read-only/cookieless context caps header-hardening findings at Low/Medium.
- The six adversary categories cover the browser surface (XSS, headers, disclosure, injection, resource, session) with no obvious gap.
- The methodology is the approved three-phase fan-out (run under explicit opt-in during the impl plan).
- The deliverable shape (report + `tests/test_web_audit.py`, test-client-driven, strict-xfail) matches the prior audits' proven format.
- The audit makes no real external network request when its demonstration tests run.
