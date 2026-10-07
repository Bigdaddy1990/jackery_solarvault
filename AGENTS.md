## Abschlussregel

Gefundene Inkonsistenzen im autorisierten Aufgabenbereich beheben und gezielt verifizieren. Nicht bloß melden oder als erledigt abschließen. Bei einem echten Blocker die verbleibende Arbeit und den belegten Blocker ausdrücklich offen halten.

## Home-Assistant-Testbasis

`hacs.json` enthält keine Home-Assistant-Versionsangabe. `.HA_VERSION` und die passende Constraints-Fixture werden aus dem exakten Home-Assistant-Pin der installierten `pytest-homeassistant-custom-component`-Metadaten abgeleitet: `python -m scripts.sync_ha_test_baseline --write` nach bewusst geprüftem Stack-Update, `--check` in CI.
