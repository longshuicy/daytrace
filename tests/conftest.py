from pathlib import Path

import pytest

from daytrace.rules import DEFAULT_RULES, Rules


@pytest.fixture
def projects(tmp_path: Path) -> Path:
    """A fake ~/Documents/Projects with work and personal repos."""
    root = tmp_path / "Projects"
    for repo in ["INCORE-2.0/incore-helm", "PDG", "personal/daytrace", "personal/twentyfour"]:
        (root / repo / ".git").mkdir(parents=True)
    return root


@pytest.fixture
def rules(projects: Path) -> Rules:
    text = DEFAULT_RULES.replace('"~/Documents/Projects/personal"', f'"{projects}/personal"')
    text = text.replace('"~/Documents/Projects"', f'"{projects}"')
    return Rules.from_text(text)
