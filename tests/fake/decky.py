"""Stand-in for Decky's `decky` module so the backend can run on a dev machine."""
import logging
import os

_ROOT = os.environ.get("DECK_MENTOR_TEST_ROOT", "/tmp/deck-mentor-test")
DECKY_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DECKY_PLUGIN_SETTINGS_DIR = os.path.join(_ROOT, "settings")
DECKY_PLUGIN_RUNTIME_DIR = os.path.join(_ROOT, "data")
DECKY_PLUGIN_LOG_DIR = os.path.join(_ROOT, "logs")
DECKY_USER_HOME = os.path.expanduser("~")
for _d in (DECKY_PLUGIN_SETTINGS_DIR, DECKY_PLUGIN_RUNTIME_DIR, DECKY_PLUGIN_LOG_DIR):
    os.makedirs(_d, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("deck-mentor")
events = []


async def emit(event, *args):
    events.append((event, args))
