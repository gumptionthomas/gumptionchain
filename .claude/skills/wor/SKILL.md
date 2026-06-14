# Wait On Review (wor)

Wait for PR reviews (e.g. Copilot), address any feedback, then report back.

## Copilot uses TWO logins — filter each endpoint by its own

A correct poll must know that Copilot authors a PR review and its inline
comments under **different logins**:

| What | Endpoint | Author login |
|---|---|---|
| the **review** object (overview / approval / "commented") | `/pulls/{n}/reviews` | `copilot-pull-request-reviewer[bot]` |
| each **inline comment** | `/pulls/{n}/comments` | `Copilot` |

Filtering *both* endpoints on the same login (e.g. `"Copilot"`) is the bug this
skill exists to avoid: a **clean review** — an overview/approval with **no
inline comments** — is authored only by `copilot-pull-request-reviewer[bot]`, so
a `"Copilot"`-only filter never matches it and the poll times out instead of
detecting the review.

## Steps

1. Determine the PR number — use the current branch's open PR (`gh pr view --json number --jq .number`), or accept a PR number as an argument.
2. Determine the repository (`gh repo view --json nameWithOwner --jq .nameWithOwner`).
3. Launch a background polling loop (~30s interval, up to 10 minutes). The review
   is **in** when a Copilot review exists **OR** any Copilot inline comment
   exists — each endpoint filtered by its own login:
   ```bash
   REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
   PR=<n>
   rev=$(gh api --paginate "repos/$REPO/pulls/$PR/reviews"  --jq '[.[]|select(.user.login=="copilot-pull-request-reviewer[bot]")]|length')
   com=$(gh api --paginate "repos/$REPO/pulls/$PR/comments" --jq '[.[]|select(.user.login=="Copilot")]|length')
   # review is in when:  rev != 0  OR  com != 0  → stop polling
   ```
   - **Always use `--paginate`.** Without it, `gh api` returns at most ~30 items, and the OLDEST items get returned first — so newer review comments after multiple rounds will be silently hidden, making it look like no new review came in. This caused a confused state on PR #58 round 4. Use `--paginate` even on small PRs.
   - Use `gh pr checks {n} --watch` (or poll) to detect when CI has passed.
   - After CI passes, continue polling until `rev != 0` **or** `com != 0`.
4. When the review is in, read it and any inline comments carefully.
   - **A review with state `COMMENTED` or `APPROVED` and no inline comments
     (`com == 0`) is a clean, actionable-free review.** Report it as clean and
     stop — do **not** keep polling for inline comments that will never come.
5. If Copilot left actionable feedback (`com != 0`, or a review body requesting changes):
   - Address each comment (fix code, update tests if needed).
   - Run the full test suite (`uv run pytest`) to verify fixes.
   - Format code (`uv run ruff format src tests`).
   - Commit and push the fixes.
   - Post replies to each Copilot comment.
   - **Ask the user to click "Re-request review" next to `copilot-pull-request-reviewer` in the PR's Reviewers sidebar.** Copilot only auto-reviews the initial push; subsequent fix-push reviews are UI-only — there is no working API equivalent (the bot rejects both REST `/requested_reviewers` ("not a collaborator") and the `/copilot review` PR comment is treated as plain text).
   - Resume polling for the next review round once the user confirms they've clicked.
   - Report a summary to the user: what Copilot said, what was changed.
6. If Copilot approved / commented with no actionable comments, report that clean result to the user.
7. After reporting, wait for user instructions — do NOT auto-merge.

## Important
- Do NOT merge the PR. Only the user decides when to merge (they will say "merge it" or "mwg").
- Use background polling to avoid blocking the conversation.
