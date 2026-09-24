"""Tests for the release-gate version/changelog check."""

from pathlib import Path

from scripts.check_release import check_release


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def test_passes_when_version_and_changelog_match(tmp_path: Path) -> None:
    pyproject = _write(tmp_path / "pyproject.toml", '[project]\nversion = "0.2.0"\n')
    changelog = _write(
        tmp_path / "CHANGELOG.md",
        "## [Unreleased]\n\n## [0.2.0] - 2026-09-23\n\n- Stuff\n",
    )
    assert check_release("v0.2.0", pyproject, changelog) is None


def test_fails_when_tag_missing_v_prefix(tmp_path: Path) -> None:
    pyproject = _write(tmp_path / "pyproject.toml", '[project]\nversion = "0.2.0"\n')
    changelog = _write(tmp_path / "CHANGELOG.md", "## [0.2.0]\n")
    error = check_release("0.2.0", pyproject, changelog)
    assert error is not None
    assert "does not start with 'v'" in error


def test_fails_when_version_does_not_match_tag(tmp_path: Path) -> None:
    pyproject = _write(tmp_path / "pyproject.toml", '[project]\nversion = "0.1.0"\n')
    changelog = _write(tmp_path / "CHANGELOG.md", "## [0.2.0]\n")
    error = check_release("v0.2.0", pyproject, changelog)
    assert error is not None
    assert "does not match" in error


def test_fails_when_changelog_has_no_matching_entry(tmp_path: Path) -> None:
    pyproject = _write(tmp_path / "pyproject.toml", '[project]\nversion = "0.2.0"\n')
    changelog = _write(tmp_path / "CHANGELOG.md", "## [Unreleased]\n\n## [0.1.0]\n")
    error = check_release("v0.2.0", pyproject, changelog)
    assert error is not None
    assert "no '## [0.2.0]' entry" in error
