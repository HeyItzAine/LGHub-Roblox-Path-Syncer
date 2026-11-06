"""
LGHUB Roblox Path Manager - Interactive Mode

Menu-driven tool for managing Roblox paths in LGHUB with options for
auto/manual path updates, viewing config, and backup/restore.
"""

import json
import sys
from pathlib import Path

import agent_api
import core
import sync_service


def show_status(db_path: Path):
    """Print current status info."""
    linked = []
    try:
        settings = core.read_settings_json(db_path)
        roblox = core.get_current_roblox_app(settings)
        current_path = roblox.get("applicationPath", "N/A") if roblox else "Not configured"
        linked = roblox.get("userPaths", []) if roblox else []
    except Exception:
        current_path = "Error reading config"

    try:
        latest = core.find_roblox_player()
        latest_path = str(latest)
    except Exception:
        latest_path = "Not found"

    try:
        if core.is_lghub_running():
            lghub_status = "Running (agent API available)" if agent_api.agent_available() else "Running"
        else:
            lghub_status = "Stopped"
    except core.LGHUBError:
        lghub_status = "Unknown (process check failed)"

    print(f"  Current LGHUB Roblox path: {current_path}")
    for p in linked:
        print(f"  Linked executable:         {p}")
    print(f"  Detected target Roblox:    {latest_path}")
    print(f"  LGHUB Status:              {lghub_status}")
    print()


def handle_auto_update(db_path: Path):
    """Auto-detect latest Roblox and update LGHUB config."""
    try:
        roblox_exe = core.find_roblox_player()
    except core.RobloxNotFoundError as e:
        print(f"Error: {e}")
        return

    new_path = str(roblox_exe).replace("/", "\\")
    print(f"  Target: {new_path}")
    if input("  Proceed? (Y/n): ").strip().lower() != "n":
        _do_update(db_path, new_path)


def handle_manual_path(db_path: Path):
    """Let user enter a custom path."""
    path_str = input("  Enter full path to RobloxPlayerBeta.exe: ").strip().strip('"')
    if not path_str:
        print("  Cancelled.")
        return

    exe_path = Path(path_str)
    if not exe_path.exists():
        print(f"  Error: File not found: {exe_path}")
        return
    if exe_path.name.lower() != core.ROBLOX_EXE_NAME.lower():
        print(f"  Warning: Expected {core.ROBLOX_EXE_NAME}, got {exe_path.name}")
        proceed = input("  Continue anyway? (y/N): ").strip().lower()
        if proceed != "y":
            return

    new_path = str(exe_path).replace("/", "\\")
    _do_update(db_path, new_path)


def _do_update(db_path: Path, new_path: str):
    """Use the same verified update flow as automatic mode."""
    try:
        print("  " + sync_service.describe(sync_service.sync_path(db_path, new_path)))
    except Exception as e:
        print(f"  Update failed: {e}")


def handle_view_config(db_path: Path):
    """Show the current Roblox entry and its profile."""
    settings = core.read_settings_json(db_path)
    roblox = core.get_current_roblox_app(settings)
    if not roblox:
        print("  No Roblox entry found in LGHUB config.")
        return

    print("  Application entry:")
    print(json.dumps(roblox, indent=4))

    profile = core.get_roblox_profile(settings, roblox.get("applicationId", ""))
    if profile:
        print()
        print("  Profile entry:")
        print(json.dumps(profile, indent=4))
    else:
        print("  No profile found for this application.")


def handle_create_backup(db_path: Path):
    """Create a backup of the current settings."""
    backup_path = core.create_backup(db_path)
    print(f"  Backup created: {backup_path}")

    # Also check for JSON companion
    json_path = backup_path.with_suffix(".json")
    if json_path.exists():
        print(f"  JSON dump:      {json_path}")


def handle_restore_backup(db_path: Path):
    """Restore from a previous backup."""
    backups = core.list_backups()
    if not backups:
        print("  No backups found.")
        return

    print("  Available backups:")
    for i, b in enumerate(backups, 1):
        size_kb = b.stat().st_size / 1024
        mtime = b.stat().st_mtime
        from datetime import datetime
        ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {i}. {b.name}  ({size_kb:.1f} KB, {ts})")

    choice = input(f"  Select backup (1-{len(backups)}, or 0 to cancel): ").strip()
    try:
        idx = int(choice)
    except ValueError:
        print("  Cancelled.")
        return

    if idx == 0 or idx < 1 or idx > len(backups):
        print("  Cancelled.")
        return

    selected = backups[idx - 1]
    print(f"  Restoring: {selected.name}")

    if core.is_lghub_running():
        print("  LGHUB must be stopped to restore.")
        stop_confirm = input("  Stop LGHUB? (Y/n): ").strip().lower()
        if stop_confirm == "n":
            print("  Cancelled.")
            return
        print("  Stopping LGHUB...")
        if not core.stop_lghub():
            print("  Error: Failed to stop LGHUB.")
            return

    try:
        core.restore_backup(selected, db_path)
        print("  Restored successfully!")
    except Exception as e:
        print(f"  Error: {e}")
        return

    restart = input("  Start LGHUB? (Y/n): ").strip().lower()
    if restart != "n":
        print("  Starting LGHUB...")
        if core.start_lghub():
            print("  LGHUB started.")
        else:
            print("  Could not start LGHUB. Please start it manually.")


def handle_list_backups():
    """List all available backups."""
    backups = core.list_backups()
    if not backups:
        print("  No backups found.")
        print(f"  Backup directory: {core.get_backup_dir()}")
        return

    print(f"  Backup directory: {core.get_backup_dir()}")
    print()
    for b in backups:
        size_kb = b.stat().st_size / 1024
        from datetime import datetime
        ts = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        json_exists = "+" if b.with_suffix(".json").exists() else " "
        print(f"  {b.name}  {size_kb:>7.1f} KB  {ts}  [JSON{json_exists}]")


def main():
    # Find LGHUB database
    try:
        db_path = core.find_lghub_settings_db()
    except core.LGHUBNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    while True:
        print()
        print("=" * 50)
        print("  LGHUB Roblox Path Manager")
        print("=" * 50)
        show_status(db_path)

        print("  1. Auto-update Roblox path (detect running or newest)")
        print("  2. Manually set Roblox path")
        print("  3. View current LGHUB Roblox config")
        print("  4. Create backup")
        print("  5. Restore from backup")
        print("  6. List backups")
        print("  7. Exit")
        print()

        choice = input("  Choice (1-7): ").strip()
        print()

        if choice == "1":
            handle_auto_update(db_path)
        elif choice == "2":
            handle_manual_path(db_path)
        elif choice == "3":
            handle_view_config(db_path)
        elif choice == "4":
            handle_create_backup(db_path)
        elif choice == "5":
            handle_restore_backup(db_path)
        elif choice == "6":
            handle_list_backups()
        elif choice == "7":
            print("  Goodbye!")
            break
        else:
            print("  Invalid choice.")


if __name__ == "__main__":
    main()
