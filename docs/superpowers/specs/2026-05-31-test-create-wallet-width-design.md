# Fix `test_create_wallet` Terminal-Width Dependency Design

**Type:** test bug fix (latent on main).

---

## Problem

`tests/test_command.py::test_create_wallet` reconstructs a wallet file path
from the CLI's printed output and loads it:

```python
result = runner.invoke(args=['wallet', 'create', '--walletdir', walletdir])
wallet_filename = result.output.strip()[len('Created ') :]
assert Wallet.from_file(wallet_filename) is not None
```

The `wallet create` command prints via a **Rich `Console`**
(`src/cancelchain/command.py:927` → `console.print(f'Created {filename}', …)`;
`console` is defined in `src/cancelchain/console.py`). Rich soft-wraps output
to the terminal width (80 columns by default under the test harness, narrower
in CI / narrow terminals). When the temp-dir path + address + `.pem` exceeds
that width, Rich inserts newlines **inside** the path. `result.output.strip()`
only trims the ends, so the reconstructed `wallet_filename` keeps the internal
newlines and `Wallet.from_file` raises `FileNotFoundError`.

Reproduced at `COLUMNS=40`:

```
FileNotFoundError: [Errno 2] No such file or directory:
'\n/tmp/.../CCm874wmWvf\njaHsm8iazEAD4peqG6dzvbqTDajmVjso7xCC.pem'
```

This is why the full suite currently requires `COLUMNS=200` — a papercut on
every run. The CLI behaviour is fine (wrapping long paths is reasonable for
humans); the **test** is at fault for treating human-formatted output as a
machine-readable path.

## Goal

Make `test_create_wallet` width-independent so the full suite passes at any
terminal width, without `COLUMNS=200`.

## Approach

Stop parsing the formatted output for a path. Verify the command's actual
contract instead: after `wallet create --walletdir X`, exactly one loadable
`*.pem` file exists in `X`. This is independent of width, styling, wrapping,
and the exact print wording.

## Change

One file: `tests/test_command.py`. Add `from pathlib import Path` to the
imports, and rewrite the test body:

```python
def test_create_wallet(app, runner):
    with app.app_context(), TemporaryDirectory() as walletdir:
        result = runner.invoke(
            args=['wallet', 'create', '--walletdir', walletdir]
        )
        assert result.exit_code == 0
        assert 'Created' in result.output
        pem_files = list(Path(walletdir).glob('*.pem'))
        assert len(pem_files) == 1
        assert Wallet.from_file(str(pem_files[0])) is not None
```

- `Path(walletdir).glob('*.pem')` finds the created wallet without any output
  parsing.
- `len(pem_files) == 1` asserts exactly one wallet was written to the
  requested directory.
- `Wallet.from_file(str(pem_files[0]))` confirms it is loadable (the original
  assertion, now fed a clean path).
- `exit_code == 0` and `'Created' in result.output` retain two cheap signals
  that the command ran and reported success. `'Created'` is a short literal
  substring check, which is wrap-safe.

No source change. The CLI's Rich-wrapped output is unchanged.

## Testing

- Run on a **narrow** terminal to prove width-independence:
  `COLUMNS=40 uv run pytest tests/test_command.py::test_create_wallet -v`
  → PASS (fails on `main`).
- Full suite with **no** `COLUMNS` override:
  `uv run pytest` → all pass (no longer width-dependent).

## Out of scope (non-goals)

- Changing the `wallet create` CLI output / Rich console wrapping (it is
  correct for human use).
- Other `test_command.py` tests — they use short-substring `in result.output`
  checks that don't wrap; only `test_create_wallet` reconstructs a path.
- The `COLUMNS=200` gate-command examples in this session's superpowers
  specs/plans (`docs/superpowers/{specs,plans}/…`) become unnecessary once
  this lands. They are harmless historical records (the prefix still works);
  not worth retro-editing merged docs. (`CLAUDE.md` does not reference
  `COLUMNS=200`.)

## Acceptance criteria

1. `COLUMNS=40 uv run pytest tests/test_command.py::test_create_wallet` passes.
2. `uv run pytest` (no `COLUMNS` override) passes the full suite.
3. `ruff check`/`format` clean, `mypy` clean (no src change). No migration /
   schema change.
