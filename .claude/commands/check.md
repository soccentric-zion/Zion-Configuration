---
description: The repo's documented full gate — build && test && lint (cargo)
allowed-tools: Bash(make check)
---

!`make check`

This is WebConfig's own full gate. Remember the blast radius: this repo ships
into Zion-OS as the meta-webconfig Yocto layer, so a broken board manifest
here breaks the OS image build in a repo that has no idea why. Manifest/schema
parse checks run in the quick gate (./.claude/validate.sh quick) in seconds.
