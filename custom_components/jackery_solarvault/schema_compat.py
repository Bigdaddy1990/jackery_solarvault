"""Use the schema class supplied and annotated by the installed Home Assistant.

HA 2026.9 already aliases Voluptuous to Probatio at runtime, but its form and
service annotations keep the Voluptuous name until 2026.10.0b0. Deriving the
constructor from HA's public PLATFORM_SCHEMA preserves the concrete type for
both type checkers without casts or package-wide typing overrides.

https://developers.home-assistant.io/blog/2026/09/30/probatio-validation-engine/
"""

from homeassistant.helpers.config_validation import PLATFORM_SCHEMA

Schema = type(PLATFORM_SCHEMA)
