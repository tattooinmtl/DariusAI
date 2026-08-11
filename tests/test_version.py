import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import dariusai
from dariusai.cli import build_parser
from dariusai.viz.server import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "version,expected",
    [
        ("0.55.0a0", "alpha 0.55"),
        ("0.55.2a1", "alpha 0.55.2"),
        ("0.55.0b3", "beta 0.55"),
        ("1.0.0rc1", "rc 1.0"),
        ("1.2.0", "1.2"),
        ("1.2.3", "1.2.3"),
        ("not-a-version", "not-a-version"),  # never raises over a version string
    ],
)
def test_version_display(version, expected):
    assert dariusai.version_display(version) == expected


def test_version_display_is_derived_from_the_version():
    """Was `assert __version__ == "0.56.0a0"`, which went stale on every bump
    and had been failing across five of them — a guard nobody could trust.
    What actually matters is that the displayed string tracks the real version,
    so assert that relationship instead of freezing a literal."""
    assert dariusai.VERSION_DISPLAY == dariusai.version_display(dariusai.__version__)
    assert dariusai.__version__.startswith("0.")
    assert dariusai.VERSION_DISPLAY.startswith("alpha ")


def test_pyproject_version_matches_package():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    assert declared and declared.group(1) == dariusai.__version__


def test_api_version_endpoint(tmp_path):
    client = TestClient(create_app(tmp_path / "brain", project_dir=tmp_path))
    body = client.get("/api/version").json()
    assert body["version"] == dariusai.__version__
    assert body["display"] == dariusai.VERSION_DISPLAY
    assert body["page_build"] > 0  # lets an open window notice it's stale


def test_page_is_served_uncacheable(tmp_path):
    """A cached copy of index.html is indistinguishable from a broken app —
    every fix ships inside it."""
    client = TestClient(create_app(tmp_path / "brain", project_dir=tmp_path))
    assert "no-store" in client.get("/").headers.get("cache-control", "")


def test_page_offers_a_reload_when_the_build_moves_on():
    page = (ROOT / "src/dariusai/viz/static/index.html").read_text(encoding="utf-8")
    assert 'id="btnReload"' in page
    assert "page_build" in page


def test_page_reads_its_version_from_the_api():
    page = (ROOT / "src/dariusai/viz/static/index.html").read_text(encoding="utf-8")
    assert 'id="versionBadge"' in page
    assert '"/api/version"' in page
    assert dariusai.__version__ not in page  # never hardcoded in the markup


def test_cli_version_flag_works_without_a_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert dariusai.VERSION_DISPLAY in capsys.readouterr().out
