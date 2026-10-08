"""Exercise HA schema boundaries with the minimum and newer validation engines."""

from typing import TYPE_CHECKING, Any

import pytest
import voluptuous as vol

from custom_components.jackery_solarvault import services
from custom_components.jackery_solarvault.config_flow import USER_SCHEMA
from custom_components.jackery_solarvault.const import DOMAIN
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall


def test_user_schema_validates_credentials_and_keeps_defaults() -> None:
    """The form keeps credential validation and its optional default values."""
    values = {CONF_USERNAME: "owner@example.com", CONF_PASSWORD: "secret"}
    validated = USER_SCHEMA(values)

    assert validated.items() >= values.items()
    assert len(validated) > len(values)
    with pytest.raises(vol.Invalid):
        USER_SCHEMA({CONF_USERNAME: "owner@example.com"})
    with pytest.raises(vol.Invalid):
        USER_SCHEMA({**values, "unexpected": True})


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize(
    "extra", [vol.PREVENT_EXTRA, vol.ALLOW_EXTRA, vol.REMOVE_EXTRA]
)
async def test_service_schema_preserves_validation_policies(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    required: bool,
    extra: int,
) -> None:
    """Real HA dispatch retains required keys, defaults, validators and extras."""
    calls: list[dict[str, Any]] = []

    async def record_call(  # ruff: ignore[unused-async]  # Service handlers are async.
        _hass: HomeAssistant, call: ServiceCall
    ) -> None:
        """Record the input after the service registry validates it."""
        calls.append(dict(call.data))

    schema = vol.Schema(
        {
            "label": str,
            vol.Optional("mode", default="auto"): vol.In(["auto", "manual"]),
        },
        required=required,
        extra=extra,
    )
    row = services._ServiceRegistration(  # ruff: ignore[private-member-access]
        "schema_compatibility", record_call, schema
    )
    monkeypatch.setattr(services, "_service_registrations", lambda: (row,))
    await services.async_setup_services(hass)

    await hass.services.async_call(DOMAIN, row.name, {"label": "test"}, blocking=True)
    assert calls.pop() == {"label": "test", "mode": "auto"}

    if required:
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(DOMAIN, row.name, {}, blocking=True)
    else:
        await hass.services.async_call(DOMAIN, row.name, {}, blocking=True)
        assert calls.pop() == {"mode": "auto"}

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, row.name, {"label": "test", "mode": "invalid"}, blocking=True
        )

    input_data = {"label": "test", "unexpected": "value"}
    if extra == vol.PREVENT_EXTRA:
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(DOMAIN, row.name, input_data, blocking=True)
    else:
        await hass.services.async_call(DOMAIN, row.name, input_data, blocking=True)
        validated = calls.pop()
        assert ("unexpected" in validated) is (extra == vol.ALLOW_EXTRA)
    assert not calls
