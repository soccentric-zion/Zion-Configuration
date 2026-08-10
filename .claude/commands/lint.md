---
description: clippy -D warnings + rustfmt --check
allowed-tools: Bash(make lint), Bash(make fmt), Bash(./.claude/validate.sh:*)
---

!`make lint`

Fix with make fmt for formatting; clippy findings by hand. If cargo itself is
absent the gate reports 77 (GATE-BLOCKED) rather than a false pass — never
report that as success.
