"""Shared live-first update flow for both entry points."""
import sqlite3
import time
import core
import agent_api


def sync_path(db_path, new_path):
    new_path = str(new_path).replace("/", "\\")
    backup = None
    def prepare():
        nonlocal backup
        backup = core.create_backup(db_path)
    try:
        result = agent_api.live_update_roblox(new_path, before_write=prepare)
    except agent_api.AgentUnavailable:
        if core.is_lghub_running():
            raise core.LGHUBError("Agent is running but unavailable. Retry later; no offline write performed.")
        settings = core.read_settings_json(db_path)
        app = core.get_current_roblox_app(settings)
        if app is None:
            raise core.LGHUBError("Add Roblox in G HUB first.")
        app_id = app["applicationId"]
        if core.exact_user_paths(app, app_id, new_path):
            return {"changed": False, "mode": "offline", "backup": None, "persisted": True}
        prepare()
        core.update_offline(db_path, app_id, new_path)
        app = core.select_roblox_app(core.read_settings_json(db_path)["applications"]["applications"], app_id)
        if not core.exact_user_paths(app, app_id, new_path):
            raise core.LGHUBError("Offline readback did not match the requested paths.")
        return {"changed": True, "mode": "offline", "backup": backup, "persisted": True}
    result.update(mode="live", backup=backup, persisted=False)
    if not result["changed"]:
        return result
    for attempt in range(10):
        try:
            settings = core.read_settings_json(db_path)
            app = core.select_roblox_app(settings.get("applications", {}).get("applications", []), result["applicationId"])
            if core.exact_user_paths(app, result["applicationId"], new_path):
                result["persisted"] = True
                break
        except (OSError, ValueError, core.LGHUBError, sqlite3.Error):
            pass
        if attempt < 9:
            time.sleep(1)
    return result


def describe(result):
    if not result["changed"]:
        return "Already up to date."
    status = "persisted and verified" if result["persisted"] else "accepted; database persistence unconfirmed"
    return f"Updated ({result['mode']}): {status}. Backup: {result['backup']}"
