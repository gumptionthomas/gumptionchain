# Project Rename: CancelChain → GumptionChain — Design

**Status:** Draft for review
**Date:** 2026-06-02
**Kind:** Rebrand / mechanical rename (design phase — defines scope, naming decisions, phasing, and per-phase verification gates; the rename itself is executed across the implementation plan that follows this spec)

This spec defines a full rename of the project from **CancelChain** to **GumptionChain**, motivated by the owner's wish to anchor the project to the `gumption.com` domain (held since 1995). The rename spans the Python package, the CLI, the configuration contract, the node-to-node signing protocol, the currency units, and all in-repo branding. It deliberately preserves the `miller`/`Miller` mining-role convention.

## Motivation

The "CancelChain" name and its dependent vocabulary (the `CC_` config prefix, the `cc-sig-v1` signing scheme, the grumble/curmudgeon units) were all chosen under a "cancel / opposition" rubric. The project is being rebranded to GumptionChain. Because the name is woven through the package namespace, the wire protocol, and the unit vocabulary, a coherent rebrand touches several independent surfaces — each of which is isolated into its own phase/PR so blast radius stays small and any single phase is independently revertible.

There is **no production deployment and no legacy chain to preserve** (per project standing notes), which is what makes the protocol-affecting parts (config prefix, signing scheme) safe to rename outright: there are zero live peers or installs to migrate, so no backward-compat gate is required. All (zero) peers move in lockstep.

## Naming decisions (locked)

| Surface | Before | After |
|---|---|---|
| Project / brand name | CancelChain | GumptionChain |
| Python package | `cancelchain` | `gumptionchain` |
| CLI command | `cancelchain` | `gumptionchain` (primary) **+** `gc` (alias) |
| Env-var prefix | `CC_` | `GC_` |
| Signing scheme id | `cc-sig-v1` | `gc-sig-v1` |
| Signing HTTP headers | `CC-Sig-Version`, `CC-Address`, `CC-Public-Key`, `CC-Timestamp`, `CC-Signature` | `GC-Sig-Version`, `GC-Address`, `GC-Public-Key`, `GC-Timestamp`, `GC-Signature` |
| Major currency unit | grumble | **grit** |
| Minor currency unit (1/100 major) | curmudgeon | **grain** |
| Currency ticker | `CCG` | `GRIT` |
| Conversion constant | `CURMUDGEON_PER_GRUMBLE` | `GRAIN_PER_GRIT` |
| Conversion helper | `grumble_to_curmudgeons()` | `grit_to_grains()` |
| Display helper | `human_curmudgeons()` | `human_grains()` |

### Rationale notes

- **`gumptionchain` + `gc` alias:** the package-matching primary command stays explicit and conventional; the short `gc` alias serves daily use. Both entry points map to the same `gumptionchain:cli` callable.
- **grit / grain:** "grit" is the cleanest one-word gumption synonym; "grain" is its *literal* subdivision (grit is physically composed of grains), so the major→minor relationship is a true English entailment, mirroring satoshi's "smallest indivisible piece" elegance. The chain's base integer unit is the minor unit (today `curmudgeons` → `grains`); the major unit is a display-layer denomination only.
- **`GRIT` ticker (not `GRT`/`GCG`):** `GCG` is ambiguous (gri**t** vs gra**in** both start with G); `GRT` collides with The Graph's token symbol. The 4-letter `GRIT` is self-documenting and unambiguous (4-char tickers are common: DOGE, USDT, GALA).

## Scope

### In scope (in-repo, code + docs)

1. **Package namespace** — `src/cancelchain/` → `src/gumptionchain/` and every `cancelchain` import across `src/` and `tests/`.
2. **Packaging / tooling config** — `pyproject.toml` (`name`, `description`, `[project.scripts]`, project URLs, `[tool.mypy] files`, `[tool.ruff] extend-exclude`, `[tool.coverage]` paths), `app.py`, `Dockerfile`, `uv.lock` (regenerated).
3. **Config contract** — the `CC_` → `GC_` env prefix and all references to `CC_*` variable names.
4. **Signing protocol** — the `cc-sig-v1` scheme id and the five `CC-*` header-name constants, plus the protocol spec doc.
5. **Currency units** — the grit/grain/`GRIT` symbol and string changes in `chain.py` and `command.py`.
6. **Live branding** — `README.rst`, `CLAUDE.md`, and `docs/api-auth-protocol.md` prose/identifiers.

### Out of scope (deliberately not changed)

- **`miller` / `Miller`** (400+ references) — the mining-role convention is retained as-is.
- **Historical design/plan docs** (`docs/superpowers/plans/*`, `docs/superpowers/specs/*`, `docs/superpowers/audits/*`, `ROADMAP.md`) — these are a dated record of work performed *as CancelChain*; rewriting them would falsify history. Only **live** docs (README, CLAUDE.md, api-auth-protocol.md) are rebranded. A residual `cancelchain` grep is therefore *expected* to return historical-doc hits after the rename completes; those are not defects.
- **Alembic revision IDs** — name-independent; the `migrations/` directory simply moves with the package.

### Out of scope (external infrastructure — manual, owner's responsibility)

These live outside the repository and are tracked as a checklist, not implemented here:

- **GitHub repo** `gumptionthomas/cancelchain` → `…/gumptionchain` (GitHub auto-redirects old URLs; follow with `git remote set-url`).
- **Domain** — main GumptionChain site to be hosted at **`gumption.com/chain`**; doc/blog endpoints derived as `gumption.com/chain/docs` and `gumption.com/chain/blog` (exact doc-hosting structure — e.g. Read the Docs vs. self-hosted — to be confirmed during the infra pass).
- **Email** — `contact@` / `tom@cancelchain.org` → `gumption.com` addresses.
- **GCS bucket** `blocks.cancelchain.org` and the chain export filename `cancelchain.jsonl` → `gumptionchain.jsonl`.

**One code↔infra dependency:** the in-repo project URLs (pyproject `[project.urls]`, README links) are set in Phase 1 to their *final* targets under `gumption.com/chain`. The homepage is confirmed (`gumption.com/chain`); the `…/chain/docs` and `…/chain/blog` sub-paths are best-effort assumptions to be re-confirmed when the infra is stood up.

## Phasing

Each phase is one branch + PR, per project convention. **Phase 1 must land first** — every other phase edits files that import the package, so the namespace must move before the rest. Phases 2, 3, and 4 are mutually independent and may land in any order after Phase 1.

### Phase 1 — Package + branding + CLI (the mechanical lift)

- `git mv src/cancelchain src/gumptionchain`.
- Rewrite every `cancelchain` import in `src/` and `tests/` → `gumptionchain`.
- `pyproject.toml`: `name`, `description`; `[project.scripts]` defines **both** `gumptionchain` and `gc` → `gumptionchain:cli`; `[project.urls]` → `gumption.com/chain` targets; `[tool.mypy] files`, `[tool.mypy] exclude`, `[tool.ruff] extend-exclude`, `[tool.coverage]` source/omit paths.
- `app.py`: `from gumptionchain import create_app` (the `app` module name itself is unchanged, so gunicorn `app:app` and the Dockerfile `CMD` are unaffected).
- `Dockerfile`: any `cancelchain` references.
- `README.rst` + `CLAUDE.md` branding — the `CancelChain` proper-noun occurrences, **except** `docs/api-auth-protocol.md`, whose branding is folded into Phase 3 to avoid touching that file twice.
- Regenerate `uv.lock` via `uv lock` (the package name change invalidates the locked project entry).
- Add the bulk import-rewrite commit SHA to `.git-blame-ignore-revs` so the mechanical rename does not pollute `git blame`.

**Verification gate:** full `uv run pytest`; `uv run ruff check src tests` + `uv run ruff format --check src tests`; `uv run mypy`; `uv run gumptionchain db check` (migration-parity CI gate); manual `uv run gumptionchain --help` and `uv run gc --help`.

### Phase 2 — Env prefix `CC_` → `GC_`

- `src/gumptionchain/config.py`: `EnvAppSettings._prefix = 'GC_'`.
- `tests/.test.env`: every `CC_*` key → `GC_*`.
- Any committed env templates (e.g. `.env.example` if present).
- Documentation references to `CC_*` variable names in `CLAUDE.md`, `README.rst`, and live docs.

**Verification gate:** full `uv run pytest` (the suite loads `tests/.test.env` via pytest-dotenv, so a missed key surfaces as config/auth failures); confirm `gumptionchain` boots with `GC_*` env.

### Phase 3 — Signing scheme + headers `cc-sig-v1` → `gc-sig-v1`

- `src/gumptionchain/signing.py`: `SIG_SCHEME = 'gc-sig-v1'` and the five header-name constants `H_VERSION`/`H_ADDRESS`/`H_PUBKEY`/`H_TIMESTAMP`/`H_SIGNATURE` → `GC-*` values. (`SIG_VERSION = '1'` is unchanged — the wire *version* number is independent of the scheme name.)
- Any other module referencing those header strings directly rather than via the `signing` constants (audit `api.py`, `api_client.py` — expected to consume the constants, but verify).
- `docs/api-auth-protocol.md`: scheme id, canonical-string example, header table, worked example, **and** the file's CancelChain branding (folded here).
- Tests asserting the scheme id or header names (`tests/test_signing.py`, `tests/test_network_audit.py`, and any others surfaced by grep).

**Verification gate:** full `uv run pytest` with attention to the signing round-trip, freshness-boundary, and node-binding tests; a residual grep for `cc-sig` / `CC-Sig` / `CC-Address` etc. returns only historical-doc hits.

### Phase 4 — Currency units grit / grain

- `src/gumptionchain/chain.py`: `CURMUDGEON_PER_GRUMBLE` → `GRAIN_PER_GRIT`; `REWARD = 100 * GRAIN_PER_GRIT`.
- `src/gumptionchain/command.py`: `grumble_to_curmudgeons` → `grit_to_grains`; `human_curmudgeons` → `human_grains`; `CCG` → `GRIT` in CLI help text, balance/console output, and docstrings ("amount of CCG" → "amount of GRIT").
- Tests referencing these symbols or the `CCG` string (`tests/test_command.py` and any others surfaced by grep).

**Verification gate:** full `uv run pytest`; manual check that `gumptionchain wallet balance` / subject-balance output renders `GRIT`.

## Cross-phase risk & mitigation

The dominant risk is a **missed import or string literal** — a rename that compiles but leaves a stale reference. Mitigation is a **residual grep sweep as each phase's completion gate**: after each phase, grepping the relevant token (`cancelchain`, `CC_`, `cc-sig`/`CC-`, `CCG`/`grumble`/`curmudgeon`) over tracked files must return **only** historical-doc hits (`docs/superpowers/{plans,specs,audits}`, `ROADMAP.md`). Any hit in `src/`, `tests/`, or live docs is an incomplete rename and blocks the phase.

Secondary risk: the `db check` migration-parity gate could trip if the package move disturbs the migrations path — covered by running it explicitly in Phase 1's gate rather than relying on CI alone.

## Testing strategy

No new behavior is introduced — this is a rename — so the existing suite is the oracle. Every phase's gate is the **full** `uv run pytest` (not a targeted subset), plus the standing CI gates (`ruff check`, `ruff format --check`, `mypy`, `db check`). Manual CLI smoke checks (`--help`, a balance read) confirm the user-facing command and ticker renders. Because tests use `db.create_all()` and a temp SQLite DB, the package move and unit rename are fully exercised without external infra.

## Deliverable shape

Four sequential PRs (Phase 1 first; 2–4 in any order after), each green on the full gate before merge, each followed by the standing Copilot-review backstop per project convention. External-infrastructure items are handed off as a checklist for the owner to execute outside the repo.
