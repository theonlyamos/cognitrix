from cognitrix.sessions.base import _turn_helper_names
from cognitrix.tools.base import ToolManager


def test_saved_agents_with_window_tools_get_show_screenshot():
    assert _turn_helper_names({'look at window'}) == ('show_screenshot',)
    assert ToolManager.get_by_name('show_screenshot').name == 'Show Screenshot'


def test_shell_helpers_still_come_with_bash():
    assert _turn_helper_names({'bash'}) == ('bash_output', 'kill_shell')
    assert _turn_helper_names({'read'}) == ()
