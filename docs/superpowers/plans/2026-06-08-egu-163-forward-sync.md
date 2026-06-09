# EGU #163 resumable forward sync — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A node can network-sync a chain of any depth from genesis (or catch up far behind) resumably, peer-to-peer — by fetching the peer's longest chain *forward by height* in batches and committing each block genesis-first (the `import` model over HTTP). No `MAX_CHAIN_FILL_DEPTH` ceiling, no giant transaction, no out-of-band export.

**Architecture:** A new READER GET endpoint serves longest-chain blocks for a height range (from the `LongestChainBlockDAO` materialization). A `Node.sync_forward` routine fetches forward from the local tip, verifies integrity + prev_hash linkage, validates + commits each block (`add_block(commit=True)`), advancing the tip — so the committed tip *is* the resume point. The backward `fill_chain`/`ChainFill`/`MAX_CHAIN_FILL_DEPTH` path is untouched (gossip short-fill).

**Spec:** `docs/superpowers/specs/2026-06-08-egu-163-forward-sync-design.md`

**Reference (read before starting):**
- `src/gumptionchain/api.py` — `BlockView` (`:303`) + `authorize_reader` (`:297`) + `add_url_rule` pattern; query-model validation pattern (`TransferTxnQueryModel`, pydantic `BaseModel` + `model_validate(request.args.to_dict())`); `make_json_response`, `make_error_response`.
- `src/gumptionchain/models.py` — `LongestChainBlockDAO` (`position` 0=genesis ascending, unique; `block_id`→BlockDAO); `BlockDAO.longest_chain_blocks_q()` (the join to mirror).
- `src/gumptionchain/api_client.py` — `get_block`/`get` (signed-request client pattern).
- `src/gumptionchain/node.py` — `fill_chain` (leave alone), `request_block` (`get_header_hash` integrity check to mirror), `add_block` (`:187`), `request_latest_blocks`.
- `src/gumptionchain/chain.py` — `is_genesis_block(block)` (`prev_hash == GENESIS_HASH`), `GENESIS_HASH`.
- `src/gumptionchain/command.py` — `sync_blocks_command` (`:388`).
- `src/gumptionchain/config.py` — `EnvAppSettings` (add `SYNC_BATCH_SIZE`).
- Tests: `tests/test_network_audit.py` (fill_chain tests + `requests_proxy`/`remote_requests_proxy` peer-routing), `tests/conftest.py` (fixtures, `mill_block`).

---

## PR 1 — forward-fetch endpoint + client + config

Branch: `feat/egu-163-blocks-range-endpoint` off fresh `main`.

### Task 1: `SYNC_BATCH_SIZE` config

**Files:** `src/gumptionchain/config.py`

- [ ] Add `SYNC_BATCH_SIZE: int = field(default=256)` to `EnvAppSettings` (env `GC_SYNC_BATCH_SIZE`), mirroring `MAX_CHAIN_FILL_DEPTH`. Commit: `feat(config): SYNC_BATCH_SIZE for forward block sync`.

### Task 2: `BlockDAO.longest_chain_blocks_range` (TDD)

**Files:** `src/gumptionchain/models.py`, `tests/test_block.py` (or a models test)

- [ ] **Step 1: Failing test** — on a milled chain of a few blocks, `db.session.scalars(BlockDAO.longest_chain_blocks_range(1, 2)).all()` returns the canonical blocks at heights 1,2 in ascending order; a range beyond the tip returns `[]`; a fork block at a shared height is NOT returned (only longest-chain membership).

- [ ] **Step 2: Implement** (mirror `longest_chain_blocks_q`):

```python
@classmethod
def longest_chain_blocks_range(
    cls, from_idx: int, limit: int
) -> Select[tuple[BlockDAO]]:
    """Longest-chain blocks at positions from_idx .. from_idx+limit-1,
    ascending (genesis→tip). position is 0-at-genesis and == block height."""
    return (  # type: ignore[no-any-return]
        db.select(BlockDAO)
        .join(
            LongestChainBlockDAO,
            BlockDAO.id == LongestChainBlockDAO.block_id,
        )
        .where(
            LongestChainBlockDAO.position >= from_idx,
            LongestChainBlockDAO.position < from_idx + limit,
        )
        .order_by(LongestChainBlockDAO.position)
    )
```

- [ ] **Step 3: Run** → PASS. Commit: `feat(models): longest_chain_blocks_range for forward sync`.

### Task 3: `GET /api/blocks?from_idx=&limit=` endpoint (TDD)

**Files:** `src/gumptionchain/api.py`, `tests/test_api.py`

- [ ] **Step 1: Failing test** (READER-authed via `ApiClient`/test client) — `GET /api/blocks?from_idx=0&limit=2` returns a JSON array of the genesis + block-1 (ascending); `limit` is clamped to `SYNC_BATCH_SIZE`; `from_idx` past the tip → `[]`; `from_idx=-1` or `limit=0` → 400.

- [ ] **Step 2: Implement** a `BlocksView(MethodView)` + query model:

```python
class BlocksQueryModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    from_idx: int = Field(ge=0)
    limit: int = Field(ge=1)


class BlocksView(MethodView):
    def get(self, **kwargs: Any) -> Response:
        try:
            model = BlocksQueryModel.model_validate(
                request.args.to_dict(flat=True)
            )
        except ValidationError as e:
            return make_error_response(_pydantic_validation_error(e))
        try:
            limit = min(model.limit, current_app.config['SYNC_BATCH_SIZE'])
            rows = db.session.scalars(
                BlockDAO.longest_chain_blocks_range(model.from_idx, limit)
            ).all()
            return make_json_response(
                [json.loads(Block.from_dao(b).to_json()) for b in rows]
            )
        except GCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)


blueprint.add_url_rule(
    '/blocks',
    view_func=authorize_reader(BlocksView.as_view('blocks_reader')),
    methods=['GET'],
)
```

> Return the blocks as a JSON array of block objects (match however `BlockView` serializes a single block — reuse `Block.to_json`/`to_dict` so the client can `Block.from_json`/`from_dict` each). Confirm `Block`/`json` are imported in `api.py`.

- [ ] **Step 3: Run** → PASS. Commit: `feat(api): GET /api/blocks height-range fetch for forward sync`.

### Task 4: `ApiClient.get_blocks` (TDD)

**Files:** `src/gumptionchain/api_client.py`, `tests/test_api_client.py`

- [ ] **Step 1: Failing test** — `ApiClient(host, wallet).get_blocks(0, 2)` (routed via `requests_proxy`) returns a `list[Block]` of length 2, ascending by idx.

- [ ] **Step 2: Implement** mirroring `get_block` (signed `self.get('/blocks', params=...)`), parsing the JSON array into `[Block.from_json(json.dumps(b)) ...]` (or `Block.from_dict`). Return `list[Block]`.

- [ ] **Step 3: Run** → PASS. Full gates. Commit: `feat(api-client): get_blocks(from_idx, limit)`. Open PR.

---

## PR 2 — forward-sync routine + `sync` command

Branch: `feat/egu-163-forward-sync` off fresh `main` (after PR 1).

### Task 5: `Node.sync_forward` (TDD — the core)

**Files:** `src/gumptionchain/node.py`, `tests/test_forward_sync.py` (new)

- [ ] **Step 1: Failing tests** — use the `remote_requests_proxy` pattern to stand up a "peer" node holding a chain, and a fresh local node:
  - **deep-chain adoption past the cap**: set `MAX_CHAIN_FILL_DEPTH` small (e.g. 3) and `SYNC_BATCH_SIZE` small (e.g. 2); build a peer chain of, say, 7 blocks; `node.sync_forward(peer_client)` from an empty node adopts all 7 (proves the ceiling is gone). Assert the local longest chain tip idx == 6.
  - **resumability**: after syncing a few blocks, run `sync_forward` again — it resumes from the tip and finishes; idempotent when already caught up (returns CAUGHT_UP, no error).
  - **divergence detect**: make the peer return, at the next height, a block whose `prev_hash` ≠ the local tip → `sync_forward` stops, returns a DIVERGED result naming the height, and commits nothing past the fork (local tip unchanged from before the bad block).
  - **integrity**: a returned block whose `get_header_hash() != block_hash` is rejected (sync stops; tip unchanged).

- [ ] **Step 2: Implement** `sync_forward`:

```python
def sync_forward(
    self, client: ApiClient, progress: Any | None = None
) -> str:
    """Fetch the peer's longest chain forward by height and commit each
    block genesis-first. Resumable: the committed tip is the progress.
    Returns 'caught_up' or 'diverged'."""
    batch_size = current_app.config['SYNC_BATCH_SIZE']
    while True:
        tip = self.longest_chain.last_block if self.longest_chain else None
        next_idx = (tip.idx + 1) if tip is not None else 0
        blocks = client.get_blocks(next_idx, batch_size)
        if not blocks:
            return 'caught_up'
        for block in blocks:
            # integrity: computed header hash must match the self-reported id
            if block.get_header_hash() != block.block_hash:
                self.logger.warning('forward-sync: header-hash mismatch')
                return 'diverged'
            # linkage: must extend our current tip (genesis links to sentinel)
            tip = self.longest_chain.last_block if self.longest_chain else None
            expected_prev = tip.block_hash if tip is not None else GENESIS_HASH
            if block.prev_hash != expected_prev:
                self.logger.warning(
                    'forward-sync: diverged at idx %s', block.idx
                )
                return 'diverged'
            self.add_block(block, commit=True)  # validate + commit (genesis-anchored)
            if progress is not None:
                progress.next()
```

> `add_block` validates (PoW, merkle, index, txns) and commits per block — the proven `import` path. A validation failure raises; let it propagate (or catch + return a status — match the `sync` command's error handling). Import `GENESIS_HASH` from `gumptionchain.chain` if not already. Re-read `self.longest_chain.last_block` each block (it advances). Guard the empty-chain / first-genesis case.

- [ ] **Step 3: Run** → PASS. `uv run mypy`. Commit: `feat(node): sync_forward — resumable forward block sync`.

### Task 6: wire into the `sync` command (TDD)

**Files:** `src/gumptionchain/command.py`, `tests/test_command.py`

- [ ] **Step 1: Failing test** — a `sync` invocation against a peer ahead of the local node catches it up (forward), reporting per-peer outcome; a peer not ahead is a no-op.

- [ ] **Step 2: Implement** — change `sync_blocks_command` so that per peer it runs `node.sync_forward(client_for_peer, progress=...)` instead of `fill_chain` (use the peer's `ApiClient` from `node.clients` / `request_latest_blocks` to pick peers ahead of us). Keep the progress-bar + per-peer try/except. Leave `Miller.poll_latest_blocks`/`fill_chain` untouched.

> Confirm how `sync` maps a peer URL to its `ApiClient` (via `create_clients`/`node.clients`). `request_latest_blocks()` yields `(Block, peer)`; only forward-sync peers whose tip idx > local tip idx.

- [ ] **Step 3: Run** → PASS. Full gates (`uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest`). Commit: `feat(sync): use forward-sync in the sync command`. Open PR.

---

## Final

After both PRs merge: final reviewer over the combined diff (focus the integrity/linkage checks + the resumability/divergence invariants); update the EGU checklist (#190) to mark #163; note the deferred follow-ups (full reorg-via-forward-sync; parallel download) — file the reorg one as an issue.
