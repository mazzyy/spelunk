# CLAUDE.md — working on Spelunk

Spelunk is a tool that **deletes and moves files on people's computers**. Treat every change to `actions.py`, `knowledge.py` (deny lists) and `rules/` as safety-critical.

## Non-negotiable safety invariants

1. **No action without a user-approved plan.** `Actions.plan()` must stay read-only. Only `Actions.execute(plan_id)` may change the disk, and only for a plan the user saw in the UI or CLI.
2. **Hard deny list wins over everything.** `knowledge.hard_denied()` and rules with `"safety": "never"` must block actions even if the UI or API is called directly. Never weaken these lists without a test proving why.
3. **Move = copy → verify (SHA-256) → delete.** Never delete a source before every file is verified. On any failure, keep the original.
4. **Re-check right before acting.** The disk can change between plan and execute (`_run` calls `check()` again).
5. **Local only.** The server binds `127.0.0.1`, requires the per-launch token on every `/api/*` request, validates the `Host` header and requires JSON bodies. No outbound network calls in core code. Any future AI/explain feature must be opt-in and send paths/names only, never file contents.
6. **Ask before touching protected folders.** In app mode nothing may read the disk before the welcome screen's choice (`App.needs_onboarding()`); don't add startup work that lists or reads user folders.
7. **Never run destructive tests against a real home folder.** Tests use `tmp_path` fixtures (`tests/conftest.py`).
8. Insert file names into the DOM as text (`h()` helper / `textContent`), never as HTML.

## Conventions

- Python **3.9+** (macOS's bundled `python3` is 3.9). Use `from __future__ import annotations`; no `match`, no `X | Y` at runtime.
- **Standard library only** in `spelunk/`. Dev-only deps (pytest) are fine in tests. The one exception is `pywebview`, imported lazily in `desktop.py` and bundled only inside `Spelunk.app`; everything must still work without it.
- The Mac app is built by `packaging/build_app.sh` (PyInstaller, `packaging/spelunk.spec`). Data files are found via `os.path.dirname(__file__)`, which works both from source and when frozen; keep it that way and add any new data folder to the spec's `datas`.
- Releases: `.github/workflows/release.yml` builds the DMG on `v*` tags. Keep `__version__` (spelunk/__init__.py) and `pyproject.toml` in sync.
- Web UI: vanilla JS + CSS, no build step, no CDN (the CSP is `default-src 'self'`).
- Sizes are **allocated bytes** (`st_blocks * 512`), shown with decimal units like Finder.
- Every new rule needs `id, match, title, category, safety, what, if_removed, action` and a test in `tests/test_knowledge.py` for anything non-trivial.
- macOS-only calls go in `system.py` and must degrade gracefully on Linux (CI and tests run there too).

## Commands

```bash
python3 -m pytest -q                 # all tests
python3 -m spelunk                   # run the dashboard
python3 -m spelunk scan ~ --top 30   # terminal report
python3 -m spelunk explain <path>    # check a rule
python3 -m spelunk app               # app mode (native window if pywebview is installed)
packaging/build_app.sh               # build dist/Spelunk.app + DMG (macOS only)
```

## Where things are

| File | Role |
|---|---|
| `spelunk/scanner.py` | Size tree (`Node`), scanning, pruning small items into a bucket |
| `spelunk/system.py` | `diskutil`/`tmutil` wrappers, external drives, overview math |
| `spelunk/knowledge.py` | Rule matching (glob, specificity, inheritance, protective override), hard deny |
| `spelunk/rules/macos.json` | The knowledge base |
| `spelunk/apps.py` | Installed apps, last opened, leftovers of removed apps, app data for uninstall |
| `spelunk/duplicates.py` | Duplicate finder (background job) |
| `spelunk/actions.py` | Plans, jobs, trash (Finder) / delete / move, manifest, action log |
| `spelunk/server.py` | HTTP API + recommendations |
| `spelunk/web/` | Dashboard |

See `ROADMAP.md` for milestones; each milestone lists acceptance criteria — implement against them and add tests.
