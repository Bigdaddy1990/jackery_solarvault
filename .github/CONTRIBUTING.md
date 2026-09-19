# Contributing to jackery\_solarvault

Thank you for your interest in improving the jackery\_solarvault integration. This guide explains how you can deliver high-quality contributions quickly and confidently.

## Know the quality expectations

* jackery\_solarvault declares the Platinum level of the Home Assistant integration quality scale (see `custom\_components/jackery\_solarvault/manifest.json`). Contributions must preserve the evidence tracked in `custom\_components/jackery\_solarvault/quality\_scale.yaml` and related compliance reports.
* Review `custom\_components/jackery\_solarvault/quality\_scale.yaml` before you start so you understand which requirements are complete, exempt, or still marked as TODO.
* When you finish a requirement or claim an exemption, update the quality scale file and include a short comment that explains the change.

## Set up your environment

1. Use Python 3.14 or newer.
2. Fork the repository and create a feature branch for each contribution.
3. Create a virtual environment and install the dependencies:

```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows use `.venv\\Scripts\\activate`
   pip install -r requirements.txt -r requirements\_test.txt
   # Install the code quality hooks (ruff, mypy, pylint hass\_logger, etc.)
   pre-commit install
   pre-commit install --hook-type pre-push
   ```

4. Install any additional tooling you need for documentation or translation updates.

## Follow development guidelines

* Write asynchronous, fully typed code and prefer modern Python features such as pattern matching, dataclasses, and `ConfigEntry.runtime\_data`.
* Reuse shared helpers under `custom\_components/jackery\_solarvault/` and constants from `homeassistant.const` instead of hardcoding values.
* Keep all user-facing text in American English, use sentence case for headings, and speak to the user in second-person voice.
* Update or add tests under `tests/` for every behavior change so coverage stays above the quality gates in `pyproject.toml` and `docs/coverage\_quality\_gates.md`.
* Revise documentation, release notes, and translations (for example files in `docs/`, `README.md`, and `custom\_components/jackery\_solarvault/translations/`) whenever behavior or options change.
* Enforce module-level coverage for critical runtime files (`coordinator.py`, `config\_flow.py`, `services.py`, and `data\_manager.py`) by running `python -m scripts.enforce\_coverage\_gates --coverage-xml coverage.xml` before opening a PR.
* Do not merge pull requests that add new logic without accompanying tests.

### Required Home Assistant references

jackery\_solarvault contributions must align with the official Home Assistant developer documentation and policy updates. Review the following sources before you begin and use them as the authoritative reference when implementing or reviewing changes:

* Home Assistant developer blog and platform overview:

  * https://developers.home-assistant.io/
  * https://developers.home-assistant.io/blog
* Development expectations and validation:

  * https://developers.home-assistant.io/docs/development\_checklist
  * https://developers.home-assistant.io/docs/development\_guidelines
  * https://developers.home-assistant.io/docs/development\_testing
  * https://developers.home-assistant.io/docs/development\_typing
  * https://developers.home-assistant.io/docs/development\_validation
  * https://www.home-assistant.io/docs/tools/check\_config/
* Quality scale rules and checklists:

  * https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist
  * https://developers.home-assistant.io/docs/core/integration-quality-scale/rules
  * https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/action-setup/
  * https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/common-modules
* Code review and documentation standards:

  * https://developers.home-assistant.io/docs/creating\_component\_code\_review
  * https://developers.home-assistant.io/docs/creating\_platform\_code\_review
  * https://developers.home-assistant.io/docs/documenting/create-page
  * https://developers.home-assistant.io/docs/documenting/integration-docs-examples
  * https://developers.home-assistant.io/docs/documenting/yaml-style-guide
* Core platform behaviors and helpers:

  * https://developers.home-assistant.io/docs/instance\_url
  * https://developers.home-assistant.io/docs/core/platform/repairs
  * https://developers.home-assistant.io/docs/core/platform/reproduce\_state
  * https://developers.home-assistant.io/docs/core/platform/significant\_change
* Config entries, flows, and automations:

  * https://developers.home-assistant.io/docs/automations
  * https://developers.home-assistant.io/docs/config\_entries\_index
  * https://developers.home-assistant.io/docs/data\_entry\_flow\_index
  * https://developers.home-assistant.io/docs/device\_automation\_action
  * https://developers.home-assistant.io/docs/device\_automation\_condition
  * https://developers.home-assistant.io/docs/device\_automation\_index
  * https://developers.home-assistant.io/docs/device\_automation\_trigger
  * https://developers.home-assistant.io/docs/internationalization/custom\_integration
* Mobile app and intent integrations:

  * https://developers.home-assistant.io/docs/api/native-app-integration/notifications
  * https://developers.home-assistant.io/docs/api/native-app-integration/sending-data
  * https://developers.home-assistant.io/docs/api/native-app-integration/sensors
  * https://developers.home-assistant.io/docs/api/native-app-integration/setup
  * https://developers.home-assistant.io/docs/core/llm/
  * https://developers.home-assistant.io/docs/intent\_builtin
  * https://developers.home-assistant.io/docs/intent\_conversation\_api

## Run local checks

Run the same checks that continuous integration executes before you open a pull request:

```bash
ruff format custom\_components/jackery\_solarvault tests
ruff check custom\_components/jackery\_solarvault tests
python -m scripts.enforce\_test\_requirements
python -m scripts.sync\_localization\_flags
mypy custom\_components/jackery\_solarvault
pytest --cov=custom\_components/jackery\_solarvault tests
python -m scripts.enforce\_coverage\_gates --coverage-xml coverage.xml
pre-commit run --all-files
python -m hassfest  # Available via the Home Assistant hassfest tool or the hassfest PyPI package
```

If you have Home Assistant Core tooling available, also run the HACS validation (`hacs action --category integration`) to mirror the `hacs.yml` workflow. Fix any reported issues before you submit your changes.

## Submit your pull request

* Fill out the pull request template, describe the intent of your change, and link any related issues.
* Include a concise test plan listing the commands you ran. Attach screenshots or recordings for UI-visible updates.
* Keep commits focused and explain documentation, code, and translation updates in your summary.
* Stay involved after opening the pull request by responding to feedback and rebasing on `main` until your contribution merges.

We appreciate your help making jackery\_solarvault better for every pet family!
