import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


def test_no_session_offers_or_schedules_a_game_updater():
    names = {d["name"] for d in main.declared_tools(labs=True)}
    assert "game_updater" not in names
    assert "game_updater" not in main.TOOL_SPECS
    assert "game_updater" not in main.LABS_TOOLS
