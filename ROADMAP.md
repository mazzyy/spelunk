# Spelunk Roadmap

This document is written so an AI coding agent (e.g. Claude Code) or a human contributor can pick up any milestone and build it without further context. Each milestone has goals, concrete tasks, technical notes, acceptance criteria and a ready-to-paste prompt.

Read `CLAUDE.md` first: its safety invariants apply to every milestone.

---

## 1. Vision

> Every byte on a Mac should be explainable, and the person who owns the Mac decides what happens to it.

Principles:

1. **Explain, don't hide.** Show "System Data" broken into real things with real names. If something can't be measured, say so and say why.
2. **The user is in the loop.** Spelunk recommends; the user decides. No "one-click clean everything".
3. **Safe by default.** Prefer Bin over delete, verified moves over raw moves, app-native cleanup (`docker system prune`) over deleting app files.
4. **Local and private.** No account, no telemetry, no uploads.
5. **Open knowledge.** The rules that explain macOS folders are the real product. Make them easy to read, test and contribute.

Target users, in order: developers on 256/512 GB MacBooks (Docker, Homebrew, node_modules, venvs, AI models), creators (video, music libraries), everyone else confused by "System Data".

---

## 2. Where we are: v0.1 (MVP, done)

Python standard library + local web dashboard, shipped as a downloadable Mac app.

```
browser (127.0.0.1, token)  ──HTTP──▶  server.py ──▶ scanner.py   (size tree)
                                              ├──▶ knowledge.py + rules/macos.json (explanations, deny list)
                                              ├──▶ actions.py   (plan → execute: Bin / delete / verified move)
                                              └──▶ system.py    (diskutil, tmutil, /Volumes)
```

What works:

- Scans home, `/Applications`, `/Library`, `/opt/homebrew`, `/usr/local`, `/Users/Shared`, `/private/var` in parallel. Allocated size, hard links counted once, symlinks not followed, other volumes skipped, unreadable folders flagged.
- Disk bar: macOS volumes (System/Preboot/Recovery/VM via `diskutil apfs list -plist`), each scanned root, "hidden" (disk used minus what we could see), free. Non-OS snapshots listed with explanations.
- ~100 rules with safety levels, inheritance and a "protective folder wins" override (a `.mov` inside a Photos library is never offered for moving).
- Suggestions grouped into Safe / Your call / Manage in app, with "better way" commands.
- Explore: squarified treemap + list with breadcrumbs.
- Review list → plan dialog → job with progress → results. Bin, permanent delete, verified move to external drive with manifest.
- Action log, Activity tab, Show in Finder.
- **Unused apps tab (v0.2):** installed-app index (Info.plist, Spotlight "last opened", Team IDs via `codesign`), leftovers of removed apps across 11 `~/Library` locations grouped per app, apps not opened for 6 months with their data, uninstall = app + data in one plan.
- **Duplicates tab (v0.2):** size → first/last 64 KB fingerprint → full SHA-256, user folders only, "keep oldest" selection.
- **Bin through Finder (v0.2):** Put Back works and Finder asks for the admin password for apps in /Applications; falls back to moving into `~/.Trash`. Spelunk refuses to remove itself and warns when moving apps to exFAT/FAT drives.
- **Downloadable Mac app:** `Spelunk.app` (PyInstaller + pywebview native window) and a DMG, built by `packaging/build_app.sh` and by GitHub Actions on every `v*` tag (`.github/workflows/release.yml`). Optional Developer ID signing + notarization when secrets are set. Full Disk Access detection with an "Open settings" button, and a first-run welcome screen in the app: nothing is scanned until the user chooses Full Disk Access (one switch, then automatic restart) or folder-by-folder macOS prompts. The choice is saved in `~/.spelunk/settings.json`.
- 49 tests (scanner, apps/leftovers, duplicates, rules, actions incl. failed-verification path, server security).
- Verified on a real MacBook home folder: 518k files scanned in 72 s (Python, read-only).

Known limitations (each is addressed below):

| # | Limitation | Milestone |
|---|---|---|
| L1 | No admin access: `/private/var`, parts of `/Library`, other users are "hidden". | M1 |
| L2 | APFS clones are counted once per copy, so totals can exceed reality. | M1 |
| L3 | Purgeable space and snapshot sizes aren't shown. | M1 |
| L4 | No "last opened" info for apps/files (only modified date). | M1 |
| L5 | Rules only know paths. *(Leftovers of removed apps: done in v0.2.)* | M2 |
| L6 | No old-version detection. *(Duplicate finder: done in v0.2.)* | M2 |
| L7 | *(Done in v0.2: Bin goes through Finder with Put Back.)* | M3 |
| L8 | No restore from the external-drive archive. | M3 |
| L9 | Recommendations like `docker system prune` are copy-paste, not one click. | M3 |
| L10 | exFAT cluster overhead can make moved data 2-4x larger on the drive. | M3 |
| L11 | Python scanner: ~1 min on a big disk, high memory with millions of files. | M4 |
| L12 | The app is a packaged Python app (Apple silicon only, ad-hoc signed until a Developer ID is added). | M4 |

---

## 3. Milestones

### M1 — v0.2 "See everything" (accuracy and visibility)

Goal: the dashboard's numbers match macOS's own Storage screen within ~2%, and "hidden" shrinks to what is truly unreadable.

Tasks:

1. **Full Disk Access detection and onboarding.** *(Done in the app: welcome screen + detection. Remaining: re-check on window focus so the dashboard updates without a restart.)* Probe readability of `~/Library/Mail`, `~/Library/Messages`, `~/Library/Safari`. If blocked, show a first-run card with the exact steps and a "Check again" button. API: `GET /api/permissions` → `{full_disk_access: bool, blocked: [paths]}`.
2. **Optional admin scan.** `python3 -m spelunk scan-system --out ~/.spelunk/system.json` run with `sudo` scans `/private/var`, `/Library`, `/Users/*` (other users: totals only, never names). The server never runs as root; it imports the JSON if present and newer than 24 h. UI shows "Run admin scan" with the command and a copy button.
3. **APFS clone-aware sizes.** On APFS, `getattrlist` with `ATTR_CMNEXT_PRIVATESIZE` gives bytes not shared with clones. Implement via `ctypes` in `system.py` (`private_size(path) -> Optional[int]`), use it for files > 1 MB, fall back to `st_blocks`. Add a "shared with copies" note in the detail panel when private < allocated.
4. **Purgeable space.** Read `NSURLVolumeAvailableCapacityForImportantUsageKey` vs `statfs` free (via a tiny `osascript -l JavaScript` bridge or `ctypes` + Foundation). Show "Purgeable: X GB (macOS frees this automatically)" in the disk bar.
5. **Snapshots in depth.** For each local snapshot: name, date, kind (Time Machine / OS update / third-party). Time Machine snapshots get a "Delete" action (`tmutil deletelocalsnapshots <date>`, needs admin → show command). Explain `com.apple.os.update-MSUPrepareUpdate` ("install or cancel the update").
6. **Last used.** For `.app` bundles and large files, read `kMDItemLastUsedDate` with `mdls -raw -name kMDItemLastUsedDate` (batch with `mdls` on many paths per call). Show "Last opened 8 months ago". Add a Suggestions filter "Apps not opened in 6 months".
7. **Reconcile with macOS.** Show a small table: "macOS says System Data = X; Spelunk explains Y of it". Document the formula in `system.overview()`.

Acceptance criteria:

- On a Mac with Full Disk Access + admin scan, `hidden` < 3% of used space.
- Totals with clones (e.g. `cp -c` a 1 GB file 3x) are counted as ~1 GB, covered by a macOS-only test (skipped on Linux).
- Snapshot list matches `tmutil listlocalsnapshots /`.
- No code path runs the HTTP server as root.

Prompt for Claude Code:
```
Read CLAUDE.md and ROADMAP.md (M1). Implement M1 tasks 1–7 in order, one commit-sized step at a time.
Keep stdlib-only and Python 3.9 compatible. macOS-specific code goes in spelunk/system.py and must
degrade gracefully on Linux. Add tests for every task (mark macOS-only tests with
pytest.mark.skipif(sys.platform != "darwin")). Run python3 -m pytest after each step.
```

---

### M2 — v0.3 "Know everything" (smarter knowledge)

Goal: Spelunk recognises leftovers, duplicates and old versions, not just known paths.

Tasks:

1. **Rule schema + validation.** Add `rules/schema.json` (JSON Schema) and a stdlib validator in `tests/test_knowledge.py` (no `jsonschema` dependency at runtime). Fields to add: `min_bytes` (only suggest above this), `tags`, `docs_url`, `commands` (list of `{label, cmd, needs_admin}`), `app_bundle_ids` (which apps own this rule).
2. *(Done in v0.2.)* **Installed-app index.** Build `{bundle_id: app_path}` from `/Applications`, `~/Applications`, `/System/Applications` (read `Contents/Info.plist` with `plistlib`) plus `mdfind "kMDItemContentType == 'com.apple.application-bundle'"`.
3. *(Done in v0.2.)* **Orphan detection.** For `~/Library/{Application Support,Containers,Group Containers,Caches,Preferences,Saved Application State,HTTPStorages,Logs}/*`: derive the owner (bundle id or team-id prefix like `UBF8T346G9.` = Microsoft) and mark entries whose app isn't installed as "Leftover of an app you removed — safe". Real example this was built from: `group.com.surfshark...` after Surfshark was uninstalled.
4. *(Done in v0.2, via Finder.)* **App uninstaller.** Selecting an app offers "Uninstall app + its N leftovers (X GB)" as one plan. Apps in `/Applications` owned by root (App Store) need admin: show the exact `sudo rm -rf` command instead of failing.
5. **Content-aware detectors** (`spelunk/detectors.py`): Rust `target/` (sibling `Cargo.toml`), Xcode `build/`, Unity `Library/`, Gradle `build/`, `.terraform/`, CMake build dirs, Jupyter checkpoints. Each detector returns a synthetic rule.
6. **Old versions.** VS Code / Cursor extension folders with multiple versions (keep the one referenced by `extensions.json`), `~/.nvm/versions/node/*` not used by any `.nvmrc`, Homebrew (`brew cleanup -n` output parsed), LM Studio / llama.cpp runtime versions.
7. *(Done in v0.2.)* **Duplicate finder.** Group files ≥ 10 MB by size → first 64 KB hash → full SHA-256. Show groups; user picks which copy to keep. Never auto-select inside app bundles or libraries.
8. **"Explain with AI" (opt-in).** For unknown folders ≥ 500 MB: send only the path and the names of the top 20 children (no contents) to a local Ollama model or an API key the user provides. Show the answer clearly labelled "AI guess — verify before acting" and offer to save it as a draft rule the user can submit upstream.

Acceptance criteria:

- Uninstalling a test app bundle in a fixture finds its Preferences/Caches/Containers leftovers.
- Duplicate finder never reads more than the first 64 KB of files with unique sizes (test with a counting stub).
- AI feature is off by default, never sends file contents, and has a test asserting the payload shape.

Prompt for Claude Code:
```
Read CLAUDE.md and ROADMAP.md (M2). Implement M2 tasks 1–7 (skip 8 for now). Put new logic in
spelunk/apps.py (installed-app index, orphans, uninstaller plans), spelunk/detectors.py and
spelunk/duplicates.py. Expose them via new endpoints and a "Leftovers" and "Duplicates" section
in the Suggestions tab. Every destructive path must go through Actions.plan/execute. Add tests.
```

---

### M3 — v0.4 "Act safely" (richer, reversible actions)

Goal: every recommendation can be acted on from the UI, and every action can be undone or traced.

Tasks:

1. *(Done in v0.2; still to do: "Empty Bin" button.)* **Finder Bin with Put Back.** Use `osascript -e 'tell application "Finder" to delete POSIX file "<path>"'` (needs Automation permission; ask once) so items can be restored with Put Back. Fall back to the current rename. Add "Empty Bin" (with confirmation and size).
2. **Archive browser + restore.** New "Archive" tab reading `Spelunk Archive/manifest.jsonl` from connected drives: what was moved, when, from where. "Restore" copies back (verified) to the original path if free.
3. **Safe command runner.** Rules may declare `commands` with an id. `POST /api/run {rule_id, command_id}` runs only commands from the rules file (never user/AI-supplied strings), streams output, and logs it. Initial set: `brew cleanup --prune=all`, `brew autoremove`, `npm cache clean --force`, `pnpm store prune`, `uv cache clean`, `pip cache purge`, `conda clean -a -y`, `docker system prune -a -f` (only if Docker is running), `xcrun simctl delete unavailable`.
4. **Relocation recipes.** One-click "Keep this on the external drive" for: Docker disk image location (guide), Ollama (`OLLAMA_MODELS` via `launchctl setenv` + LaunchAgent), Hugging Face (`HF_HOME`), LM Studio models folder (guide), pnpm `store-dir`, iPhone backups (symlink with warning), Photos library (guide only). Each recipe checks the drive is APFS/HFS where needed and warns that the drive must be connected.
5. **exFAT awareness.** Before moving, estimate size on destination using its cluster size (`diskutil info` → `DeviceBlockSize`/allocation block) and warn when overhead > 25% ("1.6 GB of small files will use 5.7 GB on this exFAT drive — zip it or use an APFS drive"). Offer "move as .zip" for folders with > 10k small files.
6. **Undo window.** For Bin actions, an "Undo" toast for 30 s that moves items back.
7. **Dry-run mode.** `--dry-run` CLI flag and a UI toggle: plans execute as simulations and write to the log with `"dry_run": true`.
8. **Scheduled report (optional).** A LaunchAgent that runs `spelunk scan --json` weekly and shows a notification when free space < 10% or a known offender grows > 5 GB.

Acceptance criteria:

- Command runner refuses any command id not present in rules (test).
- Restore round-trip test: move → restore → identical SHA-256 and path.
- exFAT estimate within 10% on a fixture with many small files (unit-test the math).

Prompt for Claude Code:
```
Read CLAUDE.md and ROADMAP.md (M3). Implement tasks 1–7. The command runner must only execute
commands declared in rules/*.json, looked up by id, executed with subprocess list arguments (never
shell=True), with a timeout and output streaming via a job like actions.Job. Add tests for each task.
```

---

### M4 — v1.0 "A real Mac app"

Goal: a signed, notarized SwiftUI app anyone can install, with the same rules and safety model.

Architecture:

```
Spelunk.app (SwiftUI, sandbox OFF, hardened runtime)
 ├── ScannerKit (Swift)         getattrlistbulk-based parallel scanner, clone-aware sizes
 ├── Knowledge (Swift)          loads the SAME rules/*.json (shared with the CLI)
 ├── Actions (Swift)            plan/execute, FileManager.trashItem (Put Back works natively)
 ├── SpelunkHelper (SMAppService privileged helper, XPC)
 │     only: scan admin-only paths, tmutil snapshot deletion, sudo-requiring removals
 └── Menu bar extra             free space, weekly report, low-space alert
```

Tasks:

1. Xcode project in `macos/`, Swift Package `SpelunkCore` with ScannerKit/Knowledge/Actions and unit tests.
2. Port scanner using `getattrlistbulk` (10–20x faster than per-file `lstat`), bounded memory with the same small-items bucketing.
3. Rules: load `spelunk/rules/*.json` from the app bundle; CI test ensures Python and Swift produce identical explanations for a fixture path list.
4. UI: sidebar (Overview, Suggestions, Explore, Leftovers, Duplicates, Archive, Activity), sunburst + treemap, review list, plan sheet. Match macOS conventions (SF Symbols, system colours, dark mode).
5. Full Disk Access onboarding screen with deep link `x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles`.
6. Privileged helper via `SMAppService.daemon`, minimal XPC API, every call re-validated against the hard deny list inside the helper.
7. Signing + notarization in GitHub Actions (`xcrun notarytool`), DMG via `create-dmg`, Homebrew cask `spelunk`, Sparkle for updates.
8. Keep the Python CLI as `spelunk` for developers; both share rules.

Not planned: Mac App Store (sandboxing prevents whole-disk scanning and Full Disk Access workflows).

Acceptance criteria:

- Cold scan of a 500 GB disk with 2M files < 15 s on Apple silicon, memory < 400 MB.
- Same explanations as the CLI for the shared fixture list.
- Helper refuses any path on the hard deny list (XPC-level test).
- Notarized DMG installs and runs on a clean macOS 14 VM.

Prompt for Claude Code:
```
Read CLAUDE.md and ROADMAP.md (M4). Create macos/ with a Swift package SpelunkCore (ScannerKit,
Knowledge, Actions) and a SwiftUI app target. Start with ScannerKit using getattrlistbulk and a
test that compares totals with the Python scanner on a fixture folder. Reuse spelunk/rules/*.json.
Do not implement deletion in Swift until Knowledge + hard deny tests pass.
```

---

### M5 — Community and open source

1. GitHub Actions: `pytest` on `ubuntu-latest` and `macos-14`; JSON rule validation; ruff lint.
2. Issue templates: "Explain this folder" (path, size, what you think it is), bug, feature.
3. `CONTRIBUTING.md` rule-writing guide (exists) + a `spelunk rules test <path>` CLI to preview a new rule.
4. `SECURITY.md`: how to report issues; the local-server threat model.
5. Docs site (GitHub Pages) generated from `rules/*.json`: one page per known folder ("What is ~/Library/Containers/com.docker.docker?") — great for search traffic and for users who just want an answer.
6. Translations of rule texts (`rules/i18n/<lang>.json`).
7. Release process: semantic versions, changelog, signed tags.

---

## 4. Reference

### Data model

`scanner.Node`: `name, size (allocated bytes incl. children), files, is_dir, children, denied, small_count, parent, mtime`. Children under `min_node_bytes` (default 1 MB) are folded into a `(smaller items)` node after a folder finishes scanning.

### Rule schema (current)

```json
{
  "id": "caches-shipit",
  "match": ["~/Library/Caches/*.ShipIt"],
  "inherit": true,
  "opaque": true,
  "title": "Leftover app-update downloads",
  "owner": "Electron apps",
  "category": "cache | developer | app-data | media | personal | system | temp | logs | installer | backup | apps",
  "safety": "safe | review | keep | never",
  "what": "Plain-language description",
  "if_removed": "What happens if removed",
  "action": "trash | delete | move | app | command | none",
  "how": "Optional better way / command"
}
```

Matching: `~` = home, `*` = within one segment, `**` = any depth. Highest specificity wins (rooted patterns beat `**/` ones). `inherit` rules explain everything inside. A generic (`**/…`) direct match is overridden by an inherited ancestor rule that is more protective, or that is marked `opaque` (the folder is managed by an app or tool, so e.g. a `node_modules` inside the Codex runtime is not "a project's dependencies").

### API (v0.1)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/state` | Scan progress, roots, version |
| POST | `/api/scan` | Start a scan `{roots?}` |
| POST | `/api/cancel` | Cancel scan |
| GET | `/api/overview` | Disk totals, APFS volumes, snapshots, hidden |
| GET | `/api/node?path=` | One folder with explained children |
| GET | `/api/recommendations?min=` | Largest actionable items |
| GET | `/api/explain?path=` | Rule for a path |
| GET | `/api/externals` | Connected drives |
| POST | `/api/plan` | `{kind, paths, dest?}` → plan (read-only) |
| POST | `/api/execute` | `{plan_id, ack}` → job |
| GET | `/api/job?id=` | Job progress/results |
| GET | `/api/history` | Action log |
| POST | `/api/reveal` | Show in Finder |

All `/api/*` need header `X-Spelunk-Token`; POST needs `Content-Type: application/json`; `Host` must be `127.0.0.1:<port>` or `localhost:<port>`.

### Testing strategy

- Unit tests on Linux and macOS with `tmp_path` fixtures only.
- macOS integration tests (skipped elsewhere): APFS clones, `diskutil` parsing with recorded plist fixtures, snapshot listing.
- UI smoke test with Playwright (headless): load, scan fixture, add to review, run a Bin plan, assert results.
- Never point tests at `~`.

### Open questions

- Final name / GitHub org (`spelunk` may clash on PyPI → publish as `spelunk-mac`).
- Keep the Python web UI after the Swift app ships, or CLI only?
- Should "Explain with AI" ship in core or as a plugin?
