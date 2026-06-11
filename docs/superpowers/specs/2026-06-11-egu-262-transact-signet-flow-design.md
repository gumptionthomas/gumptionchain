# EGU #262 — transact flow: explicit signing-key states

**Date:** 2026-06-11
**Issue:** #262 (follows #260's /advanced split; coordinates with the
signet vocabulary introduced on the hub landing — see hub#30's scope-split
comment)
**Status:** design approved (interactive brainstorm 2026-06-11; layout
mockups in `.superpowers/brainstorm/`, layout B selected)

## Goal

Make `/transact` the conversion surface it actually is: **creating a
transaction is one flow through which people create signets.** Three
directives, verbatim from the brainstorm:

1. A visitor with no signet can create one inline, without leaving the
   flow.
2. **Explicit unlock UI** — a visible locked/unlocked state that gates
   signing, instead of discovering at click time that a key is missing.
3. The per-session key (b58 paste / .pem upload) is a power-user tool:
   collapsed under an "Advanced" disclosure.

## Page composition (layout B: form first, gate at the action)

`/transact` top to bottom:

1. Security alert (one line, lighter than today's paragraph).
2. **Build & sign form** — type/amount/destination/subject/rescind-kind
   fields, unchanged ids and behavior. Filling the form requires nothing.
3. **Signet panel** — the explicit state machine (below). Sits between
   the form and the action: the literal gate.
4. Action area — `Build & review` / `Confirm & submit`, **disabled at the
   markup level** (`disabled` attribute) unless the panel is unlocked.
   The state machine enables them; `requireWallet`'s click-time error
   remains only as a backstop.
5. "Advanced tools →" pointer (unchanged), `transact/extra.html` hook
   (unchanged).

No server-side changes: routes, context (`node_host`, `rp_name`), and
the build/submit API are untouched.

## The signet panel: a three-state machine

Lives in the shared `_key_import.html` partial (so `/advanced` inherits
it identically — its broadcast/attestation tools gate on the same
states). Exactly one state is visible at a time; the JS controller picks
it from `gc-keyring` store contents + in-memory unlock state.

### State 1 — no signet on this device

The panel is an inline **mini-create** (the conversion moment):

- Heading: `Create your signet` with the one-time bridge parenthetical:
  "your signet (a signing keypair) marks your stakes as yours."
- Passphrase field + the same trust acknowledgment `/wallet` uses
  (persisting writes the encrypted keypair to this origin's IndexedDB —
  same checkbox copy).
- `Create your signet` button → `gc-keyring` generate + save (the exact
  machinery behind `/wallet`'s create-btn), then transition straight to
  state 3 (unlocked), ready to sign the txn the visitor came to make.
- Post-create nudge (in the state-3 panel): "Back up your signet on the
  [Wallet] page" — backup download and passkey enrollment deliberately
  stay on `/wallet`; no forced backup step blocks the flow.

### State 2 — signet saved, locked

- Heading: `Your signet` + `locked` badge; shows the saved address.
- Passphrase field + `Unlock` button; `Unlock with passkey` button when
  a passkey is enrolled (today's reveal logic).
- Nothing else — the form above is fillable, the actions below are
  disabled.

### State 3 — unlocked

- `Signing as <address>` badge (green) + `Lock` button.
- Auto-lock behavior unchanged (idle / tab-hide / leave / Forget —
  today's session handling); auto-lock returns the panel to state 2 and
  re-disables the action buttons.
- After an inline create, this state carries the backup nudge line.

### The Advanced disclosure (all states)

`▸ Advanced: use a one-session key instead` — a collapsed disclosure
(Bootstrap collapse, like the broadcast section) wrapping today's
ephemeral import markup (b58 textarea, .pem upload, Import/Forget
buttons, status line). Importing a session key enters a variant of
state 3 with a visually distinct badge — `one-session key · <address>` —
so a session key is never mistaken for the saved signet. Forget returns
to whichever of states 1/2 applies.

## Vocabulary

Signet-first copy throughout the partial and the transact page, with the
bridge parenthetical once per page. This is **part 1 of the base browser
signet sweep** (hub#30 scope split): `/wallet`, `/advanced`, and
`/verify` copy follow as a **sibling PR** in this repo — this PR touches
only the shared partial + `transact.html`, so the sweep PR has no
overlap. Operator surfaces (CLI, `GC_*` config, Pi HOWTO/runbook,
api-auth docs) keep "wallet" permanently — they document the
never-renamed code identifiers.

## Code shape

- **`_key_import.html`** — rewritten as the state-machine markup: three
  state containers (`data-signet-state="none|locked|unlocked"`), all
  initially hidden, plus the Advanced disclosure. Ids the glue already
  binds keep their names where the control survives (`unlock-passphrase`,
  `unlock-saved-btn`, `unlock-saved-passkey-btn`, `key-b58`, `key-pem`,
  `import-key-btn`, `forget-key-btn`, `key-status`); new controls get
  new ids (`signet-create-passphrase`, `signet-trust-ack`,
  `signet-create-btn`, `signet-lock-btn`, `signet-badge`).
- **`transact-glue.mjs`** — gains a small state controller: computes the
  state (store record present? unlocked wallet in memory? session key?),
  shows exactly one state container, toggles the action buttons'
  `disabled`. Create wires to the same keyring call `/wallet`'s glue
  uses. The existing defensive `if (el)` binding style is preserved so
  `/advanced` (no build buttons) and `/transact` share the module
  unchanged.
- **Factor for testability:** the pure state-decision function
  (inputs: has-record / unlocked / session-key → outputs: visible state,
  buttons enabled, badge text) lives where the wallet ESM's existing
  node test infrastructure (`clients/wallet/*.test.mjs`) can exercise
  it. The implementation plan verifies how those tests run and follows
  the established pattern; if the glue is not under that harness today,
  the decision function moves to a small `clients/wallet/` module that
  is.
- `/wallet`'s own page and glue are untouched in this PR.

## Error/edge posture

- Wrong passphrase fails closed with the keyring's existing error
  message; state stays locked.
- Creating when storage is unavailable (IDB blocked) shows the error in
  the panel and falls back to suggesting the Advanced session key.
- The action buttons' disabled state is belt-and-braces: the click-time
  `requireWallet` guard stays.
- `/advanced` renders the same panel; its tools already require-wallet
  at click time and additionally benefit from the explicit state.

## Testing

- **Template (pytest):** the three state containers + Advanced
  disclosure render on `/transact` AND `/advanced` (shared partial);
  action buttons carry `disabled` in initial markup; signet copy +
  bridge parenthetical present; existing transact/advanced/seam tests
  reconciled where they pin old copy or structure.
- **State logic (node, `*.test.mjs`):** the decision function's matrix —
  no record → `none`; record + locked → `locked`; record + unlocked →
  `unlocked` (saved badge); session key → `unlocked` (session badge);
  forget/lock transitions; buttons-enabled only in unlocked states.
- Manual pass on a dev node: create → sign → lock → unlock → sign;
  session-key path under Advanced; `/advanced` tools gated the same way.

## Out of scope

- `/wallet`, `/advanced`, `/verify` copy sweep (sibling PR, this repo).
- Backup/passkey enrollment inline on `/transact` (stays on `/wallet`).
- Any change to the build/submit API or auth scheme.

## Vocabulary amendment (2026-06-11, post-approval)

**Signet is retired** before reaching base (vocab tax, onboarding
confusion, the Bitcoin Signet collision — recorded on hub#30). The
panel ships **"signing key" / "your key"** copy instead: "Create your
signing key", "Your signing key · locked", badges unchanged. The
bridge parenthetical is unnecessary (the term self-explains) and is
dropped. "Wallet" remains acceptable in balance contexts and on
operator surfaces; it is simply not the onboarding word. Identifiers
use a `key-` prefix (`data-key-state`, `key-create-btn`,
`whichKeyPanel`) — the plan reflects this. The "sibling signet sweep"
PR is cancelled: base pages already say wallet, which is acceptable
outside onboarding.
