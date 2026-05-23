# Merge When Green (mwg)

Wait for CI to pass on the current PR, then squash merge and delete the branch.

## Steps

1. Determine the PR number — use the current branch's open PR (`gh pr view --json number --jq .number`), or accept a PR number as an argument.
2. Wait for CI checks to pass: `gh pr checks {n} --watch`
3. Squash merge and delete branch: `gh pr merge {n} --squash --delete-branch`

## Important
- Do NOT merge if CI fails — report the failure to the user instead.
- Always use `--squash --delete-branch` (never regular merge or rebase).
- If the merge fails (e.g., conflicts, branch protection), report the error to the user.
