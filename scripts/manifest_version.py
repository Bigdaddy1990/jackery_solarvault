"""Read and validate the Home Assistant integration manifest version."""

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'custom_components' / 'jackery_solarvault' / 'manifest.json'
VERSION_RE = re.compile(r'^[0-9]+\.[0-9]+\.[0-9]+([-.+][0-9A-Za-z.-]+)?$')


def manifest_version() -> str:
    """Retrieve and validate the package version from the manifest.

    Reads the module-level MANIFEST JSON, extracts and strips the `version` field,
    and ensures it matches the module's expected semantic-version pattern.

    Returns:
        The validated manifest version string.

    Raises:
        SystemExit: If the `version` field is missing, not a string, empty after
        stripping, or does not match the expected release version format.
    """
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    version = manifest.get('version')
    if not isinstance(version, str) or not version.strip():
        raise SystemExit('manifest.json must contain a non-empty string version')
    version = version.strip()
    if not VERSION_RE.fullmatch(version):
        raise SystemExit(f"manifest version {version!r} is not a valid release version")
    return version


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the script.

    Supports the following flags:
    - --github-output: when set, write outputs for GitHub Actions.
    - --verify-tag: when set, verify the provided tag matches the manifest version.
    - --tag: tag value to compare against (default: empty string).

    Returns:
        argparse.Namespace: Parsed arguments with attributes `github_output` (bool), `verify_tag` (bool), and `tag` (str).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--github-output', action='store_true')
    parser.add_argument('--verify-tag', action='store_true')
    parser.add_argument('--tag', default='')
    return parser.parse_args()


def main() -> int:
    """Execute the CLI helper that validates the manifest version and emits the version.

    If tag verification is requested and the provided tag does not match the manifest version, an error annotation is written to stderr and the function exits with a failure code. When GitHub output is requested, the version and v-prefixed tag are appended to the file specified by the GITHUB_OUTPUT environment variable; otherwise the version is printed to stdout.

    Returns:
        exit_code (int): `0` on success, `1` if tag verification fails.
    """
    args = parse_args()
    version = manifest_version()
    tag = args.tag.removeprefix('v')
    if args.verify_tag and tag != version:
        print(
            f"::error::Tag '{tag}' does not match manifest version '{version}'",
            file=sys.stderr,
        )
        return 1
    if args.github_output:
        output = Path(__import__('os').environ['GITHUB_OUTPUT'])
        with output.open('a', encoding='utf-8') as stream:
            stream.write(f"version={version}\n")
            stream.write(f"tag=v{version}\n")
    else:
        print(version)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
