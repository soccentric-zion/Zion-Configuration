#!/bin/bash
# fix-issue.sh — wrapper for the §9a fix-bugs loop
# Usage: ./scripts/fix-issue.sh <issue-number> "<branch-slug>" "<commit-msg-line>"
#
# Does: open fix/<#>-<slug> from develop, mark in-progress, leave a
# "Started." comment, run make validate, commit, push, open PR with
# body-file, rebase-merge, close the issue. Exits 0 on success.

set -euo pipefail

ISSUE="$1"
SLUG="$2"
MSG="$3"

cd /mnt/data/workspace/low-impact-web

# 1. Sync develop
git fetch origin >/dev/null
git checkout develop
git rebase origin/develop >/dev/null

# 2. Duplicate-work guard
if gh pr list --state open --search "$ISSUE" --json number --jq '.[0].number' | grep -q .; then
    echo "PR already open for #$ISSUE — skipping"
    exit 0
fi
if git branch -a | grep -q "fix/${ISSUE}-"; then
    echo "Branch fix/${ISSUE}-* exists — skipping"
    exit 0
fi

# 3. Branch + label + comment
git checkout -b "fix/${ISSUE}-${SLUG}"
gh issue edit "$ISSUE" --add-label "in-progress" >/dev/null
gh issue comment "$ISSUE" --body "Started. Branch: fix/${ISSUE}-${SLUG}" >/dev/null

echo "[$ISSUE] $MSG"
echo "Now: implement, edit, run 'make validate', then call scripts/finish-issue.sh $ISSUE $SLUG"
