import numpy as np
import pytest

from stampan.core.philatelic_colours import (
    PhilatelicColourEntry,
    load_philatelic_colours,
    name_philatelic_colour,
)


@pytest.fixture(scope="module")
def table():
    return load_philatelic_colours()


@pytest.fixture
def isolated_table():
    # A single far-apart entry, so nearest-match is unambiguous and the
    # modifier logic (not the real table's particular density/spacing) is
    # what's actually under test.
    return [PhilatelicColourEntry(name="testred", family="red", lab=(40.0, 45.0, 25.0))]


def test_table_loads_expected_entries(table):
    assert len(table) > 20
    names = {e.name for e in table}
    assert "carmine" in names
    assert "ultramarine" in names
    assert "cobalt blue" in names


def test_exact_match_has_near_zero_delta_e(table):
    carmine = next(e for e in table if e.name == "carmine")
    result = name_philatelic_colour(np.array(carmine.lab), table)
    assert result.base_name == "carmine"
    assert result.delta_e00 < 1.0
    assert result.confident
    assert result.full_name == "carmine"  # no modifier needed for an exact match


def test_lighter_version_gets_a_light_modifier(isolated_table):
    anchor = isolated_table[0]
    lighter = (anchor.lab[0] + 25, anchor.lab[1], anchor.lab[2])
    result = name_philatelic_colour(np.array(lighter), isolated_table)
    assert result.base_name == "testred"
    assert any(m in result.full_name for m in ("light", "pale"))


def test_darker_version_gets_a_dark_modifier(isolated_table):
    anchor = isolated_table[0]
    darker = (max(anchor.lab[0] - 20, 1.0), anchor.lab[1], anchor.lab[2])
    result = name_philatelic_colour(np.array(darker), isolated_table)
    assert result.base_name == "testred"
    assert any(m in result.full_name for m in ("dark", "deep"))


def test_desaturated_version_gets_grayish_modifier(isolated_table):
    anchor = isolated_table[0]
    desaturated = (anchor.lab[0], anchor.lab[1] * 0.2, anchor.lab[2] * 0.2)
    result = name_philatelic_colour(np.array(desaturated), isolated_table)
    assert "grayish" in result.full_name


def test_neutral_entries_never_get_a_modifier(table):
    grey = next(e for e in table if e.name == "grey")
    result = name_philatelic_colour(np.array(grey.lab), table)
    assert result.full_name == "grey"


def test_far_outlier_is_flagged_not_confident(table):
    # An extreme, implausible Lab shouldn't silently claim a confident match.
    result = name_philatelic_colour(np.array([50.0, 127.0, -127.0]), table)
    assert not result.confident
    assert result.warning is not None
