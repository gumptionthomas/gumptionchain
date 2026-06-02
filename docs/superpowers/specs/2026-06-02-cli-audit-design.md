# CLI & Operator-Surface Threat-Modeled Audit — Design

**Status:** Draft for review
**Date:** 2026-06-02
**Kind:** Security audit (design phase — defines scope, adversary model, methodology, and deliverable shape; the audit itself is run during the implementation plan that follows this spec)

This is the fifth threat-modeled audit of cancelchain, after the [verification-pipeline](../audits/2026-05-29-verification-pipeline-audit.md) (closed 0/0/0/0), [API-authentication](../audits/2026-05-31-api-authentication-audit.md) (closed 0/0/0/0), [P2P/networking](../audits/2026-06-01-network-p2p-audit.md) (closed 0/0/0/0), and [wallet/crypto](../audits/2026-06-02-wallet-crypto-audit.md) (closed 0/0/0/0 after WC1/WC2) audits. It targets the **`cancelchain` CLI and operator surface** — `command.py` (the largest previously-unaudited module, 1034 LOC), the `FlaskGroup`/`AppGroup` command tree in `__init__.py`, and the file/config/credential wiring the operator drives.

## Motivation

Four prior audits hardened the *node's* internal subsystems (validation, auth, networking, crypto). None examined the **operator-facing command layer** — the surface where a human runs `cancelchain import`, `export`, `sync`, `mill`, `wallet create`, and `txn/...`, feeding the node files, paths, host URLs, and wallet material from their own shell.

The CLI is a **thin layer over already-audited subsystems**, which sharpens (not removes) the threat model. The operator is trusted; the adversary is whoever supplies the operator with **untrusted artifacts the CLI ingests**:

- a **block-export `.jsonl`** handed to `cancelchain import` (the one place external *data* enters via the CLI),
- a **wallet `*.pem`** dropped into `WALLET_DIR` or passed via `--wallet-file`,
- a **host/peer URL** in `--host` / `CC_DEFAULT_COMMAND_HOST` / `CC_PEERS` that steers where the CLI connects and which local wallet signs.

Concrete attack *seeds* visible on inspection (candidates, not pre-judged):

- **`import_blocks_command`** (`command.py:468`) streams `Block.from_json(line)` → `node.add_block(block)`. Validity is enforced (`add_block` → `chain.add_block`/`create_chain` → `Chain.validate_block`, the verification audit's domain). But the **dedup gate uses the self-reported `block.block_hash` field** (`Block.from_db(block.block_hash)`, `command.py:483`) to decide skip-vs-add, and the count pass `sum(1 for line in f)` (`command.py:478`) plus `Block.from_json(line)` impose **no line-length or file-size bound** — a single multi-GB line is read whole.
- **`export_blocks_command`** (`command.py:417`) writes to `click.Path()` (no `exists`/dir guard), `open(file, 'a'|'w')` — append-vs-overwrite is decided by `read_last_line` + a self-reported-hash chain-match check (`command.py:435-438`); an adversarial pre-seeded file shapes that decision.
- **`host_api_client`** (`command.py:81`) derives the signing wallet from the **host URL's username** (`host_address` → `current_app.wallets.get(address)`); a crafted `--host`/config value selects *which local private key signs* and *where the signed request is sent* (credential-selection + SSRF-shaped concerns).
- **`wallet create`** (`command.py:913`) writes the new private key via `Wallet.to_file` → `open(filename, 'wb')` (`wallet.py:~225`) with **no `O_EXCL`** (silently overwrites an existing key at that path) and **no `chmod 0o600`** (the PEM lands at the process umask — typically world-readable `0o644`), and passes **no passphrase** (the key is stored unencrypted). A world-readable, unencrypted private key is readable by any other local user/process — an external adversary, not the operator harming themselves.
- **Error paths** print via `console.print_exception()` (`command.py:461,487`) and one bare `raise Exception` (`command.py:115`) — examined for sensitive-material disclosure (key paths, signatures, secrets in tracebacks) and partial-state-on-failure.

> The seeds above are **illustrative, not exhaustive.** The fan-out must walk *every* command body, including the ones not seed-listed — the `txn/*` write commands (`transfer`/`subject`/`forgive`/`support`, `command.py:608/685/760/835`) whose `--host` / `--wallet-file` / `--txn-wallet` options are Category-3/4 surfaces structurally identical to the seeded ones, and `wallet create`'s `--walletdir` handling.

## Scope & trust boundaries

### In scope

- **The command module** (`src/cancelchain/command.py`) in full: every `@click.command`/`AppGroup` body and the module helpers — `host_api_client`, `address_wallet`, `read_last_line`, `grumble_to_curmudgeons`, the progress classes, and the `init`/`sync`/`validate`/`export`/`import`/`mill` + `txn`/`wallet`/`subject` command bodies.
- **The CLI entry wiring** (`src/cancelchain/__init__.py` `cli`/`create_app` as it builds the command tree, registers groups, and loads `WALLET_DIR`/config that the commands consume).
- **CLI-owned file & path handling**: `import`/`export` file I/O, `read_last_line` seek logic, `Wallet.from_file(wallet_file)` *path* handling (not the key parsing — that's the crypto audit's), the `click.Path(exists=...)` choices.
- **CLI-owned config/credential selection**: `host_address` parsing, `DEFAULT_COMMAND_HOST`/`--host`/`--wallet-file` resolution, which local wallet is chosen to sign, where requests are sent.
- **CLI error/output hygiene**: `console.print_exception()` and broad `except Exception` bodies, as disclosure / partial-state surfaces.

### Trusted boundaries (reference, do not re-audit)

- **Block/transaction validity.** `import` routes file bytes through `node.add_block` → `chain.add_block`/`create_chain` → `Chain.validate_block` — the **verification audit's** domain (0/0/0/0). A finding that reduces to "an invalid block is accepted" is cross-referenced there, not claimed here.
- **RSA key parsing / crypto soundness.** `Wallet.from_file` / `Wallet(...)` key handling is the **wallet/crypto audit's** domain (0/0/0/0). This audit owns the *file-path and passphrase wiring*, not the key parse.
- **Network gossip/sync resource exhaustion & peer protocol.** The **networking audit's** domain (0/0/0/0). This audit owns the CLI *invocation* (`sync`/`mill` argument handling), not the protocol it drives.
- **API authentication.** The **auth audit's** domain (0/0/0/0).

**Framing consequence (the scope razor):**

- "An invalid block/transaction is accepted" → verification audit. "A malformed key is mishandled" → crypto audit. "A peer exhausts the node over the network" → networking audit. "An unauthorized request is honored" → auth audit. Cross-reference; do not re-claim.
- This audit owns: *"a CLI command, fed an adversarial file / path / host / config value the operator was handed, mishandles it — unbounded resource use on import, a path/overwrite/credential-selection mistake, a poisoned dedup/append decision, or sensitive-material disclosure in an error path — independent of the validated subsystems it invokes."*

### Explicitly out of scope

- `browser.py` and the web UI (its own offered audit — different threat model).
- The validators, key primitives, network protocol, and auth themselves (cross-referenced, per above).
- Hardening against a fully malicious *operator* attacking their own node/keys (the operator is trusted; the adversary supplies untrusted artifacts the operator feeds in).
- Third-party Python-dependency vulnerabilities in `click`/`rich`/`httpx` (covered by the supply-chain CVE workflow, not this audit).

## Adversary categories

Six lenses tailored to the operator surface. A single concrete attack may touch more than one.

1. **Malicious import-file supplier** — crafts the `cancelchain import` `.jsonl`: a single unbounded line / oversized file (memory DoS), a self-reported `block_hash` chosen to collide-and-skip or mis-route the dedup gate (`command.py:483`), or a stream shaped to wedge the import mid-batch leaving partial chain state. (Block *validity* is cross-referenced; the *command wiring and resource handling* are owned here.)
2. **Malicious export-target / pre-seeded file** — a pre-existing file at the `export` path crafted so `read_last_line` + the chain-match check (`command.py:435-438`) flip append↔overwrite, or a path/symlink causing `export` to clobber or append to something unintended.
3. **Hostile host/config supplier** — a `--host` / `CC_DEFAULT_COMMAND_HOST` / `CC_PEERS` value whose parsed username (`host_address`) selects an unexpected **local signing wallet**, or whose host steers the signed request to an attacker-controlled endpoint (credential-selection confusion + SSRF-shaped leak of a valid `cc-sig-v1` signature).
4. **Wallet-file read & write surface** — *read side:* a `*.pem` (via `--wallet-file` or `WALLET_DIR`) with a path/loading trick that mis-binds the operator to an unintended identity (key *parsing* cross-referenced to the crypto audit). *Write side:* the `wallet create` output — PEM file permissions (`0o600` vs world-readable), silent overwrite of an existing key (`O_EXCL`), and whether the key is written encrypted at all (no passphrase path exists today). A credential written world-readable is exposed to *other* local users/processes (an external adversary), independent of operator trust.
5. **Info-disclosure / error-hygiene** — `console.print_exception()` / broad `except` / bare `raise Exception` (`command.py:115`) leaking private-key file paths, signatures, secrets, or host credentials into stdout/logs, or masking a partial failure as success.
6. **Resource / robustness** — unbounded `import` line/file, the double file-read pass (`command.py:478,480`), `read_last_line` behavior on adversarial/empty/binary files (the `OSError` catch fires for any file too short to seek `-2` from the end — one-line *and* empty/0–1-byte files — falling back to `seek(0)`; a seekable binary file with no `\n` is a separate edge), the **silent skip** in `import` (`command.py:483-485` advances the progress bar whether a block is added or skipped, so a crafted file whose self-reported `block_hash` collides with an existing block drops that block with *no operator-visible warning*), and any CLI flow that commits partial state or leaves a half-written export on failure.

## Methodology — multi-agent Workflow fan-out

Executed (during the implementation plan) as a Workflow mirroring the prior four audits. **Running it requires the user's explicit opt-in at execution time; this design phase produces only documents.**

1. **Discover (fan-out):** one analyst agent per adversary category, each given the in-scope file set, the scope razor, and its lens; returns structured candidate findings (attack, `file:line`, precondition, impact, proposed severity).
2. **Verify (adversarial):** ≥3 independent refuters per candidate attempt to disprove it against the trusted-boundary controls (the `Chain.validate_block` pipeline, the crypto audit's key handling, the networking audit's bounds, `click.Path` guarantees, the operator-trust model). A finding survives only if a majority fail to refute. This kills the dominant false positive — "the operator can harm their own node" (self-harm, not a finding) — and anything that reduces to a cross-referenced audit.
3. **Synthesize:** dedupe survivors, assign final severities, compile confirmed strengths.

## Severity rubric

Critical/High/Medium/Low, graded on:

- **Reachability / adversary** — driven by an artifact an external party realistically supplies (a shared export file, a config value, a dropped `*.pem`) ⇒ higher; requires the operator to actively target their own node ⇒ self-harm, not a finding.
- **Impact** — silent acceptance of attacker-chosen state, signing-as-the-wrong-identity, exfiltration of a valid signature/secret, or key-material disclosure ⇒ higher; a bounded local crash / file clobber the operator triggers on themselves ⇒ lower or non-finding.
- **Whether it reduces to a trusted boundary** — if the real defect is in a validated/cross-referenced subsystem, it is that audit's, recorded here only as a cross-reference.

**Self-harm carve-out — one exclusion (do not let the verify phase kill it).** A CLI command that *writes a credential or secret world-readable / unencrypted* (e.g. `wallet create`'s PEM) is **not** self-harm even though the operator runs the command — the adversary is a *different* local user or process that reads the file. The operator isn't attacking themselves; they're creating an artifact a third party can exploit. Grade such findings on the third-party exposure, not on "the operator ran it."

## Deliverable / output format

- **Audit report:** `docs/superpowers/audits/2026-06-02-cli-audit.md`, structured like the prior four — executive summary with the `N/N/N/N` headline, per-adversary traces, findings table (id, adversary, severity, description, status, demonstration test), cross-cutting observations (incl. confirmed strengths — `click.Path(exists=True)` on inputs, the `add_block` validation routing, the host-username→wallet self-binding), and Recommendations.
- **Demonstration tests:** a new `tests/test_cli_audit.py`, one `@pytest.mark.xfail(strict=True)` per finding (strict-xfail while open, passing regression once remediated). CLI tests drive commands via Click's `CliRunner` / the existing command-test fixtures (`tests/test_command.py` patterns) with temporary files/dirs; **no test reads untrusted network input, exhausts real memory/disk, or writes outside a `tmp_path`** — resource findings use the bounded-observation convention (drive the uncapped behavior to a small safe bound and assert the missing guard).
- **Test fixtures:** reuse `tests/conftest.py` wallets/app and `tests/test_command.py` CLI-runner setup; `tmp_path` for all file I/O.

## Close-out flow

Each finding is remediated individually (brainstorm → spec → plan → execution, internal cross-model review to convergence, one Copilot backstop), flipping its strict-xfail demonstration to a passing regression and driving the audit toward **0/0/0/0**. Tracked under "Audit remediation — CLI findings" in the roadmap.

## Non-goals

- Remediation itself (this spec covers producing the audit; fixes are separate cycles).
- Re-auditing the verification / auth / networking / crypto layers (cross-referenced only).
- The web/browser UI.
- Dependency-CVE scanning (the supply-chain workflow owns that).
- General CLI UX/ergonomics that carry no security consequence.

## Acceptance criteria for this design

- Scope, trust boundaries, and the scope razor are unambiguous: every candidate is classifiable as in-scope, cross-reference-only, or out-of-scope — with explicit pre-commitment that "operator harms own node" is self-harm, not a finding.
- The six adversary categories cover the operator surface (import, export, host/config, wallet-file, error output, resources) with no obvious gap.
- The methodology is the approved three-phase fan-out (run under explicit opt-in during the impl plan).
- The deliverable shape (report + `tests/test_cli_audit.py`, strict-xfail + bounded-observation, `CliRunner`-driven, `tmp_path`-confined) matches the prior audits' proven format.
- The audit causes no real resource exhaustion and writes nothing outside `tmp_path` when its demonstration tests run.
