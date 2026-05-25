# Wait On Review (wor)

Wait for PR reviews (e.g. Copilot), address any feedback, then report back.

## Steps

1. Determine the PR number — use the current branch's open PR (`gh pr view --json number --jq .number`), or accept a PR number as an argument.
2. Determine the repository (`gh repo view --json nameWithOwner --jq .nameWithOwner`).
3. Launch a background polling loop (~30s interval, up to 10 minutes) checking for reviews and review comments:
   - `gh api repos/{owner/repo}/pulls/{n}/reviews`
   - `gh api repos/{owner/repo}/pulls/{n}/comments`
   - Use `gh pr checks {n} --watch` (or poll) to detect when CI has passed.
   - After CI passes, continue polling until the review is in.
4. When reviews or comments appear, read them all carefully.
5. If Copilot left actionable feedback:
   - Address each comment (fix code, update tests if needed).
   - Run the full test suite (`uv run pytest`) to verify fixes.
   - Format code (`uv run ruff format src tests`).
   - Commit and push the fixes.
   - Post replies to each Copilot comment.
   - **Ask the user to click "Re-request review" next to `copilot-pull-request-reviewer` in the PR's Reviewers sidebar.** Copilot only auto-reviews the initial push; subsequent fix-push reviews are UI-only — there is no working API equivalent (the bot rejects both REST `/requested_reviewers` ("not a collaborator") and the `/copilot review` PR comment is treated as plain text).
   - Resume polling for the next review round once the user confirms they've clicked.
   - Report a summary to the user: what Copilot said, what was changed.
6. If Copilot approved with no actionable comments, report that to the user.
7. After reporting, wait for user instructions — do NOT auto-merge.

## Important
- Do NOT merge the PR. Only the user decides when to merge (they will say "merge it" or "mwg").
- Use background polling to avoid blocking the conversation.
