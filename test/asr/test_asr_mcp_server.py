"""Regression tests for the lightweight ASR MCP frontend."""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import threading
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

import numpy as np
import pytest

from asr import asr_mcp_server
from asr import qwen3_asr_server


def _preprocess_module() -> SimpleNamespace:
    return SimpleNamespace(
        preprocess_audio=lambda _path, output_dir=None: "/tmp/fake.wav",
        get_audio_duration=lambda _path: 8.0,
    )


def _base_patches():
    return (
        mock.patch.object(asr_mcp_server, "_ensure_asr_ready", return_value=True),
        mock.patch.object(
            asr_mcp_server,
            "_transcribe_file",
            return_value={"text": "Complete transcript.", "language": "English"},
        ),
        mock.patch.object(asr_mcp_server, "_stop_asr_backend", return_value=True),
    )


def test_transcribe_podcast_reports_missing_hf_token() -> None:
    """Missing diarization credentials must be visible in the result."""
    ready, transcribe, stop_backend = _base_patches()
    with ready, transcribe, stop_backend as stop, mock.patch.dict(os.environ, {}, clear=True), \
            mock.patch.dict(sys.modules, {"preprocess": _preprocess_module()}):
        result = asr_mcp_server.transcribe_podcast("/fake.wav")

    assert result["text"] == "Complete transcript."
    assert result["diarization_status"] == "skipped"
    assert "HF_TOKEN" in result["diarization_error"]
    assert result["speaker_text_attribution"] is False
    assert result["segments"] == []
    stop.assert_not_called()


def test_transcribe_podcast_returns_speaker_timeline_without_fake_text() -> None:
    """Successful diarization returns its timeline and exact-count hint."""
    speaker_timeline = [
        {"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00"},
        {"start": 4.0, "end": 8.0, "speaker": "SPEAKER_01"},
    ]
    diarize = SimpleNamespace(run_diarization=mock.Mock(return_value=speaker_timeline))
    ready, transcribe, stop_backend = _base_patches()
    with ready, transcribe, stop_backend as stop, mock.patch.dict(os.environ, {"HF_TOKEN": "token"}, clear=True), \
            mock.patch.dict(
                sys.modules,
                {"preprocess": _preprocess_module(), "diarize": diarize},
            ):
        result = asr_mcp_server.transcribe_podcast("/fake.wav", num_speakers=2)

    assert result["diarization_status"] == "completed"
    assert result["diarization_error"] is None
    assert result["num_speakers"] == 2
    assert result["segments"] == speaker_timeline
    assert all("text" not in segment for segment in result["segments"])
    assert diarize.run_diarization.call_args.kwargs["num_speakers"] == 2
    stop.assert_called_once_with()


def test_transcribe_podcast_reports_unmet_exact_speaker_count() -> None:
    speaker_timeline = [
        {"start": 0.0, "end": 8.0, "speaker": "SPEAKER_00"},
    ]
    diarize = SimpleNamespace(run_diarization=mock.Mock(return_value=speaker_timeline))
    ready, transcribe, stop_backend = _base_patches()
    with ready, transcribe, stop_backend, mock.patch.dict(
        os.environ, {"HF_TOKEN": "token"}, clear=True
    ), mock.patch.dict(
        sys.modules,
        {"preprocess": _preprocess_module(), "diarize": diarize},
    ):
        result = asr_mcp_server.transcribe_podcast("/fake.wav", num_speakers=2)

    assert result["diarization_status"] == "failed"
    assert "requested exactly 2" in result["diarization_error"]
    assert "returned 1" in result["diarization_error"]
    assert result["segments"] == []


def test_transcribe_podcast_reports_empty_speaker_timeline() -> None:
    diarize = SimpleNamespace(run_diarization=mock.Mock(return_value=[]))
    ready, transcribe, stop_backend = _base_patches()
    with ready, transcribe, stop_backend, mock.patch.dict(
        os.environ, {"HF_TOKEN": "token"}, clear=True
    ), mock.patch.dict(
        sys.modules,
        {"preprocess": _preprocess_module(), "diarize": diarize},
    ):
        result = asr_mcp_server.transcribe_podcast("/silent.wav")

    assert result["diarization_status"] == "failed"
    assert "no speech segments" in result["diarization_error"]
    assert result["num_speakers"] == 0
    assert result["segments"] == []


def test_transcribe_podcast_reports_diarization_failure() -> None:
    """A pyannote failure must not silently look like a zero-speaker result."""
    diarize = SimpleNamespace(
        run_diarization=mock.Mock(side_effect=RuntimeError("model access denied"))
    )
    ready, transcribe, stop_backend = _base_patches()
    with ready, transcribe, stop_backend, mock.patch.dict(os.environ, {"HF_TOKEN": "token"}, clear=True), \
            mock.patch.dict(
                sys.modules,
                {"preprocess": _preprocess_module(), "diarize": diarize},
            ):
        result = asr_mcp_server.transcribe_podcast("/fake.wav")

    assert result["text"] == "Complete transcript."
    assert result["diarization_status"] == "failed"
    assert result["diarization_error"] == "model access denied"
    assert result["segments"] == []


def test_transcribe_podcast_rejects_non_positive_speaker_count() -> None:
    """Invalid exact-count hints fail before starting the ASR backend."""
    with mock.patch.object(asr_mcp_server, "_ensure_asr_ready") as ensure_ready:
        result = asr_mcp_server.transcribe_podcast("/fake.wav", num_speakers=0)

    assert result == {"error": "num_speakers must be a positive integer"}
    ensure_ready.assert_not_called()


def test_transcribe_podcast_does_not_overlap_asr_and_diarization() -> None:
    """A failed ASR release must prevent the second GPU stage from starting."""
    diarize = SimpleNamespace(run_diarization=mock.Mock())
    ready, transcribe, _stop_backend = _base_patches()
    with ready, transcribe, mock.patch.object(
        asr_mcp_server,
        "_stop_asr_backend",
        return_value=False,
    ) as stop, mock.patch.dict(
        os.environ, {"HF_TOKEN": "token"}, clear=True
    ), mock.patch.dict(
        sys.modules,
        {"preprocess": _preprocess_module(), "diarize": diarize},
    ):
        result = asr_mcp_server.transcribe_podcast("/fake.wav")

    stop.assert_called_once_with()
    diarize.run_diarization.assert_not_called()
    assert result["diarization_status"] == "failed"
    assert "not overlapped" in result["diarization_error"]


def test_transcribe_diarized_requires_hf_token(tmp_path: Path) -> None:
    """The full pipeline must fail fast when diarization credentials are absent."""
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"fake")
    with mock.patch.dict(os.environ, {}, clear=True), \
            mock.patch.object(asr_mcp_server, "_stop_asr_backend") as stop_backend:
        result = asr_mcp_server.transcribe_diarized(str(audio_path))

    assert "HF_TOKEN" in result["error"]
    assert result["speaker_text_attribution"] is False
    stop_backend.assert_not_called()


def test_8gb_profile_rejects_unvalidated_forced_aligner_pipeline(
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"fake")

    with mock.patch.dict(os.environ, {"ASR_PROFILE": "8gb"}, clear=True), \
            mock.patch.object(asr_mcp_server, "_stop_asr_backend") as stop_backend:
        result = asr_mcp_server.transcribe_diarized(str(audio_path))

    assert "not validated" in result["error"]
    assert "1.7B" in result["error"]
    assert result["speaker_text_attribution"] is False
    stop_backend.assert_not_called()


def test_transcribe_diarized_returns_speaker_attributed_text(
    tmp_path: Path,
) -> None:
    """The MCP wrapper must run all stages with timestamps and return text."""
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"fake")

    speaker_timeline = [
        {"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00"},
        {"start": 4.0, "end": 8.0, "speaker": "SPEAKER_01"},
    ]
    words = [
        {"word": "Hello", "start": 0.0, "end": 0.5},
        {"word": "there", "start": 4.0, "end": 4.5},
    ]
    merged = [
        {
            "speaker": "SPEAKER_00",
            "start": 0.0,
            "end": 0.5,
            "text": "Hello",
            "words": [words[0]],
        },
        {
            "speaker": "SPEAKER_01",
            "start": 4.0,
            "end": 4.5,
            "text": "there",
            "words": [words[1]],
        },
    ]
    diarize = SimpleNamespace(
        run_diarization=mock.Mock(return_value=speaker_timeline)
    )
    transcribe = SimpleNamespace(
        run_transcription=mock.Mock(
            return_value={
                "text": "Hello there",
                "language": "English",
                "words": words,
            }
        )
    )
    merge = SimpleNamespace(
        merge_diarization_asr=mock.Mock(return_value=merged)
    )

    with mock.patch.dict(os.environ, {"HF_TOKEN": "token"}, clear=True), \
            mock.patch.object(asr_mcp_server, "_stop_asr_backend", return_value=True), \
            mock.patch.object(asr_mcp_server, "_stop_competing_servers"), \
            mock.patch.dict(
                sys.modules,
                {
                    "preprocess": _preprocess_module(),
                    "diarize": diarize,
                    "transcribe": transcribe,
                    "merge": merge,
                },
            ):
        result = asr_mcp_server.transcribe_diarized(
            str(audio_path),
            language="en",
            num_speakers=2,
            context="proper noun",
        )

    assert result["text"] == "Hello there"
    assert result["language"] == "English"
    assert result["num_speakers"] == 2
    assert result["speaker_text_attribution"] is True
    assert result["segments"] == merged
    assert diarize.run_diarization.call_args.kwargs["num_speakers"] == 2
    assert transcribe.run_transcription.call_args.kwargs == {
        "language": "English",
        "context": "proper noun",
        "device": "cuda:0",
        "max_new_tokens": 4096,
        "max_inference_batch_size": 1,
        "return_timestamps": True,
    }
    merge.merge_diarization_asr.assert_called_once_with(
        speaker_timeline,
        words,
    )


def test_transcribe_diarized_rejects_unmet_exact_speaker_count(
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "short-dialogue.wav"
    audio_path.write_bytes(b"fake")
    speaker_timeline = [
        {"start": 0.0, "end": 8.0, "speaker": "SPEAKER_00"},
    ]
    diarize = SimpleNamespace(run_diarization=mock.Mock(return_value=speaker_timeline))
    transcribe = SimpleNamespace(run_transcription=mock.Mock())

    with mock.patch.dict(os.environ, {"HF_TOKEN": "token"}, clear=True), \
            mock.patch.object(asr_mcp_server, "_stop_asr_backend", return_value=True), \
            mock.patch.object(asr_mcp_server, "_stop_competing_servers"), \
            mock.patch.dict(
                sys.modules,
                {
                    "preprocess": _preprocess_module(),
                    "diarize": diarize,
                    "transcribe": transcribe,
                    "merge": SimpleNamespace(),
                },
            ):
        result = asr_mcp_server.transcribe_diarized(
            str(audio_path),
            num_speakers=2,
        )

    assert result["speaker_text_attribution"] is False
    assert "requested exactly 2" in result["error"]
    assert "returned 1" in result["error"]
    transcribe.run_transcription.assert_not_called()


def test_transcribe_diarized_rejects_empty_speaker_timeline(
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "silence.wav"
    audio_path.write_bytes(b"fake")
    diarize = SimpleNamespace(run_diarization=mock.Mock(return_value=[]))
    transcribe = SimpleNamespace(run_transcription=mock.Mock())

    with mock.patch.dict(os.environ, {"HF_TOKEN": "token"}, clear=True), \
            mock.patch.object(asr_mcp_server, "_stop_asr_backend", return_value=True), \
            mock.patch.object(asr_mcp_server, "_stop_competing_servers"), \
            mock.patch.dict(
                sys.modules,
                {
                    "preprocess": _preprocess_module(),
                    "diarize": diarize,
                    "transcribe": transcribe,
                    "merge": SimpleNamespace(),
                },
            ):
        result = asr_mcp_server.transcribe_diarized(str(audio_path))

    assert result["speaker_text_attribution"] is False
    assert "no speech segments" in result["error"]
    transcribe.run_transcription.assert_not_called()


def test_transcribe_diarized_rejects_non_positive_speaker_count(
    tmp_path: Path,
) -> None:
    """Invalid exact speaker counts fail before checking credentials or GPU."""
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"fake")
    with mock.patch.object(asr_mcp_server, "_stop_asr_backend") as stop_backend:
        result = asr_mcp_server.transcribe_diarized(
            str(audio_path),
            num_speakers=0,
        )

    assert result == {"error": "num_speakers must be a positive integer"}
    stop_backend.assert_not_called()


def test_launcher_disables_user_site_packages() -> None:
    """The isolated ASR runtime must not import packages from ~/.local."""
    launcher = Path(__file__).resolve().parents[2] / "asr" / "qwen3_asr_start.sh"
    text = launcher.read_text(encoding="utf-8")
    assert "export PYTHONNOUSERSITE=1" in text


def test_launcher_defaults_to_repository_uv_environment() -> None:
    """The launcher must not depend on discovery of the retired Conda runtime."""
    launcher = Path(__file__).resolve().parents[2] / "asr" / "qwen3_asr_start.sh"
    text = launcher.read_text(encoding="utf-8")
    assert 'environments/mcp-local-asr' in text
    assert '.venv/bin/python' in text
    assert 'conda run -n mcp-local-asr' not in text


def test_launcher_forwards_the_selected_asr_profile() -> None:
    launcher = Path(__file__).resolve().parents[2] / "asr" / "qwen3_asr_start.sh"
    text = launcher.read_text(encoding="utf-8")

    assert 'ASR_PROFILE="${ASR_PROFILE:-default}"' in text
    assert '--profile "$ASR_PROFILE"' in text


def test_mcp_backend_inherits_frontend_interpreter() -> None:
    """Auto-start must keep the backend in the MCP frontend environment."""
    completed = SimpleNamespace(returncode=0, stdout="", stderr="")
    with mock.patch.object(asr_mcp_server.subprocess, "Popen") as popen, \
            mock.patch.object(
                asr_mcp_server.subprocess,
                "run",
                return_value=completed,
            ), \
            mock.patch.object(asr_mcp_server.time, "sleep"), \
            mock.patch.object(
                asr_mcp_server,
                "_read_asr_health",
                return_value={"status": "ok", "profile": "default"},
            ):
        assert asr_mcp_server._start_asr_server() is True

    assert popen.call_args.kwargs["env"]["ASR_PYTHON"] == sys.executable


def test_mcp_restarts_a_backend_when_the_requested_profile_changes() -> None:
    health = {"status": "ok", "profile": "default"}
    with mock.patch.dict(os.environ, {"ASR_PROFILE": "8gb"}, clear=False), \
            mock.patch.object(
                asr_mcp_server,
                "_read_asr_health",
                return_value=health,
            ), mock.patch.object(
                asr_mcp_server,
                "_stop_asr_backend",
                return_value=True,
            ) as stop, mock.patch.object(
                asr_mcp_server,
                "_stop_competing_servers",
            ), mock.patch.object(
                asr_mcp_server,
                "_start_asr_server",
                return_value=True,
            ) as start:
        assert asr_mcp_server._ensure_asr_ready()

    stop.assert_called_once_with()
    start.assert_called_once_with()


def test_asr_installer_uses_uv_without_conda() -> None:
    """The ASR installer branch must remain independent of Conda discovery."""
    installer = Path(__file__).resolve().parents[2] / "install.sh"
    text = installer.read_text(encoding="utf-8")
    asr_block = text.split(
        "# --------------- ASR installation ---------------",
        maxsplit=1,
    )[1].split(
        "# --------------- OCR installation ---------------",
        maxsplit=1,
    )[0]
    assert '"$UV_BIN" sync --project "$ASR_PROJECT_DIR" --locked' in asr_block
    assert "ensure_environment" not in asr_block
    assert "CONDA_CMD" not in asr_block


def test_verbose_json_response_keeps_expanded_fields(tmp_path: Path) -> None:
    """FastAPI response filtering must not strip verbose response fields."""
    result = SimpleNamespace(text="Hello world", language="English")
    upload_path = tmp_path / "upload.wav"
    upload_path.write_bytes(b"fake audio")

    with mock.patch.object(qwen3_asr_server.asr_model, "model", object()), \
            mock.patch.object(
                qwen3_asr_server,
                "save_upload",
                new=AsyncMock(return_value=upload_path),
            ), \
            mock.patch.object(
                qwen3_asr_server.asr_model,
                "transcribe",
                return_value=[result],
            ):
        response = asyncio.run(
            qwen3_asr_server.transcribe_audio(
                mock.MagicMock(),
                language="English",
                response_format=qwen3_asr_server.ResponseFormat.verbose_json,
            )
        )

    payload = json.loads(response.body)
    assert payload == {
        "task": "transcribe",
        "language": "English",
        "duration": 0.0,
        "text": "Hello world",
        "segments": [],
    }


def test_asr_model_decodes_unsupported_audio_with_ffmpeg(
    tmp_path: Path,
) -> None:
    """Formats unsupported by libsndfile should be normalized with ffmpeg."""
    model = qwen3_asr_server.ASRModel()
    model.model = mock.Mock()
    model.model.transcribe.return_value = [
        SimpleNamespace(text="Hello", language="English")
    ]
    decode_error = qwen3_asr_server.sf.LibsndfileError(
        1,
        prefix="unsupported",
    )

    with mock.patch.object(
        qwen3_asr_server.sf,
        "read",
        side_effect=[decode_error, (np.zeros(16000, dtype=np.float32), 16000)],
    ), mock.patch.object(
        qwen3_asr_server.shutil,
        "which",
        return_value="/usr/bin/ffmpeg",
    ), mock.patch.object(
        qwen3_asr_server.tempfile,
        "mkdtemp",
        return_value=str(tmp_path),
    ), mock.patch.object(
        qwen3_asr_server.subprocess,
        "run",
    ) as run, mock.patch.object(
        qwen3_asr_server.shutil,
        "rmtree",
    ) as rmtree:
        result = model.transcribe("/tmp/input.m4a")

    run.assert_called_once()
    command = run.call_args.args[0]
    assert command[0] == "/usr/bin/ffmpeg"
    assert command[-1] == str(tmp_path / "decoded.wav")
    model.model.transcribe.assert_called_once_with(
        audio=str(tmp_path / "decoded.wav"),
        language=None,
    )
    assert result[0].text == "Hello"
    rmtree.assert_called_once_with(str(tmp_path), ignore_errors=True)


@pytest.mark.parametrize(
    ("duration_seconds", "expected_calls", "expected_text"),
    [
        (479, 1, "input"),
        (480, 1, "input"),
        (481, 2, "chunk_0000 chunk_0001"),
        (960, 2, "chunk_0000 chunk_0001"),
        (961, 3, "chunk_0000 chunk_0001 chunk_0002"),
    ],
)
def test_asr_chunk_boundaries_preserve_order_without_duplicates(
    duration_seconds: int,
    expected_calls: int,
    expected_text: str,
) -> None:
    model = qwen3_asr_server.ASRModel()
    model.model = mock.Mock()
    model.model.transcribe.side_effect = lambda *, audio, language: [
        SimpleNamespace(text=Path(audio).stem, language="English")
    ]

    with mock.patch.object(
        qwen3_asr_server.sf,
        "read",
        return_value=(np.zeros(duration_seconds, dtype=np.float32), 1),
    ), mock.patch.object(qwen3_asr_server.sf, "write") as write:
        result = model.transcribe("/tmp/input.wav", language="English")

    assert model.model.transcribe.call_count == expected_calls
    assert result[0].text == expected_text
    assert write.call_count == (0 if duration_seconds <= 480 else expected_calls)


def test_asr_model_uses_profile_specific_chunk_size() -> None:
    model = qwen3_asr_server.ASRModel(max_chunk_seconds=60)
    model.model = mock.Mock()
    model.model.transcribe.side_effect = lambda *, audio, language: [
        SimpleNamespace(text=Path(audio).stem, language="English")
    ]

    with mock.patch.object(
        qwen3_asr_server.sf,
        "read",
        return_value=(np.zeros(61, dtype=np.float32), 1),
    ), mock.patch.object(qwen3_asr_server.sf, "write") as write:
        result = model.transcribe("/tmp/input.wav", language="English")

    assert model.model.transcribe.call_count == 2
    assert result[0].text == "chunk_0000 chunk_0001"
    assert write.call_count == 2


def test_8gb_profile_rejects_settings_that_relax_validated_limits() -> None:
    profile = qwen3_asr_server.resolve_runtime_profile("8gb")
    app = SimpleNamespace(state=SimpleNamespace(max_chunk_seconds=61))

    with pytest.raises(ValueError, match="validated 8gb maximum"):
        qwen3_asr_server._bounded_profile_setting(
            app,
            profile,
            "max_chunk_seconds",
            "ASR_MAX_CHUNK_SECONDS",
        )


def test_8gb_profile_caps_the_pytorch_cuda_allocator() -> None:
    qwen_model = mock.Mock()
    qwen_model.from_pretrained.return_value = object()
    model = qwen3_asr_server.ASRModel()
    total_mib = 12282

    with mock.patch.dict(
        sys.modules,
        {"qwen_asr": SimpleNamespace(Qwen3ASRModel=qwen_model)},
    ), mock.patch.object(
        qwen3_asr_server.torch.cuda,
        "get_device_properties",
        return_value=SimpleNamespace(total_memory=total_mib * 1024**2),
    ), mock.patch.object(
        qwen3_asr_server.torch.cuda,
        "set_per_process_memory_fraction",
    ) as set_fraction:
        model.load(
            "/models/Qwen3-ASR-0.6B",
            profile="8gb",
            max_chunk_seconds=60,
            max_new_tokens=1024,
            cuda_memory_limit_mib=6144,
        )

    set_fraction.assert_called_once_with(6144 / total_mib, device="cuda:0")
    assert qwen_model.from_pretrained.call_args.kwargs["max_new_tokens"] == 1024


def test_only_8gb_asr_profile_reports_the_tested_ceiling() -> None:
    with mock.patch.object(
        qwen3_asr_server.torch.cuda,
        "is_available",
        return_value=False,
    ), mock.patch.object(qwen3_asr_server.asr_model, "profile", "default"):
        default_health = asyncio.run(qwen3_asr_server.health_check())

    with mock.patch.object(
        qwen3_asr_server.torch.cuda,
        "is_available",
        return_value=False,
    ), mock.patch.object(qwen3_asr_server.asr_model, "profile", "8gb"):
        bounded_health = asyncio.run(qwen3_asr_server.health_check())

    assert default_health["profile_tested_whole_device_vram_ceiling_mib"] is None
    assert bounded_health["profile_tested_whole_device_vram_ceiling_mib"] == 8000


def test_asr_removes_temporary_chunks_when_a_later_chunk_fails(
    tmp_path: Path,
) -> None:
    model = qwen3_asr_server.ASRModel()
    model.model = mock.Mock()
    model.model.transcribe.side_effect = [
        [SimpleNamespace(text="first", language="English")],
        RuntimeError("simulated inference failure"),
    ]
    chunk_directory = tmp_path / "chunks"

    def make_chunk_directory(*_args, **_kwargs) -> str:
        chunk_directory.mkdir()
        return str(chunk_directory)

    with mock.patch.object(
        qwen3_asr_server.sf,
        "read",
        return_value=(np.zeros(481, dtype=np.float32), 1),
    ), mock.patch.object(qwen3_asr_server.sf, "write"), mock.patch.object(
        qwen3_asr_server.tempfile,
        "mkdtemp",
        side_effect=make_chunk_directory,
    ):
        with pytest.raises(RuntimeError, match="simulated inference failure"):
            model.transcribe("/tmp/input.wav")

    assert not chunk_directory.exists()


def test_transcribe_file_preserves_actionable_backend_error_detail(
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "broken audio.wav"
    audio_path.write_bytes(b"not audio")
    response = io.BytesIO(
        json.dumps(
            {"detail": "Audio format is not supported and ffmpeg is unavailable"}
        ).encode()
    )
    backend_error = urllib.error.HTTPError(
        asr_mcp_server._transcribe_url(),
        500,
        "Internal Server Error",
        {},
        response,
    )

    with mock.patch.object(
        asr_mcp_server._DIRECT_OPENER,
        "open",
        side_effect=backend_error,
    ):
        result = asr_mcp_server._transcribe_file(str(audio_path))

    assert "Audio format is not supported" in result["error"]
    assert "ffmpeg is unavailable" in result["error"]


def test_loopback_backend_requests_bypass_environment_proxy() -> None:
    response = mock.MagicMock()
    response.__enter__.return_value.status = 200
    with mock.patch.object(
        asr_mcp_server._DIRECT_OPENER,
        "open",
        return_value=response,
    ) as direct, mock.patch(
        "urllib.request.urlopen",
        side_effect=AssertionError("loopback request used environment proxy"),
    ):
        assert asr_mcp_server._check_asr_health()

    direct.assert_called_once()


def test_backend_endpoint_serializes_overlapping_model_calls(
    tmp_path: Path,
) -> None:
    uploads = []
    for index in range(3):
        upload = tmp_path / f"upload-{index}.wav"
        upload.write_bytes(b"audio")
        uploads.append(upload)

    upload_iterator = iter(uploads)

    async def save_next_upload(_upload) -> Path:
        return next(upload_iterator)

    active = 0
    maximum_active = 0
    activity_lock = threading.Lock()

    def transcribe(*_args, **_kwargs):
        nonlocal active, maximum_active
        with activity_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.02)
        with activity_lock:
            active -= 1
        return [SimpleNamespace(text="ok", language="English")]

    async def run_calls() -> None:
        await asyncio.gather(
            *(
                qwen3_asr_server.transcribe_audio(mock.MagicMock())
                for _ in range(3)
            )
        )

    with mock.patch.object(qwen3_asr_server.asr_model, "model", object()), \
            mock.patch.object(
                qwen3_asr_server,
                "save_upload",
                side_effect=save_next_upload,
            ), mock.patch.object(
                qwen3_asr_server.asr_model,
                "transcribe",
                side_effect=transcribe,
            ):
        asyncio.run(run_calls())

    assert maximum_active == 1


def test_backend_endpoint_keeps_event_loop_responsive_during_inference(
    tmp_path: Path,
) -> None:
    upload = tmp_path / "upload.wav"
    upload.write_bytes(b"audio")
    started = threading.Event()
    release = threading.Event()

    async def save_upload(_upload) -> Path:
        return upload

    def transcribe(*_args, **_kwargs):
        started.set()
        release.wait(timeout=0.5)
        return [SimpleNamespace(text="ok", language="English")]

    async def run_call() -> float:
        before = time.perf_counter()
        task = asyncio.create_task(
            qwen3_asr_server.transcribe_audio(mock.MagicMock())
        )
        while not started.is_set():
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.01)
        elapsed = time.perf_counter() - before
        release.set()
        await task
        return elapsed

    with mock.patch.object(qwen3_asr_server.asr_model, "model", object()), \
            mock.patch.object(
                qwen3_asr_server,
                "save_upload",
                side_effect=save_upload,
            ), mock.patch.object(
                qwen3_asr_server.asr_model,
                "transcribe",
                side_effect=transcribe,
            ):
        elapsed = asyncio.run(run_call())

    assert elapsed < 0.2
