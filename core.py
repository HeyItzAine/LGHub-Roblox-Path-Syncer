"""
LGHUB Roblox Path Updater - Shared Core Module

Provides all shared logic for finding paths, reading/writing the LGHUB
settings database, backup/restore, and LGHUB process management.
"""

import ntpath
from contextlib import closing
import sqlite3
import json
import shutil
import subprocess
import os
import uuid
import time
import hashlib
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROBLOX_EXE_NAME = "RobloxPlayerBeta.exe"
LGHUB_PROCESSES = ["lghub.exe", "lghub_agent.exe", "lghub_system_tray.exe"]
BACKUP_DIR_NAME = "backups"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LGHUBError(Exception):
    """Base exception for LGHUB operations."""

class LGHUBNotFoundError(LGHUBError):
    """LGHUB settings database not found."""

class RobloxNotFoundError(LGHUBError):
    """RobloxPlayerBeta.exe not found."""

class DatabaseLockedError(LGHUBError):
    """LGHUB database is locked (LGHUB is running)."""


# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------

def _local_appdata() -> Path:
    """Return the LOCALAPPDATA directory."""
    val = os.environ.get("LOCALAPPDATA")
    if val:
        return Path(val)
    return Path.home() / "AppData" / "Local"


def find_lghub_settings_db() -> Path:
    """Locate the LGHUB settings.db file."""
    db = _local_appdata() / "LGHUB" / "settings.db"
    if db.exists():
        return db
    raise LGHUBNotFoundError(
        f"LGHUB settings.db not found at {db}\n"
        "Make sure Logitech G HUB is installed."
    )


def find_lghub_exe() -> Path:
    """Locate the LGHUB main executable for restarting."""
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "LGHUB" / "lghub.exe",
        Path("C:/Program Files/LGHUB/lghub.exe"),
    ]
    for p in candidates:
        if p.exists():
            return p
    raise LGHUBNotFoundError("lghub.exe not found in Program Files.")


def _running_roblox_player() -> Path | None:
    """Use the running player's path; prefer the foreground player."""
    if os.name != "nt":
        return None
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name='RobloxPlayerBeta.exe'\" "
        "| Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        processes = json.loads(result.stdout)
        if isinstance(processes, dict):
            processes = [processes]
        paths = {}
        for process in processes:
            value = process.get("ExecutablePath")
            if value:
                path = Path(value)
                if path.is_file() and path.name.lower() == ROBLOX_EXE_NAME.lower():
                    paths[int(process["ProcessId"])] = path
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return None

    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    foreground_pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(foreground_pid))
    if foreground_pid.value in paths:
        return paths[foreground_pid.value]
    unique_paths = set(paths.values())
    if len(unique_paths) == 1:
        return unique_paths.pop()
    if len(unique_paths) > 1:
        raise RobloxNotFoundError(
            "Multiple Roblox versions are running. Focus the Roblox window "
            "you want to link, then run the updater again."
        )
    return None


def find_roblox_player() -> Path:
    """Find the running Roblox player, or the newest installed executable."""
    running = _running_roblox_player()
    if running is not None:
        return running
    versions_dir = _local_appdata() / "Roblox" / "Versions"
    if not versions_dir.exists():
        raise RobloxNotFoundError(
            f"Roblox Versions directory not found at {versions_dir}\n"
            "Make sure Roblox is installed."
        )

    candidates = []
    for entry in versions_dir.iterdir():
        if entry.is_dir() and entry.name.startswith("version-"):
            exe = entry / ROBLOX_EXE_NAME
            if exe.exists():
                candidates.append(exe)

    if not candidates:
        raise RobloxNotFoundError(
            f"No {ROBLOX_EXE_NAME} found in any version folder under {versions_dir}"
        )

    # Pick the one with the newest modification time
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def get_backup_dir() -> Path:
    """Return the backup directory path, creating it if needed."""
    d = _local_appdata() / "LGHUB" / BACKUP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Database read / write
# ---------------------------------------------------------------------------

def read_settings_raw(db_path: Path) -> bytes:
    """Read one consistent SQLite snapshot without copying live WAL files."""
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
        row = conn.execute("SELECT file FROM data WHERE _id = 1").fetchone()
    if row is None:
        raise LGHUBError("No settings row found.")
    return row[0] if isinstance(row[0], bytes) else row[0].encode("utf-8")


def read_settings_json(db_path: Path) -> dict:
    return json.loads(read_settings_raw(db_path))


def write_settings_json(db_path: Path, data: dict) -> None:
    """
    Write the JSON blob back to the data table in settings.db.
    LGHUB MUST be stopped before calling this.
    """
    if is_lghub_running():
        raise DatabaseLockedError(
            "LGHUB is still running. Stop it before writing to the database."
        )
    blob = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE data SET file = ? WHERE _id = 1", (blob,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def create_backup(db_path: Path, backup_dir: Path | None = None) -> Path:
    """
    Create a consistent SQLite snapshot and a JSON dump of that snapshot. Returns the path to the backup .db file.
    """
    if backup_dir is None:
        backup_dir = get_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_db = backup_dir / f"settings_{ts}_{uuid.uuid4().hex[:8]}.db"
    deadline = time.monotonic() + 15
    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise LGHUBError("Timed out creating a consistent backup.")
    try:
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
            with closing(sqlite3.connect(backup_db)) as target:
                source.backup(target, pages=256, progress=progress, sleep=0.05)
        settings = read_settings_json(backup_db)
        backup_db.with_suffix(".json").write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        backup_db.unlink(missing_ok=True)
        backup_db.with_suffix(".json").unlink(missing_ok=True)
        raise
    return backup_db


def restore_backup(backup_db: Path, target_db: Path) -> None:
    """Restore a backup over the current settings.db. LGHUB must be stopped."""
    if is_lghub_running():
        raise DatabaseLockedError(
            "LGHUB is still running. Stop it before restoring a backup."
        )
    if not backup_db.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_db}")

    shutil.copy2(backup_db, target_db)
    for ext in ("-wal", "-shm"):
        backup_companion = backup_db.parent / (backup_db.name + ext)
        target_companion = target_db.parent / (target_db.name + ext)
        if backup_companion.exists():
            shutil.copy2(backup_companion, target_companion)
        elif target_companion.exists():
            # Remove stale WAL/SHM if backup didn't have them
            target_companion.unlink()


def list_backups(backup_dir: Path | None = None) -> list[Path]:
    """List available backup .db files sorted newest first."""
    if backup_dir is None:
        backup_dir = get_backup_dir()
    if not backup_dir.exists():
        return []
    backups = sorted(
        backup_dir.glob("settings_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Filter out WAL/SHM files
    return [b for b in backups if not b.name.endswith(("-wal", "-shm"))]


# ---------------------------------------------------------------------------
# LGHUB process management
# ---------------------------------------------------------------------------

def is_lghub_running() -> bool:
    """Check if any LGHUB process is running."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq lghub_agent.exe"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            raise LGHUBError("Unable to determine G HUB process state.")
        return "lghub_agent.exe" in result.stdout.lower()
    except Exception as e:
        raise LGHUBError("Unable to determine G HUB process state.") from e


def stop_lghub(timeout: int = 10) -> bool:
    """Stop all LGHUB processes. Returns True if all stopped."""
    for proc in LGHUB_PROCESSES:
        subprocess.run(
            ["taskkill", "/F", "/IM", proc],
            capture_output=True, text=True, timeout=10
        )

    # Wait for processes to exit
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_lghub_running():
            return True
        time.sleep(0.5)
    return not is_lghub_running()


def _spawn_detached(exe: Path) -> None:
    """Launch a process fully detached: no inherited console/stdio, so its
    log spam (e.g. lghub.exe's websocket retry errors) never reaches us."""
    subprocess.Popen(
        [str(exe)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def start_lghub(timeout: int = 20) -> bool:
    """Start LGHUB (agent first, then UI). Returns True once the agent is up.

    lghub.exe is only the Electron UI - it connects to lghub_agent.exe on
    port 9010. Starting the UI alone makes it spam ECONNREFUSED retries until
    the agent appears, so start the agent explicitly and wait for it.
    """
    try:
        exe = find_lghub_exe()
        agent = exe.parent / "lghub_agent.exe"
        if agent.exists() and not is_lghub_running():
            _spawn_detached(agent)
            deadline = time.time() + timeout
            while time.time() < deadline and not is_lghub_running():
                time.sleep(0.5)
        _spawn_detached(exe)
        time.sleep(2)
        return is_lghub_running()
    except Exception as e:
        print(f"Failed to start LGHUB: {e}")
        return False


# ---------------------------------------------------------------------------
# Roblox config in LGHUB settings
# ---------------------------------------------------------------------------

def get_current_roblox_app(settings: dict) -> dict | None:
    """Find the Roblox application entry in the settings JSON."""
    return select_roblox_app(settings.get("applications", {}).get("applications", []))


def select_roblox_app(apps, application_id=None):
    if application_id is not None:
        matches = [a for a in apps if a.get("applicationId") == application_id]
    else:
        matches = [a for a in apps if any(
            ntpath.basename(p).lower() == ROBLOX_EXE_NAME.lower()
            for p in [a.get("applicationPath", "")] + a.get("userPaths", [])
        )]
        if not matches:
            matches = [a for a in apps if a.get("name", "").lower() in ("roblox", "robloxplayerbeta")]
    if len(matches) > 1:
        raise LGHUBError("Multiple Roblox application entries match; resolve duplicates in G HUB.")
    return matches[0] if matches else None


def exact_user_paths(app, application_id, new_path):
    return bool(app and app.get("applicationId") == application_id
                and len(app.get("userPaths", [])) == 1
                and _same_path(app["userPaths"][0], new_path))


def update_offline(db_path, application_id, new_path):
    """Reread and patch only the selected app under one write transaction."""
    if is_lghub_running():
        raise DatabaseLockedError("G HUB must be stopped for offline updates.")
    with closing(sqlite3.connect(db_path, timeout=5)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT file FROM data WHERE _id=1").fetchone()
            if row is None:
                raise LGHUBError("No settings row found.")
            settings = json.loads(row[0])
            app = select_roblox_app(settings.get("applications", {}).get("applications", []), application_id)
            if app is None:
                raise LGHUBError("Selected application no longer exists.")
            app["applicationPath"] = new_path
            app["userPaths"] = [new_path]
            conn.execute("UPDATE data SET file=? WHERE _id=1", (json.dumps(settings).encode("utf-8"),))


def get_roblox_profile(settings: dict, app_id: str) -> dict | None:
    """Find the profile associated with a Roblox application ID."""
    profiles = settings.get("profiles", {}).get("profiles", [])
    for profile in profiles:
        if profile.get("applicationId") == app_id:
            return profile
    return None


def _same_path(a: str, b: str) -> bool:
    return ntpath.normcase(ntpath.normpath(a)) == ntpath.normcase(ntpath.normpath(b))


def desired_user_paths(existing_user_paths: list[str], new_exe_path: str) -> list[str]:
    """Replace the linked-executable list outright: only the newest exe.
    userPaths is what LGHUB's process detection matches against (in addition
    to applicationPath), so keeping it current keeps profiles auto-switching."""
    return [str(new_exe_path)]


def roblox_path_effective(app: dict, exe_path: str) -> bool:
    """True if the exe is already covered by applicationPath or userPaths."""
    if _same_path(app.get("applicationPath", ""), exe_path):
        return True
    return any(_same_path(p, exe_path) for p in app.get("userPaths", []))


def update_roblox_path(settings: dict, new_exe_path: str) -> dict:
    """
    Update or create the Roblox application entry with the new exe path.
    Returns the modified settings dict.
    """
    # Normalize to backslashes
    new_exe_path = str(new_exe_path).replace("/", "\\")

    existing = get_current_roblox_app(settings)

    if existing:
        # Update existing entry - path plus linked-executable list
        existing["applicationPath"] = new_exe_path
        existing["userPaths"] = desired_user_paths(existing.get("userPaths", []), new_exe_path)
    else:
        # Create new entry
        app_id = str(uuid.uuid4())
        new_app = {
            "applicationId": app_id,
            "applicationPath": new_exe_path,
            "isCustom": True,
            "name": "Roblox",
        }

        if "applications" not in settings:
            settings["applications"] = {}
        if "applications" not in settings["applications"]:
            settings["applications"]["applications"] = []
        settings["applications"]["applications"].append(new_app)

        # Create a default profile for the new app
        new_profile = {
            "activeForApplication": True,
            "applicationId": app_id,
            "id": app_id,
            "name": "PROFILE_NAME_DEFAULT",
            "assignments": [],
        }
        if "profiles" not in settings:
            settings["profiles"] = {}
        if "profiles" not in settings["profiles"]:
            settings["profiles"]["profiles"] = []
        settings["profiles"]["profiles"].append(new_profile)

    return settings


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def file_hash(path: Path) -> str | None:
    """Return SHA-256 hex digest of a file, or None if it doesn't exist."""
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
