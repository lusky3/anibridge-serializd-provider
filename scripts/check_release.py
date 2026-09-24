"""Verify a release tag matches the package version and has a changelog entry."""

import re
import sys
import tomllib
from pathlib import Path


def check_release(tag: str, pyproject_path: Path, changelog_path: Path) -> str | None:
    """Return an error message if the release is invalid, else None."""
    if not tag.startswith("v"):
        return f"Tag '{tag}' does not start with 'v'"

    tag_version = tag[1:]
    pyproject_data = tomllib.loads(pyproject_path.read_text())
    pyproject_version = pyproject_data["project"]["version"]

    if tag_version != pyproject_version:
        return (
            f"Tag version '{tag_version}' does not match "
            f"pyproject.toml version '{pyproject_version}'"
        )

    pattern = re.compile(rf"^## \[{re.escape(tag_version)}\]", re.MULTILINE)
    if not pattern.search(changelog_path.read_text()):
        return f"CHANGELOG.md has no '## [{tag_version}]' entry"

    return None


def main() -> int:
    """Run the release gate against the given tag and report the result."""
    if len(sys.argv) != 2:
        print("Usage: check_release.py <tag>", file=sys.stderr)
        return 2

    tag = sys.argv[1]
    error = check_release(tag, Path("pyproject.toml"), Path("CHANGELOG.md"))
    if error:
        print(f"Release gate failed: {error}", file=sys.stderr)
        return 1

    print(f"Release gate passed for {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
