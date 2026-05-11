"""End-to-end tests for the podcast ASR pipeline.

Note: The project directory is asr/asr-pipeline/ (hyphenated, not a
valid Python package name).  We add it to sys.path so that `import preprocess`
etc. resolve when running from the repo root.
"""

import os
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
import soundfile as sf
import torch

# Allow importing modules from this hyphenated directory
_PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

import preprocess  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_test_audio(name: str) -> str | None:
    """Return absolute path to a test audio file, or None if missing."""
    audio_dir = Path(__file__).resolve().parent.parent.parent / "mcp_test" / "audio"
    path = audio_dir / name
    return str(path) if path.exists() else None


# ---------------------------------------------------------------------------
# T01 — Preprocess tests
# ---------------------------------------------------------------------------


class TestPreprocess:
    """Tests for preprocess.py module."""

    def test_preprocess_mp3_to_wav(self):
        """Preprocessing an MP3 should return a 16kHz mono WAV file."""
        input_path = _get_test_audio("english_tech_speech.mp3")
        if not input_path:
            pytest.skip("Test audio not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = preprocess.preprocess_audio(input_path, output_dir=tmpdir)
            assert output_path.endswith(".wav")
            assert os.path.isfile(output_path)
            info = sf.info(output_path)
            assert info.samplerate == 16000
            assert info.channels == 1

    def test_preprocess_already_wav_passthrough(self):
        """Already-16kHz-mono WAV should be returned as-is (idempotent)."""
        input_path = _get_test_audio("english_tech_speech.mp3")
        if not input_path:
            pytest.skip("Test audio not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            wav = preprocess.preprocess_audio(input_path, output_dir=tmpdir)
            # Second pass — same input (now a valid WAV)
            wav2 = preprocess.preprocess_audio(wav, output_dir=tmpdir)
            assert wav == wav2  # should be identical path

    def test_get_audio_duration_reasonable(self):
        """Duration should be within 1s of ground-truth length."""
        input_path = _get_test_audio("english_tech_speech.mp3")
        if not input_path:
            pytest.skip("Test audio not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            wav = preprocess.preprocess_audio(input_path, output_dir=tmpdir)
            dur = preprocess.get_audio_duration(wav)
            # Use soundfile for cross-check
            info = sf.info(wav)
            expected = info.duration
            assert abs(dur - expected) < 1.0

    def test_validate_audio_non_16k(self):
        """validate_audio should report non-16k sample rate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "8k.wav")
            sf.write(path, [0.0] * 8000, 8000)
            result = preprocess.validate_audio(path)
            assert result["sample_rate"] != 16000
            assert result["sample_rate"] == 8000

    def test_validate_audio_16k_mono(self):
        """validate_audio should report 16k mono correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "16k.wav")
            sf.write(path, [0.0] * 16000, 16000)
            result = preprocess.validate_audio(path)
            assert result["sample_rate"] == 16000
            assert result["channels"] == 1
            assert abs(result["duration_sec"] - 1.0) < 0.1

    def test_preprocess_ffmpeg_not_found(self):
        """When ffmpeg is missing, an actionable error should be raised."""
        input_path = _get_test_audio("english_tech_speech.mp3") or "/dev/null"
        with mock.patch("subprocess.run") as m_run:
            m_run.side_effect = FileNotFoundError("ffmpeg not found")
            with pytest.raises(RuntimeError, match="ffmpeg"):
                preprocess.preprocess_audio(input_path)


# ---------------------------------------------------------------------------
# T02 — Diarization tests
# ---------------------------------------------------------------------------

class TestDiarization:
    """Tests for diarize.py module."""

    _imported: bool = False

    @classmethod
    def _import_diarize(cls):
        if not cls._imported:
            import diarize as _d
            cls._d = _d
            cls._imported = True
        return cls._d

    def test_mock_segments_structure(self):
        """Mocked diarization returns properly structured segment dicts."""
        diarize = self._import_diarize()

        # Build a mock output that simulates pyannote 4.x DiarizeOutput
        mock_seg1 = mock.MagicMock()
        mock_seg1.start = 0.5
        mock_seg1.end = 3.2
        mock_seg2 = mock.MagicMock()
        mock_seg2.start = 4.0
        mock_seg2.end = 7.8

        mock_annotation = mock.MagicMock()
        mock_annotation.itertracks.return_value = [
            (mock_seg1, mock.MagicMock(), "SPEAKER_00"),
            (mock_seg2, mock.MagicMock(), "SPEAKER_01"),
        ]

        mock_output = mock.MagicMock()
        mock_output.speaker_diarization = mock_annotation

        with mock.patch.object(diarize.Pipeline, "from_pretrained") as m_fp:
            m_fp.return_value.return_value = mock_output
            result = diarize.run_diarization(
                audio_path="/fake/audio.wav",
                hf_token="dummy_token",
            )

        assert len(result) == 2
        assert result[0] == {"start": 0.5, "end": 3.2, "speaker": "SPEAKER_00"}
        assert result[1] == {"start": 4.0, "end": 7.8, "speaker": "SPEAKER_01"}
        # Ensure segments are sorted by start time
        assert result[0]["start"] < result[1]["start"]

    def test_mock_hf_token_from_env(self):
        """If hf_token not given, it falls back to HF_TOKEN env var."""
        diarize = self._import_diarize()

        mock_output = mock.MagicMock()
        mock_annotation = mock.MagicMock()
        mock_annotation.itertracks.return_value = []
        mock_output.speaker_diarization = mock_annotation

        with mock.patch.object(diarize.Pipeline, "from_pretrained") as m_fp:
            m_fp.return_value.return_value = mock_output
            # fake env
            with mock.patch.dict(os.environ, {"HF_TOKEN": "env_token"}):
                diarize.run_diarization(audio_path="/fake/audio.wav")

            call_kwargs = m_fp.call_args.kwargs
            assert call_kwargs.get("token") == "env_token"

    def test_mock_gpu_cleanup(self):
        """After run_diarization, Pipeline should be deleted and GC called."""
        diarize = self._import_diarize()

        mock_pipeline = mock.MagicMock()
        mock_annotation = mock.MagicMock()
        mock_annotation.itertracks.return_value = []
        mock_output = mock.MagicMock()
        mock_output.speaker_diarization = mock_annotation
        mock_pipeline.return_value = mock_output

        with mock.patch.object(diarize.Pipeline, "from_pretrained") as m_fp:
            m_fp.return_value = mock_pipeline
            with mock.patch("torch.cuda.empty_cache") as m_empty:
                diarize.run_diarization(
                    audio_path="/fake/audio.wav",
                    hf_token="tok",
                )

        # torch.cuda.empty_cache should have been called
        m_empty.assert_called()

    def test_mock_num_speakers_passed(self):
        """num_speakers should be forwarded to pyannote pipeline call."""
        diarize = self._import_diarize()

        mock_annotation = mock.MagicMock()
        mock_annotation.itertracks.return_value = []
        mock_output = mock.MagicMock()
        mock_output.speaker_diarization = mock_annotation
        mock_pipeline = mock.MagicMock()
        mock_pipeline.return_value = mock_output

        with mock.patch.object(diarize.Pipeline, "from_pretrained") as m_fp:
            m_fp.return_value = mock_pipeline
            diarize.run_diarization(
                audio_path="/fake/audio.wav",
                hf_token="tok",
                num_speakers=3,
            )

        # Verify num_speakers was passed
        call_kwargs = mock_pipeline.call_args.kwargs
        assert call_kwargs.get("num_speakers") == 3

    @pytest.mark.skipif(
        not (torch.cuda.is_available() and os.environ.get("HF_TOKEN")),
        reason="GPU + HF_TOKEN required for GPU diarization test",
    )
    def test_gpu_diarization_real(self):
        """Real GPU diarization on test audio."""
        diarize = self._import_diarize()
        input_path = _get_test_audio("english_tech_speech.mp3")
        if not input_path:
            pytest.skip("Test audio not found")

        # Preprocess first
        wav = preprocess.preprocess_audio(input_path)
        try:
            result = diarize.run_diarization(
                audio_path=wav,
                hf_token=os.environ["HF_TOKEN"],
                device="cuda",
            )
        except Exception as exc:
            msg = str(exc).lower()
            if any(kw in msg for kw in (
                "401", "403", "unauthorized", "gatedrepo",
                "authentication failed",
            )):
                pytest.skip(
                    "pyannote auth failed — ensure you have accepted terms for "
                    "both pyannote/speaker-diarization-3.1 AND "
                    "pyannote/speaker-diarization-community-1"
                )
            raise

        assert len(result) >= 1
        for seg in result:
            assert "start" in seg
            assert "end" in seg
            assert "speaker" in seg
            assert seg["start"] < seg["end"]
        # check sorted
        starts = [s["start"] for s in result]
        assert starts == sorted(starts)


# ---------------------------------------------------------------------------
# T03 — Transcription tests
# ---------------------------------------------------------------------------

class TestTranscription:
    """Tests for transcribe.py module."""

    _imported: bool = False

    @classmethod
    def _import_transcribe(cls):
        if not cls._imported:
            import transcribe as _t
            cls._t = _t
            cls._imported = True
        return cls._t

    def _make_mock_chunk(self, text, lang, words):
        """Build a mock ASRTranscription chunk."""
        chunk = mock.MagicMock()
        chunk.text = text
        chunk.language = lang
        chunk.time_stamps = [
            mock.MagicMock(text=w, start_time=s, end_time=e)
            for w, s, e in words
        ]
        return chunk

    def test_mock_combines_chunks(self):
        """Multiple chunks should be merged into a single result dict."""
        transcribe = self._import_transcribe()

        with mock.patch("transcribe.Qwen3ASRModel") as m_model:
            m_model.from_pretrained.return_value.transcribe.return_value = [
                self._make_mock_chunk("Hello world", "English", [
                    ("Hello", 0.0, 0.5), ("world", 0.6, 1.0),
                ]),
                self._make_mock_chunk("from Python", "English", [
                    ("from", 1.2, 1.5), ("Python", 1.6, 2.0),
                ]),
            ]
            result = transcribe.run_transcription(
                audio_path="/fake/audio.wav",
                language="English",
            )

        assert result["text"] == "Hello world from Python"
        assert result["language"] == "English"
        assert len(result["words"]) == 4
        assert result["words"][0] == {"word": "Hello", "start": 0.0, "end": 0.5}
        assert result["words"][-1] == {"word": "Python", "start": 1.6, "end": 2.0}

    def test_mock_context_passed(self):
        """Context string should be forwarded to model.transcribe()."""
        transcribe = self._import_transcribe()

        with mock.patch("transcribe.Qwen3ASRModel") as m_model:
            m_model.from_pretrained.return_value.transcribe.return_value = []
            transcribe.run_transcription(
                audio_path="/fake/audio.wav",
                context="EBITDA ROI non-GAAP",
            )

        call_kwargs = m_model.from_pretrained.return_value.transcribe.call_args.kwargs
        assert call_kwargs.get("context") == "EBITDA ROI non-GAAP"

    def test_mock_gpu_cleanup(self):
        """After run_transcription, model should be deleted and GC called."""
        transcribe = self._import_transcribe()

        with mock.patch("transcribe.Qwen3ASRModel") as m_model:
            m_model.from_pretrained.return_value.transcribe.return_value = []
            with mock.patch("torch.cuda.empty_cache") as m_empty:
                transcribe.run_transcription(audio_path="/fake/audio.wav")

        m_empty.assert_called()

    def test_mock_empty_result(self):
        """Silent / empty audio should return empty text and words."""
        transcribe = self._import_transcribe()

        with mock.patch("transcribe.Qwen3ASRModel") as m_model:
            m_model.from_pretrained.return_value.transcribe.return_value = []
            result = transcribe.run_transcription(
                audio_path="/fake/audio.wav",
            )

        assert result["text"] == ""
        assert result["words"] == []

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="GPU required")
    def test_gpu_real_english(self):
        """Real GPU transcription on English test audio with timestamps."""
        transcribe = self._import_transcribe()
        input_path = _get_test_audio("english_tech_speech.mp3")
        if not input_path:
            pytest.skip("Test audio not found")

        wav = preprocess.preprocess_audio(input_path)
        result = transcribe.run_transcription(
            audio_path=wav,
            language="English",
            device="cuda:0",
        )
        assert len(result["words"]) > 0
        for w in result["words"]:
            assert "word" in w
            assert "start" in w
            assert "end" in w
            assert w["start"] < w["end"]
        assert result["language"] == "English"

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="GPU required")
    def test_gpu_real_chinese(self):
        """Real GPU transcription on Chinese test audio with timestamps."""
        transcribe = self._import_transcribe()
        input_path = _get_test_audio("chinese_tech_speech.mp3")
        if not input_path:
            pytest.skip("Test audio not found")

        wav = preprocess.preprocess_audio(input_path)
        result = transcribe.run_transcription(
            audio_path=wav,
            language="Chinese",
            device="cuda:0",
        )
        assert len(result["words"]) > 0
        assert result["language"] == "Chinese"


# ---------------------------------------------------------------------------
# T04 — Merge & output tests
# ---------------------------------------------------------------------------

class TestMerge:
    """Tests for merge.py module."""

    _imported: bool = False

    @classmethod
    def _import_merge(cls):
        if not cls._imported:
            import merge as _m
            cls._m = _m
            cls._imported = True
        return cls._m

    _spk_segs = [
        {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_01"},
        {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_02"},
        {"start": 12.0, "end": 15.0, "speaker": "SPEAKER_01"},
    ]
    _asr_words = [
        {"word": "Hello", "start": 0.2, "end": 0.8},
        {"word": "world", "start": 1.0, "end": 1.4},
        {"word": "this", "start": 5.2, "end": 5.5},
        {"word": "is", "start": 5.6, "end": 5.8},
        {"word": "test", "start": 6.0, "end": 6.3},
        {"word": "again", "start": 12.2, "end": 12.8},
    ]

    def test_merge_basic(self):
        """Words assigned to correct speakers based on midpoint overlap."""
        merge = self._import_merge()
        result = merge.merge_diarization_asr(self._spk_segs, self._asr_words)

        # Expect segments for both speakers
        speakers = [s["speaker"] for s in result]
        assert "SPEAKER_01" in speakers
        assert "SPEAKER_02" in speakers
        # Each segment should have text and words
        for seg in result:
            assert "speaker" in seg
            assert "start" in seg
            assert "end" in seg
            assert "text" in seg
            assert "words" in seg
            assert len(seg["words"]) > 0

    def test_merge_speaker_grouping(self):
        """Consecutive words from same speaker should be in one segment."""
        merge = self._import_merge()
        result = merge.merge_diarization_asr(self._spk_segs, self._asr_words)

        # Words "this", "is", "test" all fall in SPEAKER_02's segment (5-10s)
        spk02_seg = [s for s in result if s["speaker"] == "SPEAKER_02"][0]
        assert len(spk02_seg["words"]) == 3
        assert spk02_seg["text"] == "this is test"

    def test_merge_no_diarization(self):
        """Without diarization, all words go to SPEAKER_00."""
        merge = self._import_merge()
        result = merge.merge_diarization_asr([], self._asr_words)

        assert len(result) >= 1
        for seg in result:
            assert seg["speaker"] == "SPEAKER_00"

    def test_json_output_structure(self):
        """JSON output includes metadata and segments with words."""
        merge = self._import_merge()
        segs = merge.merge_diarization_asr(self._spk_segs, self._asr_words)

        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "test.json")
            merge.to_json(segs, out, duration_sec=20.0, language="English")

            with open(out) as f:
                data = json.load(f)

        assert "metadata" in data
        assert data["metadata"]["duration_sec"] == 20.0
        assert data["metadata"]["language"] == "English"
        assert "segments" in data
        assert len(data["segments"]) >= 1
        assert "words" in data["segments"][0]

    def test_srt_format(self):
        """SRT output uses correct index, timestamp, and speaker prefix."""
        merge = self._import_merge()
        segs = merge.merge_diarization_asr(self._spk_segs, self._asr_words)

        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "test.srt")
            merge.to_srt(segs, out)

            with open(out) as f:
                content = f.read()

        lines = content.strip().split("\n")
        # Should have index lines
        assert "1" in lines
        # Timestamp format: HH:MM:SS,mmm
        assert any("-->" in line for line in lines)
        # Speaker prefix
        assert any("[SPEAKER_" in line for line in lines)

    def test_txt_format(self):
        """TXT output has speaker labels per line."""
        merge = self._import_merge()
        segs = merge.merge_diarization_asr(self._spk_segs, self._asr_words)

        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "test.txt")
            merge.to_txt(segs, out)

            with open(out) as f:
                lines = f.readlines()

        for line in lines:
            line = line.strip()
            if line:
                assert line.startswith("[SPEAKER_")

    def test_merge_empty_words(self):
        """Empty words list should produce empty segments."""
        merge = self._import_merge()
        result = merge.merge_diarization_asr(self._spk_segs, [])
        assert result == []


# ---------------------------------------------------------------------------
# T05 — CLI / pipeline tests
# ---------------------------------------------------------------------------

class TestPipelineCLI:
    """Tests for pipeline.py CLI entry point."""

    @classmethod
    def _pipeline_main(cls, *args) -> int:
        """Invoke pipeline.main() with given CLI args, return exit code."""
        import pipeline as _p
        return _p.main(list(args))

    def test_version(self, capsys):
        """--version should print version and exit."""
        with pytest.raises(SystemExit) as exc:
            self._pipeline_main("--version")
        assert exc.value.code == 0

    def test_help(self, capsys):
        """--help should print usage and exit."""
        with pytest.raises(SystemExit) as exc:
            self._pipeline_main("--help")
        assert exc.value.code == 0

    def test_missing_file(self):
        """Non-existent input should exit with code 2."""
        code = self._pipeline_main("/nonexistent/file.wav")
        assert code == 2

    def test_no_diarize_flag_accepted(self):
        """--no-diarize should not raise arg errors."""
        input_path = _get_test_audio("english_tech_speech.mp3")
        if not input_path:
            pytest.skip("Test audio not found")

        with mock.patch("pipeline._run_single") as m_run:
            m_run.return_value = 0
            code = self._pipeline_main(input_path, "--no-diarize")
        assert code == 0

    def test_context_passed_to_run_transcription(self):
        """--context should be forwarded to transcription."""
        import pipeline as _p

        with mock.patch.object(_p._transcribe_mod, "run_transcription") as m_tr:
            m_tr.return_value = {"text": "", "language": "English", "words": []}
            with mock.patch("pipeline.preprocess.preprocess_audio") as m_pp:
                m_pp.return_value = "/fake.wav"
                with mock.patch("pipeline.preprocess.get_audio_duration") as m_dur:
                    m_dur.return_value = 10.0
                    _p._run_single(
                        input_path="/fake.mp3",
                        output_dir="/tmp",
                        language="English",
                        context="EBITDA ROI",
                        num_speakers=None,
                        no_diarize=True,
                        formats={"json"},
                        device="cpu",
                        hf_token=None,
                    )
        call_kwargs = m_tr.call_args.kwargs
        assert call_kwargs.get("context") == "EBITDA ROI"
