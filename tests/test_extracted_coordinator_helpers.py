"""Characterization tests for coordinator helper extraction modules."""

from custom_components.jackery_solarvault.models.property_merge import (
    merge_dict_values,
    sync_property_aliases,
)
from custom_components.jackery_solarvault.stats.backfill import (
    apply_year_month_backfill,
)
from custom_components.jackery_solarvault.stats.validators import verify_and_backfill


def test_property_merge_preserves_nested_base_keys() -> None:
    """Nested merge keeps existing keys and overlays only incoming values."""
    assert merge_dict_values(
        {"properties": {"soc": 50, "inPw": 100}, "name": "base"},
        {"properties": {"soc": 51}},
    ) == {"properties": {"soc": 51, "inPw": 100}, "name": "base"}


def test_property_alias_sync_mirrors_non_null_alias() -> None:
    """Alias synchronization mirrors a present value into its missing alias."""
    assert sync_property_aliases({"soc": 80}, (("soc", "batterySoc"),)) == {
        "soc": 80,
        "batterySoc": 80,
    }


def test_stats_validator_rejects_unconfirmed_zero_cloud_value() -> None:
    """The extracted validator keeps the cloud-zero/local-positive guard."""
    expected = 2.5
    assert verify_and_backfill(0, expected, label="today_energy.de") == expected


def test_stats_backfill_module_exposes_year_month_backfill() -> None:
    """The extracted backfill module exposes the existing year-month helper."""
    assert callable(apply_year_month_backfill)
