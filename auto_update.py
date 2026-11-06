"""Update the Roblox link using live agent state, with a stopped-agent fallback."""
import sys
import core
import sync_service


def main():
    try:
        result = sync_service.sync_path(core.find_lghub_settings_db(), core.find_roblox_player())
        print(sync_service.describe(result))
        return 0 if result["changed"] else 2
    except Exception as e:
        print(f"Update failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
