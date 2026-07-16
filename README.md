# LGHub Roblox Path Syncer

Keeps Logitech G HUB's Roblox profile working after Roblox updates, without stopping or restarting G HUB.

## The problem

Roblox installs every update into a fresh folder: `%LOCALAPPDATA%\Roblox\Versions\version-<hash>\RobloxPlayerBeta.exe`. G HUB stores the absolute path to the exe, so each update leaves your Roblox profile pointing at a folder that no longer exists. Profile auto-switching stops until you re-link the game by hand. Roblox ships updates roughly weekly, so this breaks often.

## What the tool does

`auto_update.py` (or the `.bat` wrapper) runs three steps:

1. Finds the newest `RobloxPlayerBeta.exe` under `%LOCALAPPDATA%\Roblox\Versions`, picked by file modification time.
2. Backs up `settings.db` (with its WAL/SHM companions and a readable JSON dump) to `%LOCALAPPDATA%\LGHUB\backups`.
3. Links the new exe to your existing Roblox entry in G HUB.

If the path is already current, it exits with code 2 and writes nothing.

## Requirements

- Windows 10 or 11
- Python 3.10 or newer on PATH (standard library only, nothing to `pip install`)
- Logitech G HUB, with Roblox added as a game once through the UI
- Roblox

## Setup

**1. Download the tool**

```
git clone https://github.com/HeyItzAine/LGHub-Roblox-Path-Syncer.git
```

Or click Code > Download ZIP on GitHub and extract it. Any folder works; the scripts locate G HUB and Roblox through environment variables, not their own location.

**2. Add Roblox to G HUB (first time only)**

Skip this if G HUB already has a Roblox profile.

1. Open G HUB and click the game selector at the top of the window.
2. Click Add Game (labelled Add application in some versions).
3. Browse to `%LOCALAPPDATA%\Roblox\Versions`, open the newest `version-` folder, and select `RobloxPlayerBeta.exe`.
4. Set up your profile for it as usual.

**3. Run the sync**

Double-click `sync_roblox_path.bat`. If you just added the game, it prints "Already up to date", which confirms everything is wired up. After each Roblox update, run it again and your profile keeps working.

**4. Optional: run it on a schedule**

Open Task Scheduler, create a Basic Task with the trigger "When I log on" (or daily), and point the action at `sync_roblox_path.bat`. Roblox updates then never break the profile.

## Usage

Double-click `sync_roblox_path.bat`, or run:

```
python auto_update.py
```

Exit codes: `0` updated, `2` already up to date, `1` error.

For manual path entry, viewing the current config, and backup/restore, use the menu version:

```
python interactive.py
```

## Backups and restore

Every run that changes something first copies `settings.db` to `%LOCALAPPDATA%\LGHUB\backups\settings_<timestamp>.db`, alongside a `.json` dump you can read in a text editor. To roll back, run `interactive.py`, pick option 5, and choose a backup. Restore stops G HUB, copies the files back, and offers to start it again.

## Files

| File | Purpose |
|------|---------|
| `auto_update.py` | One-shot sync, no prompts |
| `interactive.py` | Menu: sync, manual path, view config, backup, restore |
| `sync_roblox_path.bat` | Double-click wrapper for `auto_update.py` |
| `agent_api.py` | Websocket client for the G HUB agent on port 9010 |
| `core.py` | Path discovery, `settings.db` read/write, backups, process control |
| `monitor.py` | Dev tool that diffs G HUB config before and after a manual UI change |
