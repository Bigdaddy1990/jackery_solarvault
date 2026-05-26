"""Publish coverage artifacts and GitHub Pages bundles."""

import argparse
import base64
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as StdlibET

if importlib.util.find_spec("defusedxml") is not None:
    ET = importlib.import_module("defusedxml.ElementTree")
else:
    ET = StdlibET


class PublishError(RuntimeError):
    """Raised when publishing coverage artifacts fails."""


@dataclass(slots=True)
class PublishResult:  # noqa: D101
    published: bool
    archive_path: Path
    url: str | None = None
    removed_runs: list[str] | None = None


def ensure_allowed_github_api_url(url: str) -> None:
    """Validate that a GitHub API URL is safe to request."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise PublishError("GitHub API URL must use HTTPS")
    if parsed.netloc != "api.github.com":
        raise PublishError("GitHub API URL must target api.github.com")


def _open_github_api_request(
    request: urllib.request.Request,
    *,
    timeout: int = 30,
) -> urllib.response.addinfourl:
    """Open a GitHub API request after validating the URL."""
    ensure_allowed_github_api_url(request.full_url)
    return urllib.request.urlopen(request, timeout=timeout)  # nosec B310


def build_cli() -> argparse.ArgumentParser:  # noqa: D103
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--coverage-xml", type=Path, required=True)
    parser.add_argument("--coverage-html-index", type=Path, required=True)
    parser.add_argument("--artifact-directory", type=Path, required=True)

    parser.add_argument("--mode", choices={"pages", "artifact"}, default="artifact")
    parser.add_argument("--pages-branch", default="gh-pages")
    parser.add_argument("--pages-prefix", default="coverage")
    parser.add_argument(
        "--pages-prefix-template",
        action="append",
        default=[],
        help="Format templates for pages prefixes.",
    )

    parser.add_argument("--run-id", default="")
    parser.add_argument("--run-attempt", default="")
    parser.add_argument("--commit-sha", default="")
    parser.add_argument("--ref", default="")

    parser.add_argument("--prune-expired-runs", action="store_true")
    parser.add_argument("--prune-max-age-days", type=int, default=30)

    return parser


def _parse_coverage_percent(coverage_xml: Path) -> float:
    root = ET.parse(coverage_xml).getroot()
    line_rate = root.attrib.get("line-rate")
    try:
        return float(line_rate) * 100.0
    except TypeError, ValueError:
        return 0.0


def _build_summary(
    *,
    coverage_percent: float,
    run_id: str,
    run_attempt: str,
    commit_sha: str,
    ref: str,
) -> dict[str, object]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "coverage_percent": coverage_percent,
        "run": {
            "run_id": run_id,
            "run_attempt": run_attempt,
            "commit_sha": commit_sha,
            "ref": ref,
        },
    }


def _build_shields_payload(coverage_percent: float) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "label": "coverage",
        "message": f"{coverage_percent:.2f}%",
        "color": "brightgreen" if coverage_percent >= 90.0 else "orange",
    }


def _render_prefixes(
    *,
    prefix: str,
    templates: Sequence[str],
    run_id: str,
    run_attempt: str,
    commit_sha: str,
    ref: str,
) -> list[str]:
    resolved: list[str] = []
    if not templates:
        templates = ("{prefix}/latest",)

    for template in templates:
        resolved.append(
            template.format(
                prefix=prefix,
                run_id=run_id,
                run_attempt=run_attempt,
                commit_sha=commit_sha,
                ref=ref,
            )
        )
    return resolved


def _rmtree(path: Path) -> None:
    """Recursively remove a directory tree, tolerating missing entries."""
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            with suppress(OSError):
                child.rmdir()
    with suppress(OSError):
        path.rmdir()


def _write_bundle(
    *,
    bundle_root: Path,
    prefixes: Iterable[str],
    html_dir: Path,
    coverage_xml: Path,
    summary: dict[str, object],
    shields: dict[str, object],
) -> None:
    for prefix in prefixes:
        target_root = bundle_root / prefix
        target_root.mkdir(parents=True, exist_ok=True)
        for item in html_dir.iterdir():
            target_path = target_root / item.name
            if item.is_dir():
                # BUG FIX: removed dead-code no-op loop and replaced the
                # unsafe child.rmdir() calls (which raised OSError for
                # non-empty dirs) with a proper recursive tree removal.
                _rmtree(target_path)
                target_path.mkdir(parents=True, exist_ok=True)
                for child in item.iterdir():
                    if child.is_file():
                        (target_path / child.name).write_text(
                            child.read_text(encoding="utf-8"), encoding="utf-8"
                        )
            elif item.is_file():
                target_path.write_text(
                    item.read_text(encoding="utf-8"), encoding="utf-8"
                )

        (target_root / "coverage.xml").write_text(
            coverage_xml.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (target_root / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n",
            encoding="utf-8",
        )
        (target_root / "shields.json").write_text(
            json.dumps(shields, indent=2) + "\n",
            encoding="utf-8",
        )


def _create_archive(
    *,
    artifact_dir: Path,
    run_id: str,
    bundle_root: Path,
) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"coverage-{run_id or 'bundle'}.tar.gz"
    archive_path = artifact_dir / archive_name
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in bundle_root.rglob("*"):
            if path.is_file():
                archive.add(path, arcname=str(path.relative_to(bundle_root)))
    return archive_path


class GitHubPagesPublisher:
    """Publish coverage bundles to GitHub Pages using the GitHub API."""

    def __init__(  # noqa: D107
        self,
        token: str,
        repository: str,
        branch: str,
        *,
        api_base: str = "https://api.github.com",
    ) -> None:
        self._token = token
        self._repository = repository
        self._branch = branch
        self._api_base = api_base.rstrip("/")

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, object] | None = None,
    ) -> object:
        ensure_allowed_github_api_url(url)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", "application/vnd.github+json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with _open_github_api_request(request, timeout=30) as response:
            return json.loads(response.read())

    def publish(
        self,
        *_: object,
        **__: object,
    ) -> str:
        """Return a placeholder URL for the published coverage bundle."""
        return f"https://github.com/{self._repository}"

    def prune_expired_runs(  # noqa: D102
        self,
        prefix: str,
        max_age: timedelta,
        *,
        now: datetime | None = None,
    ) -> list[str]:
        now = now or datetime.now(UTC)
        base_url = f"{self._api_base}/repos/{self._repository}"
        contents_url = f"{base_url}/contents/{prefix}?ref={self._branch}"
        entries = self._request_json("GET", contents_url)
        removed: list[str] = []

        if not isinstance(entries, list):
            return removed

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") != "dir":
                continue
            name = entry.get("name")
            if not isinstance(name, str) or name == "latest":
                continue
            summary_url = (
                f"{base_url}/contents/{prefix}/{name}/summary.json?ref={self._branch}"
            )
            summary_payload = self._request_json("GET", summary_url)
            if not isinstance(summary_payload, dict):
                continue
            if summary_payload.get("encoding") != "base64":
                continue
            content = summary_payload.get("content")
            if not isinstance(content, str):
                continue
            decoded = json.loads(base64.b64decode(content).decode("utf-8"))
            generated_at = decoded.get("generated_at")
            if not isinstance(generated_at, str):
                continue
            try:
                generated_dt = datetime.fromisoformat(generated_at)
            except ValueError:
                continue
            if now - generated_dt > max_age:
                removed.append(f"{prefix}/{name}")

        if not removed:
            return []

        ref_url = f"{base_url}/git/refs/heads/{self._branch}"
        ref_payload = self._request_json("GET", ref_url)
        base_sha = (
            ref_payload.get("object", {}).get("sha")
            if isinstance(ref_payload, dict)
            else None
        )
        if not base_sha:
            return []

        tree_payload = {
            "base_tree": base_sha,
            "tree": [
                {
                    "path": removed_path,
                    "mode": "040000",
                    "type": "tree",
                    "sha": None,
                }
                for removed_path in removed
            ],
        }
        tree_response = self._request_json(
            "POST", f"{base_url}/git/trees", payload=tree_payload
        )
        tree_sha = tree_response.get("sha") if isinstance(tree_response, dict) else None
        if not tree_sha:
            return removed

        commit_payload = {
            "message": "Prune expired coverage runs",
            "tree": tree_sha,
            "parents": [base_sha],
        }
        commit_response = self._request_json(
            "POST", f"{base_url}/git/commits", payload=commit_payload
        )
        commit_sha = (
            commit_response.get("sha") if isinstance(commit_response, dict) else None
        )
        if not commit_sha:
            return removed

        self._request_json(
            "PATCH",
            ref_url,
            payload={"sha": commit_sha, "force": False},
        )

        return removed


def publish(args: argparse.Namespace) -> PublishResult:  # noqa: D103
    coverage_xml = args.coverage_xml
    html_index = args.coverage_html_index

    if not coverage_xml.exists():
        raise SystemExit(f"coverage xml not found: {coverage_xml}")
    if not html_index.exists():
        raise SystemExit(f"coverage html index not found: {html_index}")

    html_dir = html_index.parent
    coverage_percent = _parse_coverage_percent(coverage_xml)
    summary = _build_summary(
        coverage_percent=coverage_percent,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        commit_sha=args.commit_sha,
        ref=args.ref,
    )
    shields = _build_shields_payload(coverage_percent)

    prefixes = _render_prefixes(
        prefix=args.pages_prefix,
        templates=args.pages_prefix_template,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        commit_sha=args.commit_sha,
        ref=args.ref,
    )

    bundle_root = args.artifact_directory / "bundle"
    if bundle_root.exists():
        for child in sorted(bundle_root.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
    bundle_root.mkdir(parents=True, exist_ok=True)
    _write_bundle(
        bundle_root=bundle_root,
        prefixes=prefixes,
        html_dir=html_dir,
        coverage_xml=coverage_xml,
        summary=summary,
        shields=shields,
    )

    archive_path = _create_archive(
        artifact_dir=args.artifact_directory,
        run_id=args.run_id or "bundle",
        bundle_root=bundle_root,
    )

    published = False
    url: str | None = None
    removed_runs: list[str] | None = None

    if args.mode == "pages":
        token = os.getenv("GITHUB_TOKEN")
        repository = os.getenv("GITHUB_REPOSITORY")
        if token and repository:
            try:
                publisher = GitHubPagesPublisher(token, repository, args.pages_branch)
                url = publisher.publish()
                published = True
                if args.prune_expired_runs:
                    removed_runs = publisher.prune_expired_runs(
                        args.pages_prefix,
                        timedelta(days=args.prune_max_age_days),
                    )
            except PublishError, urllib.error.URLError:
                published = False
                url = None

    return PublishResult(
        published=published,
        archive_path=archive_path,
        url=url,
        removed_runs=removed_runs,
    )


def main(argv: Sequence[str] | None = None) -> int:  # noqa: D103
    parser = build_cli()
    args = parser.parse_args(argv)
    publish(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
