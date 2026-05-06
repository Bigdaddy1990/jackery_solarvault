# Unique ID Contract

This integration keeps entity unique IDs stable across translations, friendly
names, app names, and dashboard labels.

## Rules

- Unique IDs are derived from the Jackery `device_id` plus a stable key suffix.
- translation keys, localized names, `deviceName`, `wname`, and app labels must
  never become part of an entity unique ID.
- Add-on battery pack entities may include the stable pack index because Jackery
  exposes battery packs as ordered app subcards.
- Device registry identifiers use the integration domain plus stable device or
  pack identifiers.
- Renaming a device in the app may change the visible Home Assistant name, but
  it must not change `unique_id`.

## Expected format

Main device entity:

```text
<device_id>_<stable_key_suffix>
```

Battery-pack entity:

```text
<device_id>_battery_pack_<index>_<stable_key_suffix>
```

No migration should be introduced just to rename an entity. Prefer translation
and display-name fixes over unique-ID changes.
