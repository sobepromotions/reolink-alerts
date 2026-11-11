"""Shared test setup.

The tests read tests/test_settings.toml and write only to a temporary folder,
so running them never reads or changes your own settings.toml, state.json or log.
"""
import tempfile
from pathlib import Path

import reolink_alerts as ra

FIXTURE = Path(__file__).with_name("test_settings.toml")
TEMP = Path(tempfile.mkdtemp(prefix="reolink_alerts_tests_"))

ra.SETTINGS_PATH = FIXTURE
ra.load_settings.__defaults__ = (FIXTURE,)
ra.STATE_PATH = TEMP / "state.json"
ra.LOG_PATH = TEMP / "reolink_alerts.log"
ra.INPUT_CONF_PATH = TEMP / "popup_input.conf"
