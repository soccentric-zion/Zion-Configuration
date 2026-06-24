"""Regression test for deploy/install.sh copying required files (#52).

The installer used to copy only app/, static/, and requirements.txt —
silently losing compliance/, the example configs, and req.md. The
runtime falls back to placeholder data when compliance is missing,
which masked the bug in production installs. Fix: install.sh now
copies all required directories/files and verifies the critical ones
are present after install.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "deploy" / "install.sh"


def _files_copied_by_install() -> set[str]:
    """Parse install.sh and return the set of paths it would copy into $APP_DIR."""
    text = INSTALL_SH.read_text()
    # Find the `cp -r` block that targets $APP_DIR. Match the entire
    # multi-line command (backslash-continued) and pull every
    # "$SRC_DIR/<path>" argument from it.
    cp_block = re.search(
        r'cp -r\s*\\\s*\n((?:\s*"\$SRC_DIR/[^"]+"\s*\\\s*\n)+)\s*"\$APP_DIR/"',
        text,
    )
    assert cp_block is not None, "could not find `cp -r ... $APP_DIR/` block in install.sh"
    paths: set[str] = set()
    for m in re.finditer(r'"\$SRC_DIR/([^"]+)"', cp_block.group(1)):
        paths.add(m.group(1))
    return paths


def test_install_copies_compliance_dir() -> None:
    """install.sh must include $SRC_DIR/compliance in the cp -r list (#52)."""
    paths = _files_copied_by_install()
    assert "compliance" in paths, (
        f"install.sh does not copy compliance/ — production installs lose CVE/SBOM data; "
        f"copied paths: {sorted(paths)}"
    )


def test_install_copies_config_examples() -> None:
    """install.sh must include the example configs and req.md (#52)."""
    paths = _files_copied_by_install()
    assert "config.example.yaml" in paths
    assert "config.yaml.example" in paths
    assert "req.md" in paths


def test_install_verifies_critical_files() -> None:
    """install.sh must warn if any critical file is missing after copy (#52)."""
    text = INSTALL_SH.read_text()
    assert (
        "compliance/compliance.yaml" in text
    ), "install.sh must verify compliance/compliance.yaml is present after install"
    assert "WARNING" in text, "install.sh must warn (not silently fail) when a file is missing"


def test_install_sh_passes_bash_syntax_check() -> None:
    """The script must parse without errors (#52)."""
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"
