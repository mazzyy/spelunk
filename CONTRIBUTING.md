# Contributing to Spelunk

Thanks for helping make Mac storage understandable.

## The easiest contribution: teach Spelunk a folder

If you found a big, confusing folder on your Mac, add a rule to `spelunk/rules/macos.json`:

```json
{"id": "jetbrains-caches", "match": "~/Library/Caches/JetBrains", "inherit": true,
 "title": "JetBrains IDE caches", "owner": "IntelliJ, PyCharm, WebStorm...", "category": "developer",
 "safety": "safe",
 "what": "Indexes and caches for JetBrains IDEs.",
 "if_removed": "The IDE re-indexes projects on next open.",
 "action": "trash"}
```

Guidelines:

- **Be conservative with `safety`.** Only use `safe` if deleting can never lose user data. When unsure, use `review`.
- Use `never` for anything whose loss is irreplaceable or breaks macOS.
- `what` and `if_removed` are read by non-technical people: short, plain sentences, no jargon.
- Prefer the app's own cleanup in `how` (a menu path or a command) over deleting files.
- `match` supports `~`, `*` (one folder level) and `**` (any depth). The most specific pattern wins.
- Add `"inherit": true` when the rule should also explain everything inside the folder.
- Add `"opaque": true` too when an app or tool manages the folder's contents, so generic patterns (`**/node_modules`, `**/*.mp4`) inside it don't produce misleading advice.
- Check it: `python3 -m spelunk explain "/path/to/that/folder"`
- Add a test line in `tests/test_knowledge.py` if the rule is non-trivial.

## Code changes

1. Read `CLAUDE.md` (safety invariants apply to humans too).
2. Python 3.9+, standard library only in `spelunk/`.
3. `python3 -m pytest` must pass.
4. Anything that deletes or moves files needs a test that proves the safe path (blocked items, failed verification keeps the original, etc.).

## Reporting a folder you can't identify

Open an issue with the path, its size and what's inside (folder names only, never personal file contents).
