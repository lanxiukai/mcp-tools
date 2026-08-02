from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ocr import ocr_mcp_server, ocr_server


class TestOcrRuntimeConfig(TestCase):
    def test_mcp_urls_prefer_generic_ocr_port(self) -> None:
        original_environment = os.environ.copy()
        try:
            with patch.dict(
                os.environ,
                {"OCR_PORT": "8123"},
                clear=True,
            ):
                reloaded = importlib.reload(ocr_mcp_server)
                self.assertEqual(reloaded._health_url(), "http://127.0.0.1:8123/health")
        finally:
            os.environ.clear()
            os.environ.update(original_environment)
            importlib.reload(ocr_mcp_server)

    def test_mcp_start_forwards_resolved_ocr_port(self) -> None:
        with patch.object(ocr_mcp_server, "_check_ocr_health", return_value=False), patch.object(
            ocr_mcp_server,
            "_stop_competing_servers",
        ), patch.object(ocr_mcp_server.subprocess, "Popen") as popen:
            ocr_mcp_server._try_start_ocr_server()
            self.assertEqual(
                popen.call_args.kwargs["env"]["OCR_PORT"],
                str(ocr_mcp_server.OCR_PORT),
            )

    def test_server_defaults_to_loopback_and_ocr_port(self) -> None:
        with patch.dict(os.environ, {"OCR_PORT": "9317"}, clear=True), patch.object(
            sys,
            "argv",
            ["ocr_server.py"],
        ):
            args = ocr_server.parse_args()
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 9317)

    def test_server_cli_allows_explicit_host_override(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["ocr_server.py", "--host", "0.0.0.0", "--port", "9318"],
        ):
            args = ocr_server.parse_args()
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 9318)

    def test_launcher_forwards_generic_configuration_without_pythonpath(self) -> None:
        script = Path(__file__).resolve().parents[2] / "ocr" / "ocr_start.sh"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "args.txt"
            fake_python = root / "python"
            fake_python.write_text(
                "#!/usr/bin/env bash\n"
                "test -z \"${PYTHONPATH:-}\" || exit 19\n"
                "printf '%s\\n' \"$@\" > \"$OCR_TEST_CAPTURE\"\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            result = subprocess.run(
                ["bash", str(script), "--fg"],
                env={
                    "OCR_PYTHON": str(fake_python),
                    "OCR_PORT": "9317",
                    "OCR_HOST": "127.0.0.1",
                    "OCR_MODEL_NAME": "PaddlePaddle/PaddleOCR-VL-1.6",
                    "OCR_TEST_CAPTURE": str(capture),
                    "PATH": os.environ["PATH"],
                    "PYTHONPATH": "",
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                capture.read_text(encoding="utf-8").splitlines(),
                [
                    "-m",
                    "ocr.ocr_server",
                    "--model",
                    "PaddlePaddle/PaddleOCR-VL-1.6",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "9317",
                ],
            )
