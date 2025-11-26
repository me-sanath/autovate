from __future__ import annotations

from pathlib import Path

from app import testgen


def test_generate_pytest_skeletons(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    src = repo / "pkg"
    src.mkdir()
    (src / "module.py").write_text("def hello():\n    return 42\n", encoding="utf-8")

    created = testgen.generate_pytest_skeletons(repo)
    assert any(path.endswith("tests/auto/test_module.py") for path in created.keys())
    generated_file = repo / "tests" / "auto" / "test_module.py"
    assert generated_file.exists()
    assert "importlib" in generated_file.read_text()

