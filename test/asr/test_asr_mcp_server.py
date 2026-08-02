"""Regression tests for the lightweight ASR MCP frontend."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

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
    )


def test_transcribe_podcast_reports_missing_hf_token() -> None:
    """Missing diarization credentials must be visible in the result."""
    ready, transcribe = _base_patches()
    with ready, transcribe, mock.patch.dict(os.environ, {}, clear=True), \
            mock.patch.dict(sys.modules, {"preprocess": _preprocess_module()}):
        result = asr_mcp_server.transcribe_podcast("/fake.wav")

    assert result["text"] == "Complete transcript."
    assert result["diarization_status"] == "skipped"
    assert "HF_TOKEN" in result["diarization_error"]
    assert result["speaker_text_attribution"] is False
    assert result["segments"] == []


def test_transcribe_podcast_returns_speaker_timeline_without_fake_text() -> None:
    """Successful diarization returns its timeline and exact-count hint."""
    speaker_timeline = [
        {"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00"},
        {"start": 4.0, "end": 8.0, "speaker": "SPEAKER_01"},
    ]
    diarize = SimpleNamespace(run_diarization=mock.Mock(return_value=speaker_timeline))
    ready, transcribe = _base_patches()
    with ready, transcribe, mock.patch.dict(os.environ, {"HF_TOKEN": "token"}, clear=True), \
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


def test_transcribe_podcast_reports_diarization_failure() -> None:
    """A pyannote failure must not silently look like a zero-speaker result."""
    diarize = SimpleNamespace(
        run_diarization=mock.Mock(side_effect=RuntimeError("model access denied"))
    )
    ready, transcribe = _base_patches()
    with ready, transcribe, mock.patch.dict(os.environ, {"HF_TOKEN": "token"}, clear=True), \
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
