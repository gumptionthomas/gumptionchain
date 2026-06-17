# Subject search: typeahead node method + proxy route (EGU primitive)

**Date:** 2026-06-16
**Status:** design approved
**Issue:** gumptionchain#287
**Consumers:** gumptactoe (first adopter), gumption-hub, future EGU apps.
**Consumer spec:** `gumptactoe/docs/superpowers/specs/2026-06-16-subject-autocomplete-design.md`

## Goal

Give a consumer web app a **subject search** capability so it can offer a
typeahead of subjects **already on the chain** as a user types. First adopter:
gumptactoe. This extends the just-landed GRIT-spend rails
(`node_proxy_blueprint` + `makeOnboarding.signTransaction`) and is built as a
**shared EGU primitive**, not a gumptactoe one-off.

**Product term:** "**Standing**" / "subject" — consistent with the GRIT-spend
rails. The chain-API name for the underlying record stays *stake attestation*.

## Boundary decision — this lives in **base**

Subject search belongs in base `gumptionchain`, not the hub. It is a **stateless
read primitive over chain-intrinsic data** (what subjects exist on the chain,
ranked by stake), which base already computes (`subject_leaderboard`) and
already surfaces in the reference browser (`/subjects`). The consumer
(gumptactoe) embeds the `gumptionchain` package and mounts
`node_proxy_blueprint` directly — it never depends on the hub — so the primitive
must live where every EGU app can reach it: base. The hub gets it for free (it
embeds base) and may layer a themed UI on top. The condition that would flip it
to the hub — *curated/editorial* search (featured ranking, social-identity
enrichment, cross-chain aggregation, persistent caches) — is out of scope.

## Background — what already exists (and stays unchanged)

| Element | Where | Relevant detail |
|---|---|---|
| Subject storage | `OutflowDAO.opposition` / `.support` (`models.py`) | `String(500)`, **base64url-encoded** (no padding); each indexed |
| `encode_subject` / `decode_subject` | `payload.py` | base64url; `decode` is bijective for valid subjects |
| `subject_leaderboard(limit)` | `ChainDAO` (`models.py`) | per-distinct-subject union of unspent opposition + support legs → `sum(opposition)`, `sum(support)`, `total`; ranked by `total` desc; keyed by the **encoded** subject. Consumed only by the browser `/subjects` view today |
| Per-subject balance API | `api.py` | `GET /api/subject/<enc>/opposition` and `/support`, both `authorize_reader`, `block_hash`-keyed `cache`, return `{balance|support, as_of_block}` |
| `node_proxy_blueprint(make_client, ...)` | `node_proxy.py` | browser-facing JSON relay over `ApiClient`; `_grit(grains)` → `{grit, grains}`; CSRF-exempt; `rate_limit` hook; `_ProxyError` mapping |
| `OutflowDAO.__init__` | `models.py:146-167` | the single choke point where `opposition`/`support` are set on a row |

**Core question resolved:** subjects are stored base64url-encoded, and base64
prefixes do not map to plaintext prefixes, so search **cannot** run against the
encoded column. We add a **decoded-plaintext column** to index and match
against (see Layer 1). This was chosen over decode-in-Python (does not scale)
and a dedicated index table (over-built for now).

## Match semantics (shipped)

- **Prefix, case-insensitive.** `subject_lower LIKE :q_lower || '%'` (the query
  lowercased in Python, LIKE metacharacters escaped). Left-anchored, so the
  plain index on `subject_lower` (`ix_outflow_subject_lower`) is used as a real
  seek.
- Subjects are **case-sensitive literals** — `"Tabs > Spaces"` ≠
  `"tabs > spaces"` are distinct subjects. We match loosely (case-insensitive)
  but **return the exact canonical string**, never an altered one.
- **Ranked by total GRIT at stake** (`support + opposition`), descending.
- **Hard cap** via `limit` (default 8).
- Empty / whitespace query → **empty result** (never dump the whole set).
- Substring matching is a deliberate future toggle (swap `:q || '%'` for
  `'%' || :q || '%'`); it would forgo the index seek and is left for a later
  enhancement if broader recall is wanted.

## What we build — four thin layers

### Layer 1 — schema: a decoded-plaintext column

In `OutflowDAO` (`models.py`):

- Add `subject_plain: Mapped[str | None] = mapped_column(String(500))` — the
  **decoded canonical plaintext** of whichever stake column is set.
- Add a sibling `subject_plain.lower()` column, `subject_lower` — the
  matched-against form (the canonical `subject_plain` is what's returned).
- Populate both in `__init__`: when `opposition` or `support` is non-`None`, set
  `subject_plain = decode_subject(opposition or support)` and
  `subject_lower = subject_plain.lower()`; otherwise `None` (address / rescind
  outflows carry no searchable subject). Decode defensively — subjects are
  validated upstream, but a decode failure must not break row construction (fall
  back to `None`).
- Add a **plain** index `db.Index('ix_outflow_subject_lower', 'subject_lower')`.
  (Two columns + a plain index rather than one column + a `lower()` **expression
  index**: SQLite reflects expression indexes imperfectly, so `gumptionchain db
  check` reports a phantom diff; a stored lowercased column sidesteps that and
  gives Unicode-correct folding via Python `.lower()`.)

**Migration (greenfield):** fold the column + index into the **baseline**
migration `63d32cd7621a_initial_schema.py` rather than stacking a new revision —
no production data exists yet, so there is no backfill and no `alembic stamp`
ceremony. Tests build via `db.create_all()` from the model, so they pick the
column up automatically. The `gumptionchain db check` CI gate continues to
enforce model/migration parity.

**Note on existing data:** because we fold into baseline, this design assumes no
deployed chain to backfill. If that assumption ever changes, populating
`subject_plain` for existing rows would require a one-off backfill (decode each
opposition/support outflow) — explicitly out of scope here.

### Layer 2 — DAO: `ChainDAO.search_subjects(query, limit=8)`

A leaderboard-shaped query, filtered by the plaintext prefix:

- Same union shape as `subject_leaderboard`: unspent opposition leg + support
  leg (`_unspent_clause`), grouped by subject, `sum` per kind, `total`.
- Add `WHERE subject_lower LIKE :query_lower || '%'` (LIKE metacharacters in the
  query escaped) on each leg.
- Order by `total` desc (tiebreak by subject), `LIMIT :limit`.
- Returns rows of `(subject_plain canonical, opposition_grains, support_grains,
  total_grains)` — **grains** (internal units).
- Guard: a query that is empty or whitespace-only after `strip()` matches
  nothing — each leg's predicate becomes `false()`, so the statement executes
  but returns no rows (never dumps the whole set).
- `limit` is clamped to a sane ceiling (e.g. `1..50`) to bound result size.

Returns canonical plaintext directly (grouping key is the subject), so no
Python decode of results is needed. Lives beside `subject_leaderboard`; tested
at the DAO layer against the temp SQLite DB.

### Layer 3 — node API endpoint

New read endpoint in `api.py`, mirroring the existing subject-read views:

```
GET /api/subjects/search?q=<query>&limit=<n>      (authorize_reader)
→ 200 {
    "subjects": [
      { "subject": "<exact canonical string>",
        "opposition": <grains:int>,
        "support":    <grains:int> },
      ...
    ],
    "as_of_block": "<block_hash>"
  }
```

- `MethodView` + `authorize_reader`, same as `OppositionBalanceView` /
  `SubjectSupportView`.
- `q` missing/blank → `{"subjects": [], "as_of_block": <hash>}` (200, not an
  error — the consumer degrades to an empty dropdown).
- `limit` parsed as int, defaulted to 8, clamped (out-of-range/garbage → default
  or clamped bound, not a 400).
- Empty chain → `EmptyChainError` via the existing `make_error_response` path.
- Grains stay internal (the proxy converts). Block-hash-keyed `cache` MAY be
  used keyed on `f'{block_hash}.search.{lower(q)}.{limit}'`, consistent with the
  balance endpoints; cache is optional for a first cut.

### Layer 4 — `ApiClient` method + proxy route

**`ApiClient.get_subject_search`** (`api_client.py`), following the existing
read methods:

```python
def get_subject_search(
    self, query: str, limit: int = 8, *, raise_for_status: bool = True,
) -> httpx.Response:
    return self.get(
        '/api/subjects/search',
        params={'q': query, 'limit': limit},
        raise_for_status=raise_for_status,
    )
```

**Proxy route** on `node_proxy_blueprint` (`node_proxy.py`):

```
GET /api/node/subject/search?q=<query>&limit=8
→ 200 {
    "subjects": [
      { "subject": "<exact canonical string>",
        "support":    { "grit": <float>, "grains": <int> },
        "opposition": { "grit": <float>, "grains": <int> } },
      ...
    ]
  }
```

- Reuses `_grit(grains)` for each tally — consistent with the existing
  `/balance` and `/subject/balances` routes; no precision loss; the consumer
  formats whole-GRIT itself. (Chosen over the issue's whole-int sketch for
  consistency; trivially changeable.)
- `limit` parsed/forwarded; `q` forwarded as-is (blank `q` relays through to the
  node's empty-result path).
- The node's `as_of_block` is **dropped** at the proxy — the typeahead doesn't
  need it, and this matches the issue's response shape and the sibling
  `/subject/balances` route (which also omits it). Only `/balance` surfaces it.
- Same treatment as the other proxy routes: node host stays server-side,
  CSRF-exempt, `rate_limit` hook applies, `_ok`/`_call` error mapping (node down
  → 502; node 4xx → 400/404). Read-only relay — holds no key, builds no txn,
  safe to expose to a browser.

## Data flow

```
browser typeahead (debounced)
  → GET /api/node/subject/search?q=tab&limit=8        (proxy, base or consumer app)
    → ApiClient.get_subject_search("tab", 8)          (signs gc-sig-v1, node host server-side)
      → GET /api/subjects/search?q=tab&limit=8         (node, authorize_reader)
        → ChainDAO.search_subjects("tab", 8)           (SQL: subject_lower LIKE 'tab%')
        ← [{subject, opposition_grains, support_grains, total}]
      ← {subjects:[{subject, opposition, support}], as_of_block}   (grains)
    ← grains→GRIT via _grit()
  ← {subjects:[{subject, support:{grit,grains}, opposition:{grit,grains}}]}
```

## Testing

- **DAO** (`tests/test_*.py`, temp SQLite): `search_subjects` — prefix match is
  case-insensitive; returns exact canonical (mixed-case) strings; ranks by total
  stake desc; respects `limit` and the clamp; empty/whitespace query → `[]`;
  unspent filtering (a fully-rescinded subject does not appear); a subject set
  by `support` vs `opposition` both searchable; `subject_plain` is populated by
  the constructor for stake outflows and `None` for address/rescind.
- **Node API** (Flask test client): `GET /api/subjects/search` returns the
  documented shape; requires READER (401/403 without); blank `q` → empty list +
  200; `limit` parsing/clamp; empty chain → error path.
- **Proxy** (`tests/test_node_proxy.py`, `FakeClient`): `/subject/search`
  relays, converts grains→GRIT via `_grit()`, forwards `q`/`limit`, maps node
  errors (502 on transport failure, 404/400 passthrough), honors the
  `rate_limit` hook and `max_body_bytes`.
- **Migration parity:** `gumptionchain db check` stays green (baseline edited,
  not stacked).
- Full `pytest` + `ruff` + `mypy` + `node --test` gates stay green.

## Docs / report-back

On merge, report to the gumptactoe session (in the PR + a summary):

1. **Proxy route:** `GET /api/node/subject/search?q=&limit=8`, method, and the
   exact request/response JSON (tallies are `{grit, grains}` objects via
   `_grit()`, **not** whole-int — note the deviation from the issue sketch).
2. **Match semantics shipped:** prefix, case-insensitive, index-accelerated;
   ranked by total stake desc; default limit 8 (clamped).
3. **Index added:** `OutflowDAO.subject_plain` (decoded canonical) +
   `subject_lower` (indexed `ix_outflow_subject_lower`); folded into baseline;
   no backfill (greenfield).
4. The **merge commit SHA** to pin in gumptactoe's `[tool.uv.sources]`.
5. Any deviations.

## Scope

**In:** the four layers above + their tests; the report-back. One base branch →
PR → review → squash-merge.

**Out (separate efforts / YAGNI):**
- Substring / fuzzy / trigram / FTS matching (prefix is the 80% UX; substring is
  a one-line future toggle, indexed substring is a much bigger lift).
- A backfill path for already-deployed chains (greenfield assumption).
- gumptactoe's typeahead DOM (the consumer; pins the merge SHA).
- The hub's themed search UI (consumes the base primitive later).
- Caching beyond the optional block-hash key reuse.

## Invariants — what does NOT change

- The encoded-subject storage, `encode_subject`/`decode_subject`, and the
  existing per-subject balance endpoints.
- `node_proxy_blueprint`'s existing routes, `_grit()` shape, error mapping, and
  CSRF/rate-limit treatment.
- The permissioned read model (`authorize_reader`); search exposes only
  already-public subject data.
- `subject_leaderboard` and the browser `/subjects` view (search is additive).

## Risks

- **`subject_plain` drift from the encoded column.** Mitigation: populate at the
  single constructor choke point from the encoded value; never hand-set. The
  DAO test asserts population for stake outflows and `None` otherwise.
- **Substring expectation.** The consumer (and hub) get prefix first; the
  report-back states this explicitly so the typeahead is built around prefix.
- **Index unused if we later switch to substring.** Accepted — the index earns
  its keep for prefix now; a substring switch is a separate, deliberate change.
