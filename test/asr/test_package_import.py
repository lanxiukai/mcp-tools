"""Regression: regular-package precedence on ``sys.path``.

``asr/__init__.py`` makes the repository-local ``asr`` a regular
package.  Python resolves regular packages in ``sys.path`` order
(:pep:`328`), so a local ``asr`` directory placed before an installed
decoy (e.g. ``site-packages/asr/__init__.py`` from an unrelated pip
install) wins the import immediately.  Without ``__init__.py`` the
directory would be a namespace package (:pep:`420`) that defers to
later regular packages on ``sys.path`` — a decoy could silently shadow
``asr/model_source.py``, ``asr/qwen3_asr_server.py``, etc.

This test proves the local regular package is always reached first.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
class TestPackageImportShadowing:
    """An installed ``asr`` package must not shadow the local regular package."""

    _REPO_ROOT: Path = Path(__file__).resolve().parents[2]

    def test_local_regular_package_not_shadowed_by_decoy_on_sys_path(
        self, tmp_path: Path
    ) -> None:
        """Given a local ``asr`` regular package before a decoy on ``sys.path``,
        when importing ``asr.model_source``, then the local implementation
        is resolved — the decoy is never reached.
        """
        # -- decoy package with __init__.py (regular package) ---------------
        decoy_dir = tmp_path / "decoy-lib"
        decoy_asr = decoy_dir / "asr"
        decoy_asr.mkdir(parents=True)
        (decoy_asr / "__init__.py").write_text(
            textwrap.dedent("""\
            # Decoy package — must not be reached when importing the real
            # asr/ directory from the repository.
            DECOY_SENTINEL = "decoy-reached"
            """)
        )

        # -- subprocess verifier --------------------------------------------
        verifier = textwrap.dedent("""\
        import os, sys

        # Print sys.path to aid debugging on failure
        print("sys.path[0]:", sys.path[0], flush=True)
        print("sys.path[1]:", sys.path[1], flush=True)

        import asr.model_source
        from asr.model_source import HUB_MODEL_ID

        # The real HUB_MODEL_ID references Qwen.  If we got the decoy
        # it would be "decoy-reached".
        assert HUB_MODEL_ID == "Qwen/Qwen3-ASR-1.7B", (
            f"Expected real HUB_MODEL_ID, got {HUB_MODEL_ID!r}"
        )
        """)

        verifier_path = tmp_path / "verifier.py"
        verifier_path.write_text(verifier)

        # -- run: real repo root first, then an installed-package decoy -----
        # The repository's path precedes site-packages when executing its
        # tools. A namespace package would still be skipped in favor of the
        # later regular decoy; a regular local package must win immediately.
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([
            str(self._REPO_ROOT),     # repository-local asr/
            str(decoy_dir),           # installed-package decoy
        ])

        result = subprocess.run(
            [sys.executable, str(verifier_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        # -- assertions -----------------------------------------------------
        # The local regular package must resolve before the decoy.  A
        # namespace package (no __init__.py) would be skipped in favor
        # of the later regular decoy instead.
        assert result.returncode == 0, (
            f"Subprocess failed (rc={result.returncode}):\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )

    def test_import_asr_qwen3_asr_server_succeeds_in_package_mode(
        self, tmp_path: Path
    ) -> None:
        """Given the repo root on sys.path, when ``import asr.qwen3_asr_server``
        is executed, then the import succeeds without starting a server or
        loading a model, and the repository resolver is bound on the module.

        ``qwen3_asr_server.py`` uses ``if __package__`` to pick the correct
        import form — ``from .model_source import resolve_model_source`` in
        package mode, ``from model_source import resolve_model_source`` in
        direct-script mode (``python asr/qwen3_asr_server.py``).  This test
        proves the package-mode path resolves correctly, without touching the
        network or loading a model.
        """
        verifier = textwrap.dedent("""\
        import sys

        # -- import the module (must succeed without side-effects) ----------
        import asr.qwen3_asr_server as srv  # noqa: E402

        # -- verify the repository resolver is bound on the module ----------
        from asr.model_source import resolve_model_source as direct_ref  # noqa: E402
        from asr.qwen3_asr_server import resolve_model_source as via_module  # noqa: E402

        assert via_module is direct_ref, (
            f"resolve_model_source mismatch: {via_module!r} vs {direct_ref!r}"
        )
        assert srv.REPO_DIR is not None, "REPO_DIR is None"

        print("IMPORT_OK", flush=True)
        """)

        verifier_path = tmp_path / "verifier_import.py"
        verifier_path.write_text(verifier)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(self._REPO_ROOT)

        result = subprocess.run(
            [sys.executable, str(verifier_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, (
            f"Subprocess failed (rc={result.returncode}):\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
        assert "IMPORT_OK" in result.stdout, (
            f"Import did not reach OK marker:\n{result.stdout}"
        )
