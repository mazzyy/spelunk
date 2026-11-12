# Spelunk

**See everything that fills your Mac's disk — including the hidden "System Data" — and decide, item by item, what to delete or move to an external drive.**

![Spelunk dashboard](docs/screenshot.png)

macOS hides most of what fills a disk. A 256 GB MacBook can report 80 GB of "System Data" with no breakdown and no safe way to clean it: app caches, Docker disks, WhatsApp media, update snapshots, Homebrew, editor caches, Python environments. Finder won't show it, the Storage screen won't explain it, and deleting the wrong folder in `~/Library` breaks apps.

Spelunk scans your disk, explains every big item in plain language, tells you whether it's safe to remove, and lets **you** decide. Nothing is deleted or moved without your confirmation.

## What it does

- **Shows the whole disk.** Your home folder, apps, `/Library`, Homebrew, system temp and swap, plus macOS volumes (System, Preboot, Recovery, VM) and local snapshots. Whatever it can't read is shown as "hidden" with the reason, not silently left out.
- **Explains what things are.** A knowledge base of 100+ macOS locations: what it is, which app owns it, what happens if you remove it, and the better way to shrink it (e.g. `docker system prune -a` or WhatsApp › Settings › Storage).
- **Rates safety.** `Safe to clear` (rebuilds itself), `Your call` (depends on you), `Keep / manage in app`, `Never touch` (blocked).
- **Keeps you in the loop.** Add items to a review list, see exactly what will happen, then confirm.
- **Moves files safely to an external drive.** Copies, verifies every file with SHA-256, writes a manifest, and only then removes the original. Any failure leaves the original untouched.
- **Logs everything** to `~/.spelunk/actions.jsonl`.
- **Stays local.** No network, no telemetry, no account. The dashboard only listens on `127.0.0.1` with a random per-launch token.

## Quick start

Requires macOS and Python 3.9+ (the `python3` that comes with Xcode Command Line Tools is fine). No dependencies.

```bash
git clone https://github.com/mazzyy/spelunk.git
cd spelunk
python3 -m spelunk
```

Your browser opens the dashboard and the first scan starts. A full scan of a typical developer Mac takes about a minute.

Prefer the terminal?

```bash
python3 -m spelunk scan            # report of the largest items with explanations
python3 -m spelunk scan ~ --top 50 # only your home folder
python3 -m spelunk explain ~/Library/Caches/com.microsoft.VSCode.ShipIt
```

### See more: grant Full Disk Access

macOS blocks apps from reading Mail, Messages, Safari data, parts of `~/Library` and more. To let Spelunk measure them, give the app you run it from (Terminal, iTerm, VS Code…) Full Disk Access:

**System Settings › Privacy & Security › Full Disk Access › +** and add your terminal, then restart it and rescan.

Spelunk still works without it; those folders are shown as "no access" and counted under *Hidden*.

## How safety works

| Rule | Why |
|---|---|
| Nothing happens without a plan you've seen | Every action shows the exact items, sizes and warnings first. |
| Plans are single-use and expire after 10 minutes | A stale plan can't be replayed. |
| `Never touch` items and system folders are blocked in code | Keychains, `/System`, your home's top-level folders, `~/.ssh` and more are refused even if a rule file is edited badly. |
| `Your call` / `Keep` items need an extra confirmation | You tick "I've reviewed these items". |
| Moves are copy → verify → delete | SHA-256 for every file. A manifest (`Spelunk Archive/manifest.jsonl`) on the drive records where everything came from. |
| Move to Bin is the default | You can still restore from the Bin. Space is freed when you empty it. |
| Symbolic links | Recreated on drives that support them; on exFAT they're listed in the manifest with their target. |

## Project layout

```
spelunk/
  scanner.py        fast stdlib scanner (allocated size, hard links once, no symlink following)
  system.py         APFS volumes, snapshots, external drives (diskutil / tmutil)
  knowledge.py      path → explanation engine + hard deny list
  rules/macos.json  the knowledge base (add rules here!)
  actions.py        plan / execute: Bin, delete, verified move
  server.py         local API (token + host check)
  web/              dashboard (vanilla JS, no build step)
tests/              pytest suite (runs on macOS and Linux)
ROADMAP.md          where this is going, in detail
CLAUDE.md           rules for AI coding agents working on this repo
```

## Contributing

The most valuable contribution is **knowledge**: a folder on your Mac that's big and confusing, what it is, and whether it's safe to remove. See [CONTRIBUTING.md](CONTRIBUTING.md). Bigger plans are in [ROADMAP.md](ROADMAP.md).

```bash
python3 -m pip install pytest
python3 -m pytest
```

## License

MIT — see [LICENSE](LICENSE).
