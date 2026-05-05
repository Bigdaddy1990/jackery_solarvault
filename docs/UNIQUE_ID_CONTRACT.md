# Unique ID contract

Home Assistant unique IDs for Jackery SolarVault entities follow a single
rule: ``<device_id>_<stable_key_suffix>``.

* ``device_id`` is the Jackery cloud device identifier (a stable
  integer-string), captured from the ``/v1/device/system/list`` discovery
  response and never derived from a translatable name.
* ``stable_key_suffix`` is the entity-description ``key`` (e.g.
  ``pv1_jahreswert``, ``home_today_load``). It is hard-coded in the code
  and does not depend on translations or the user-visible
  ``deviceName``.

## Forbidden

The unique ID assignment line **must not** reference any of:

* ``FIELD_DEVICE_NAME`` — translatable.
* ``FIELD_WNAME`` — user-controlled.
* ``translation_key`` — translation keys are presentation-layer.
* ``name=`` — names are not stable identifiers.

This is enforced by ``test_unique_id_contract_is_documented_and_followed``
in ``tests/test_code_quality.py``. The check inspects the line that sets
``self._attr_unique_id`` in ``custom_components/jackery_solarvault/entity.py``
and rejects any of the forbidden fragments.

## Migration story

There is none. The first public package shipped with the contract above;
no entry-version migrations are needed and ``CONF_SCAN_INTERVAL`` is
intentionally not present. Renaming or re-slugifying entities does not
change their unique ID.
