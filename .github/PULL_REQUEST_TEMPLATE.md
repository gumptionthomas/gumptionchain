<!-- Conventional Commit title, e.g. feat(api): add subject search endpoint -->

## Summary

<!-- What does this change do, and why? -->

## Changes

<!-- Bullet the notable changes. -->

-

## How to test

<!-- Exact commands / steps a reviewer can run to verify. -->

```console
$ uv run pytest
```

## Checklist

- [ ] Branch named `<type>/<short-description>`
- [ ] `uv run ruff check src tests` passes
- [ ] `uv run ruff format --check src tests` passes
- [ ] `uv run mypy` passes
- [ ] `uv run pytest` passes
- [ ] Schema change? `uv run gumptionchain db migrate` + hand-reviewed migration + `db check` passes
- [ ] Dependency change? `uv.lock` updated and committed
- [ ] Docs updated if behavior or configuration changed
