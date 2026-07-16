"""
LGHUB Roblox Path Auto-Updater - Automatic Mode

Finds the latest RobloxPlayerBeta.exe and updates LGHUB's configuration
automatically. Creates a backup before making any changes.

Two update strategies:
  1. LIVE (preferred): talk to the lghub_agent websocket API on port 9010 and
     link the new exe via userPaths - no LGHUB stop/restart needed. The agent
     persists the change to settings.db itself within ~1s.
  2. OFFLINE (fallback): if the agent isn't reachable, write settings.db
     directly (stopping LGHUB first if it's somehow running without an agent).
"""

import sys
import time

import agent_api
import core


def _print_summary(old_path: str, new_path: str, backup_path, mode: str):
    print()
    print("=" * 50)
    print("  Done!")
    print(f"  Mode:     {mode}")
    print(f"  Old path: {old_path}")
    print(f"  New path: {new_path}")
    print(f"  Backup:   {backup_path}")
    print("=" * 50)


def main():
    print("=" * 50)
    print("  LGHUB Roblox Path Auto-Updater")
    print("=" * 50)
    print()

    # Step 1: Find LGHUB settings database
    try:
        db_path = core.find_lghub_settings_db()
        print(f"LGHUB settings: {db_path}")
    except core.LGHUBNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Step 2: Find latest Roblox player
    try:
        roblox_exe = core.find_roblox_player()
        print(f"Latest Roblox:  {roblox_exe}")
    except core.RobloxNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    new_path = str(roblox_exe).replace("/", "\\")

    # Step 3: Read current config (copy-based read works while LGHUB runs)
    try:
        settings = core.read_settings_json(db_path)
    except Exception as e:
        print(f"Error reading LGHUB config: {e}")
        sys.exit(1)

    current = core.get_current_roblox_app(settings)
    current_path = current.get("applicationPath", "Not configured") if current else "Not configured"
    current_user_paths = current.get("userPaths", []) if current else []

    print()
    print(f"Current path:  {current_path}")
    print(f"Linked paths:  {current_user_paths or '(none)'}")
    print(f"New path:      {new_path}")
    print()

    # Step 4: Check if already up to date (exe covered + linked list clean)
    if current is not None:
        desired = core.desired_user_paths(current_user_paths, new_path)
        userpaths_clean = (
            len(desired) == len(current_user_paths)
            and all(core._same_path(a, b) for a, b in zip(desired, current_user_paths))
        )
        if core.roblox_path_effective(current, new_path) and userpaths_clean:
            print("Already up to date! No changes needed.")
            sys.exit(2)

    # Step 5: Create backup (works while LGHUB runs - copies db + wal + shm)
    print("Creating backup...")
    try:
        backup_path = core.create_backup(db_path)
        print(f"Backup saved: {backup_path}")
    except Exception as e:
        print(f"Error creating backup: {e}")
        sys.exit(1)

    # Step 6: LIVE update via the agent API - no LGHUB restart
    if agent_api.agent_available():
        print()
        print("LGHUB agent is running - updating live (no restart needed)...")
        try:
            result = agent_api.live_update_roblox(new_path)
            print("Linked paths now:", result["userPaths"])
            if result["note"]:
                print(f"Note: {result['note']}")

            # Confirm the agent persisted the change to settings.db
            for _ in range(10):
                time.sleep(1)
                persisted = core.get_current_roblox_app(core.read_settings_json(db_path))
                if persisted and core.roblox_path_effective(persisted, new_path):
                    print("Verified: change persisted to settings.db.")
                    break
            else:
                print("Warning: change not yet visible in settings.db "
                      "(the agent may persist it on its own schedule).")

            _print_summary(current_path, new_path, backup_path, "live (agent API)")
            return
        except agent_api.AgentError as e:
            print(f"Live update failed: {e}")
            print("Falling back to offline update...")

    # Step 7: OFFLINE fallback - direct DB write
    print()
    if core.is_lghub_running():
        print("LGHUB is running without a reachable agent - stopping it to update.")
        if not core.stop_lghub():
            print("Error: Failed to stop LGHUB. Try closing it manually.")
            sys.exit(1)
        print("LGHUB stopped.")
        restart_needed = True
    else:
        print("LGHUB is not running - updating settings.db directly.")
        restart_needed = False

    print("Updating Roblox path...")
    settings = core.update_roblox_path(settings, new_path)
    try:
        core.write_settings_json(db_path, settings)
        print("Config updated successfully!")
    except Exception as e:
        print(f"Error writing config: {e}")
        print(f"You can restore from backup: {backup_path}")
        sys.exit(1)

    if restart_needed:
        print()
        print("Starting LGHUB...")
        if core.start_lghub():
            print("LGHUB started.")
        else:
            print("Could not start LGHUB automatically. Please start it manually.")

    _print_summary(current_path, new_path, backup_path, "offline (direct DB write)")


if __name__ == "__main__":
    main()
