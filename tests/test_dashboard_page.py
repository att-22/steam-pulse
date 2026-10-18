"""The whole page, run headless on a warehouse built by the models.

Needs STEAM_PULSE_DATABASE pointing at a warehouse built by `dbt build`, so it only runs
where there is one: in CI, in the job that builds the models.
"""

import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).parent.parent / "dashboard" / "app.py"
DATABASE = os.environ.get("STEAM_PULSE_DATABASE")

pytestmark = pytest.mark.skipif(
    not DATABASE or not Path(DATABASE).exists(),
    reason="needs STEAM_PULSE_DATABASE pointing at a built warehouse",
)


def test_the_page_renders_every_section_without_errors():
    page = AppTest.from_file(str(APP), default_timeout=60).run()

    assert not page.exception
    headers = [element.value for element in page.header] + [
        element.value for element in page.subheader
    ]
    for section in [
        "Most played right now",
        "Players over time",
        "Steam as a whole",
        "When Steam plays",
        "In and out of the top 100",
    ]:
        assert section in headers
    assert len(page.metric) == 4
    assert "Not affiliated with Valve" in page.caption[-1].value
