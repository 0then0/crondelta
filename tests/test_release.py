import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_release", ROOT / "scripts/check_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_current_release_metadata():
    assert release.check_release(ROOT, "v0.1.0") == "0.1.0"


@pytest.mark.parametrize("tag", ["0.1.0", "v0.2.0", "v0.1.0-extra", ""])
def test_wrong_release_tag_is_rejected(tag):
    with pytest.raises(ValueError, match="release tag must be"):
        release.check_release(ROOT, tag)


@pytest.mark.parametrize("module", ['__version__ = "0.2.0"', '"module without version"'])
def test_runtime_version_mismatch_blocks_release(tmp_path, module):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    (tmp_path / "src/crondelta").mkdir(parents=True)
    (tmp_path / "src/crondelta/__init__.py").write_text(module)
    with pytest.raises(ValueError, match="differs from __version__"):
        release.check_release(tmp_path, "v0.1.0")
