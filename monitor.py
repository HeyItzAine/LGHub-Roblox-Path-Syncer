"""
LGHUB Config Monitor - Phase 1 Discovery Tool

Takes before/after snapshots of LGHUB configuration files to identify
exactly what changes when a path is added or modified in the LGHUB UI.
"""

import json
import difflib
import sys
from pathlib import Path
from datetime import datetime

import core


def snapshot_lghub_state(db_path: Path) -> dict:
    """Capture the current state of all LGHUB config files."""
    lghub_dir = db_path.parent
    roaming_dir = Path(core._local_appdata()).parent / "Roaming" / "LGHUB"

    state = {
        "timestamp": datetime.now().isoformat(),
        "db_json": None,
        "db_raw_hash": None,
        "file_hashes": {},
        "file_sizes": {},
    }

    # Read the JSON blob from the database
    try:
        state["db_json"] = core.read_settings_json(db_path)
    except Exception as e:
        print(f"  Warning: Could not read DB JSON: {e}")

    # Hash the raw blob
    try:
        raw = core.read_settings_raw(db_path)
        state["db_raw_hash"] = core.file_hash.__wrapped__(raw) if hasattr(core.file_hash, '__wrapped__') else None
        import hashlib
        state["db_raw_hash"] = hashlib.sha256(raw).hexdigest()
    except Exception:
        pass

    # Hash all files in the LGHUB local directory
    files_to_track = []
    if lghub_dir.exists():
        for f in lghub_dir.iterdir():
            if f.is_file():
                files_to_track.append(f)
    if roaming_dir.exists():
        for f in roaming_dir.iterdir():
            if f.is_file():
                files_to_track.append(f)

    for f in files_to_track:
        rel = str(f)
        state["file_hashes"][rel] = core.file_hash(f)
        state["file_sizes"][rel] = f.stat().st_size

    return state


def compare_snapshots(before: dict, after: dict) -> str:
    """Compare two snapshots and return a human-readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append("LGHUB CONFIG CHANGE REPORT")
    lines.append(f"Before: {before['timestamp']}")
    lines.append(f"After:  {after['timestamp']}")
    lines.append("=" * 70)

    # --- File-level changes ---
    lines.append("\n--- File Changes ---")
    all_files = set(before["file_hashes"].keys()) | set(after["file_hashes"].keys())
    changed_files = []
    for f in sorted(all_files):
        h_before = before["file_hashes"].get(f)
        h_after = after["file_hashes"].get(f)
        s_before = before["file_sizes"].get(f, 0)
        s_after = after["file_sizes"].get(f, 0)
        if h_before != h_after:
            if h_before is None:
                lines.append(f"  NEW:     {f} ({s_after} bytes)")
            elif h_after is None:
                lines.append(f"  DELETED: {f}")
            else:
                delta = s_after - s_before
                sign = "+" if delta >= 0 else ""
                lines.append(f"  CHANGED: {f} ({s_before} -> {s_after} bytes, {sign}{delta})")
            changed_files.append(f)

    if not changed_files:
        lines.append("  No file changes detected.")

    # --- DB blob hash ---
    lines.append("\n--- Database Blob ---")
    if before["db_raw_hash"] == after["db_raw_hash"]:
        lines.append("  Blob hash unchanged.")
    else:
        lines.append(f"  Before hash: {before['db_raw_hash']}")
        lines.append(f"  After hash:  {after['db_raw_hash']}")

    # --- JSON diff ---
    if before["db_json"] and after["db_json"]:
        before_text = json.dumps(before["db_json"], indent=2, ensure_ascii=False)
        after_text = json.dumps(after["db_json"], indent=2, ensure_ascii=False)

        if before_text == after_text:
            lines.append("\n--- JSON Content ---")
            lines.append("  No JSON changes detected.")
        else:
            lines.append("\n--- JSON Diff (unified) ---")
            diff = difflib.unified_diff(
                before_text.splitlines(),
                after_text.splitlines(),
                fromfile="before",
                tofile="after",
                lineterm="",
            )
            diff_lines = list(diff)
            if len(diff_lines) > 200:
                lines.extend(diff_lines[:200])
                lines.append(f"  ... ({len(diff_lines) - 200} more lines)")
            else:
                lines.extend(diff_lines)

        # --- Roblox-specific check ---
        lines.append("\n--- Roblox Entry ---")
        roblox_before = core.get_current_roblox_app(before["db_json"])
        roblox_after = core.get_current_roblox_app(after["db_json"])

        if roblox_before:
            lines.append(f"  Before: {json.dumps(roblox_before, indent=4)}")
        else:
            lines.append("  Before: Not found")

        if roblox_after:
            lines.append(f"  After:  {json.dumps(roblox_after, indent=4)}")
        else:
            lines.append("  After:  Not found")

    return "\n".join(lines)


def save_snapshot_json(state: dict, filename: str):
    """Save the JSON blob from a snapshot to a file for manual inspection."""
    if state["db_json"]:
        Path(filename).write_text(
            json.dumps(state["db_json"], indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"  Saved: {filename}")


def main():
    print("=" * 50)
    print("  LGHUB Config Monitor")
    print("=" * 50)
    print()

    # Find the database
    try:
        db_path = core.find_lghub_settings_db()
        print(f"Found settings.db: {db_path}")
    except core.LGHUBNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Check current Roblox status
    try:
        settings = core.read_settings_json(db_path)
        roblox = core.get_current_roblox_app(settings)
        if roblox:
            print(f"Current Roblox entry: {roblox.get('applicationPath', 'N/A')}")
        else:
            print("No Roblox entry found in LGHUB config.")
    except Exception as e:
        print(f"Warning: Could not read current config: {e}")

    print()
    print("Taking BEFORE snapshot...")
    before = snapshot_lghub_state(db_path)
    save_snapshot_json(before, "before.json")
    print(f"  Tracked {len(before['file_hashes'])} files")
    print()

    print("-" * 50)
    print("Now make your changes in LGHUB.")
    print("(Add/remove a path, change a profile, etc.)")
    print("-" * 50)
    input("Press ENTER when done... ")
    print()

    print("Taking AFTER snapshot...")
    after = snapshot_lghub_state(db_path)
    save_snapshot_json(after, "after.json")
    print(f"  Tracked {len(after['file_hashes'])} files")
    print()

    # Compare and report
    report = compare_snapshots(before, after)
    print(report)

    # Save report to file
    report_file = f"monitor_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    Path(report_file).write_text(report, encoding="utf-8")
    print(f"\nReport saved to: {report_file}")

    # Ask if user wants to do another round
    print()
    again = input("Take another snapshot? (y/N): ").strip().lower()
    if again == "y":
        print("\nMake your next change in LGHUB.")
        input("Press ENTER when done... ")
        print()
        print("Taking new snapshot...")
        after2 = snapshot_lghub_state(db_path)
        save_snapshot_json(after2, "after2.json")
        report2 = compare_snapshots(after, after2)
        print(report2)
        report_file2 = f"monitor_report2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        Path(report_file2).write_text(report2, encoding="utf-8")
        print(f"\nReport saved to: {report_file2}")


if __name__ == "__main__":
    main()
