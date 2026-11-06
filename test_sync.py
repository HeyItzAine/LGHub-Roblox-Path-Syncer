import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import core
import agent_api
import sync_service


class SyncTests(unittest.TestCase):
    def app(self, **changes):
        return dict(applicationId="player", applicationPath=r"C:\Games\RobloxPlayerBeta.exe", userPaths=[], **changes)

    def test_selection_ignores_studio_and_rejects_duplicates(self):
        app = self.app()
        self.assertIs(core.select_roblox_app([{"name": "Roblox Studio"}, app]), app)
        with self.assertRaises(core.LGHUBError):
            core.select_roblox_app([app, dict(app)])

    def test_exact_check_does_not_accept_primary_path(self):
        app = self.app()
        self.assertFalse(core.exact_user_paths(app, "player", app["applicationPath"]))
        app["userPaths"] = [app["applicationPath"].upper()]
        self.assertTrue(core.exact_user_paths(app, "player", app["applicationPath"]))
        self.assertFalse(core.exact_user_paths(app, "other", app["applicationPath"]))

    def test_backup_and_transaction_preserve_other_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "settings.db"
            conn = sqlite3.connect(db)
            conn.execute("PRAGMA journal_mode=wal")
            conn.execute("CREATE TABLE data(_id INTEGER PRIMARY KEY, file BLOB)")
            data = {"applications": {"applications": [self.app()]}, "language": "before"}
            conn.execute("INSERT INTO data VALUES(1,?)", (json.dumps(data),))
            conn.commit()
            backup = core.create_backup(db, Path(temp) / "backups")
            self.assertEqual(core.read_settings_json(backup), json.loads(backup.with_suffix(".json").read_text()))
            data["language"] = "latest"
            conn.execute("UPDATE data SET file=?", (json.dumps(data),))
            conn.commit()
            with patch.object(core, "is_lghub_running", return_value=False):
                core.update_offline(db, "player", r"C:\Games\New\RobloxPlayerBeta.exe")
            self.assertEqual(core.read_settings_json(db)["language"], "latest")
            self.assertEqual(core.read_settings_json(backup)["language"], "before")
            conn.close()

    def test_uncertain_write_reconciles_without_second_set(self):
        app = self.app()
        updated = dict(app, userPaths=["new"])
        connection = MagicMock()
        with patch.object(agent_api, "AgentConnection", return_value=connection), patch.object(agent_api, "find_roblox_app", side_effect=[app, updated]), patch.object(agent_api, "set_user_paths", side_effect=agent_api.AgentUnavailable("lost reply")) as setter, patch.object(agent_api.time, "sleep"):
            result = agent_api.live_update_roblox("new")
        self.assertTrue(result["changed"])
        self.assertEqual(setter.call_count, 1)

    def test_rejection_never_falls_back(self):
        with patch.object(agent_api, "live_update_roblox", side_effect=agent_api.AgentRejected("rejected")), patch.object(core, "update_offline") as writer:
            with self.assertRaises(agent_api.AgentRejected):
                sync_service.sync_path(Path("unused"), "new")
            writer.assert_not_called()

    def test_running_unavailable_agent_never_writes(self):
        with patch.object(agent_api, "live_update_roblox", side_effect=agent_api.AgentUnavailable("offline")), patch.object(core, "is_lghub_running", return_value=True), patch.object(core, "update_offline") as writer:
            with self.assertRaises(core.LGHUBError):
                sync_service.sync_path(Path("unused"), "new")
            writer.assert_not_called()

    def test_noop_does_not_read_database(self):
        with patch.object(agent_api, "live_update_roblox", return_value={"changed": False}), patch.object(core, "read_settings_json") as reader:
            self.assertFalse(sync_service.sync_path(Path("unused"), "new")["changed"])
            reader.assert_not_called()

    def test_uncertain_write_exhaustion_is_not_offline_eligible(self):
        with patch.object(agent_api, "AgentConnection", return_value=MagicMock()), patch.object(agent_api, "find_roblox_app", return_value=self.app()), patch.object(agent_api, "set_user_paths", side_effect=agent_api.AgentUnavailable("lost")), patch.object(agent_api.time, "sleep"):
            with self.assertRaises(agent_api.AgentError) as error:
                agent_api.live_update_roblox("new")
            self.assertNotIsInstance(error.exception, agent_api.AgentUnavailable)

    def test_wrong_readback_does_not_report_success(self):
        app = self.app()
        with patch.object(agent_api, "AgentConnection", return_value=MagicMock()), patch.object(agent_api, "find_roblox_app", return_value=app), patch.object(agent_api, "set_user_paths"):
            with self.assertRaises(agent_api.AgentProtocolError):
                agent_api.live_update_roblox(app["applicationPath"])

    def test_unknown_process_state_blocks_offline(self):
        with patch.object(core.subprocess, "run", side_effect=OSError("unavailable")):
            with self.assertRaises(core.LGHUBError):
                core.is_lghub_running()


if __name__ == "__main__":
    unittest.main()
