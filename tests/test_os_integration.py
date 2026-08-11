import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from dariusai import os_integration

# A disposable scratch key — never the real Run key. Cleaned up after every test.
SCRATCH_KEY = r"Software\DariusAI-Test-Scratch\Run"
SCRATCH_VALUE = "DariusAITestEntry"


@pytest.fixture(autouse=True)
def cleanup_scratch_key():
    yield
    os_integration.delete_key_for_test(SCRATCH_KEY)
    os_integration.delete_key_for_test(r"Software\DariusAI-Test-Scratch")  # empty parent left behind


def test_is_enabled_false_when_key_does_not_exist():
    assert os_integration.is_enabled(SCRATCH_VALUE, SCRATCH_KEY) is False


def test_set_enabled_true_creates_the_value():
    result = os_integration.set_enabled(True, SCRATCH_VALUE, SCRATCH_KEY)
    assert result is True
    assert os_integration.is_enabled(SCRATCH_VALUE, SCRATCH_KEY) is True


def test_set_enabled_false_removes_the_value():
    os_integration.set_enabled(True, SCRATCH_VALUE, SCRATCH_KEY)
    result = os_integration.set_enabled(False, SCRATCH_VALUE, SCRATCH_KEY)
    assert result is False
    assert os_integration.is_enabled(SCRATCH_VALUE, SCRATCH_KEY) is False


def test_set_enabled_false_when_never_created_does_not_raise():
    result = os_integration.set_enabled(False, SCRATCH_VALUE, SCRATCH_KEY)
    assert result is False


def test_launch_command_points_at_the_real_vbs_launcher():
    cmd = os_integration._launch_command()
    assert "DariusAI.vbs" in cmd
    assert "wscript.exe" in cmd


def test_scratch_value_name_never_leaks_into_the_real_run_key():
    """Confirms this test suite only ever touched the scratch key, not the
    real Run key — the scratch value name must not exist there."""
    assert os_integration.is_enabled(SCRATCH_VALUE) is False
