"""Tests for model-source resolution in the ASR Pipeline orchestration layer.

Covers:
- Shared ``resolve_model_source`` integration in ``transcribe.run_transcription()``
- ``resolve_forced_aligner_source`` integration for ForcedAligner handoff
"""

import os
import sys
from pathlib import Path
from unittest import mock

# Allow importing modules from this hyphenated directory.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PIPELINE_DIR = _REPO_ROOT / "asr-pipeline"
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

import transcribe as transcribe_mod  # noqa: E402

FA_HUB_ID: str = "Qwen/Qwen3-ForcedAligner-0.6B"


# ---------------------------------------------------------------------------
# Shared model resolver integration
# ---------------------------------------------------------------------------


def test_run_transcription_uses_shared_resolver() -> None:
    """Regression: run_transcription should resolve model via shared resolver.

    Before fix: transcribe.py hard-coded ``"Qwen/Qwen3-ASR-1.7B"``.
    After fix: ``resolve_model_source(None, REPO_DIR)`` is called and
    its return value becomes the first positional arg to
    ``Qwen3ASRModel.from_pretrained``.
    """
    sentinel = "/tmp/fake-local-model-directory"
    repo_root = _REPO_ROOT

    mock_resolver = mock.MagicMock(return_value=sentinel)
    _original_resolver = transcribe_mod.resolve_model_source
    transcribe_mod.resolve_model_source = mock_resolver

    try:
        with mock.patch(
            "transcribe._read_audio_mono_f32",
        ) as m_read:
            m_read.return_value = (
                [0.0] * 16000,  # 1 sec @ 16 kHz
                16000,
            )
            with mock.patch("transcribe.Qwen3ASRModel") as m_model:
                m_model.from_pretrained.return_value.transcribe.return_value = []
                with mock.patch("torch.cuda.empty_cache"):
                    transcribe_mod.run_transcription(
                        audio_path="/fake/audio.wav",
                        language="English",
                    )

        mock_resolver.assert_called_once_with(None, repo_root)

        fp_call_args = m_model.from_pretrained.call_args
        fp_first_arg = fp_call_args[0][0]
        assert fp_first_arg == sentinel, (
            f"resolve_model_source sentinel '{sentinel}' should be "
            f"first positional arg to from_pretrained, "
            f"but got '{fp_first_arg}'"
        )
    finally:
        transcribe_mod.resolve_model_source = _original_resolver


# ---------------------------------------------------------------------------
# ForcedAligner handoff in run_transcription
# ---------------------------------------------------------------------------


class TestPipelineForcedAlignerResolution:
    """Regression: transcription resolves ForcedAligner when timestamps on."""

    def test_default_timestamps_mode_resolves_both_asr_and_fa_sources(
        self,
    ) -> None:
        """Given timestamps, when transcribing, then resolve the aligner source."""
        fa_sentinel = "/tmp/local-fa-model"
        m_fa_resolver = mock.MagicMock(return_value=fa_sentinel)
        with mock.patch.object(
            transcribe_mod, "resolve_forced_aligner_source", m_fa_resolver
        ):
            with mock.patch.object(
                transcribe_mod, "_read_audio_mono_f32", return_value=([0.0], 1)
            ):
                with mock.patch.object(transcribe_mod, "Qwen3ASRModel") as m_model:
                    m_model.from_pretrained.return_value.transcribe.return_value = []
                    with mock.patch("torch.cuda.empty_cache"):
                        transcribe_mod.run_transcription("/fake.wav", device="cpu")

        m_fa_resolver.assert_called_once_with(None, transcribe_mod.REPO_DIR)
        assert m_model.from_pretrained.call_args.kwargs["forced_aligner"] == fa_sentinel

    def test_no_timestamps_mode_skips_fa_resolution_and_passes_none(
        self,
    ) -> None:
        """Given no timestamps, when transcribing, then skip the aligner."""
        m_fa_resolver = mock.MagicMock(return_value="should-not-be-used")
        with mock.patch.object(
            transcribe_mod, "resolve_forced_aligner_source", m_fa_resolver
        ):
            with mock.patch.object(
                transcribe_mod, "_read_audio_mono_f32", return_value=([0.0], 1)
            ):
                with mock.patch.object(transcribe_mod, "Qwen3ASRModel") as m_model:
                    m_model.from_pretrained.return_value.transcribe.return_value = []
                    with mock.patch("torch.cuda.empty_cache"):
                        transcribe_mod.run_transcription(
                            "/fake.wav", device="cpu", return_timestamps=False
                        )

        m_fa_resolver.assert_not_called()
        assert m_model.from_pretrained.call_args.kwargs["forced_aligner"] is None
