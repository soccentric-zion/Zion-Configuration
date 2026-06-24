#!/bin/bash
# finish-issue.sh — commit, push, PR, merge, close.
# Run AFTER scripts/fix-issue.sh and AFTER the edits are made + validated.
#
# Usage: ./scripts/finish-issue.sh <issue-number> "<branch-slug>" "<pr-body-file>"

set -euo pipefail

ISSUE="$1"
SLUG="$2"
BODY="$3"

cd /mnt/data/workspace/low-impact-web

# Make sure we are on the right branch.
BRANCH="fix/${ISSUE}-${SLUG}"
if [[ "$(git branch --show-current)" != "$BRANCH" ]]; then
    echo "Not on $BRANCH — abort"
    exit 1
fi

# Stage only app/, tests/, .gitignore, and config example changes —
# NEVER stage a stray audit.md / spec file. (The .gitignore entry now
# keeps audit.md out.)
git add app tests .gitignore scripts config.example.yaml config.yaml.example 2>/dev/null || git add app tests
git diff --cached --quiet && { echo "Nothing staged"; exit 1; }
git commit -m "fix: ${SLUG} (#${ISSUE})" >/dev/null

git push -u origin "$BRANCH" >/dev/null
gh pr create --base develop --title "fix: ${SLUG} (fix #${ISSUE})" --body-file "$BODY" >/dev/null
PR=$(gh pr list --state open --search "$ISSUE" --json number --jq '.[0].number')
gh pr merge "$PR" --rebase --delete-branch >/dev/null

git checkout develop >/dev/null
git pull --rebase origin develop >/dev/null

gh issue close "$ISSUE" --comment "Fixed in #${PR}." >/dev/null
echo "[$ISSUE] merged as PR #${PR}"
