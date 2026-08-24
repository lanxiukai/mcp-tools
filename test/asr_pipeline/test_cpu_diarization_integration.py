"""Opt-in real pyannote checks that deliberately keep GPU usage at zero."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIRECTORY = REPOSITORY_ROOT / "asr-pipeline"
sys.path.insert(0, str(PIPELINE_DIRECTORY))

import diarize  # noqa: E402


SINGLE_SPEAKER = REPOSITORY_ROOT / "mcp-tool-test/smoke-test/asr_smoke_test.wav"
SECOND_SPEAKER = REPOSITORY_ROOT / "mcp-tool-test/asr/daily/zh_en_dialogue/D13_934.wav"
SHORT_DIALOGUE = REPOSITORY_ROOT / "mcp-tool-test/asr/daily/zh_en_dialogue/D12_760.wav"


@unittest.skipUnless(
    os.environ.get("MCP_TOOLS_DIARIZATION_CPU_INTEGRATION") == "1",
    "set MCP_TOOLS_DIARIZATION_CPU_INTEGRATION=1 for real CPU diarization",
)
class CpuDiarizationIntegrationTests(unittest.TestCase):
    def _assert_timeline(self, segments: list[dict], expected_speakers: int) -> None:
        self.assertTrue(segments)
        starts = [segment["start"] for segment in segments]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(
            all(segment["start"] < segment["end"] for segment in segments)
        )
        self.assertEqual(
            len({segment["speaker"] for segment in segments}),
            expected_speakers,
        )

    def test_real_one_and_two_speaker_timelines_on_cpu(self) -> None:
        self.assertTrue(SINGLE_SPEAKER.is_file())
        self.assertTrue(SECOND_SPEAKER.is_file())
        metrics: dict[str, dict[str, float | int]] = {}
        with tempfile.TemporaryDirectory() as directory:
            two_speaker = Path(directory) / "two-speaker-48s.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-stream_loop",
                    "4",
                    "-i",
                    str(SINGLE_SPEAKER),
                    "-stream_loop",
                    "7",
                    "-i",
                    str(SECOND_SPEAKER),
                    "-filter_complex",
                    "[0:a]atrim=0:24,aresample=16000,aformat=channel_layouts=mono[first];"
                    "[1:a]atrim=0:24,aresample=16000,aformat=channel_layouts=mono[second];"
                    "[first][second]concat=n=2:v=0:a=1[out]",
                    "-map",
                    "[out]",
                    str(two_speaker),
                ],
                check=True,
            )

            for name, source, expected_speakers in (
                ("one_speaker", SINGLE_SPEAKER, 1),
                ("two_speaker", two_speaker, 2),
            ):
                with self.subTest(name=name):
                    started = time.perf_counter()
                    segments = diarize.run_diarization(
                        str(source),
                        num_speakers=expected_speakers,
                        device="cpu",
                    )
                    self._assert_timeline(segments, expected_speakers)
                    metrics[name] = {
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                        "segments": len(segments),
                        "speakers": expected_speakers,
                    }
        print("DIARIZATION_CPU_METRICS=" + json.dumps(metrics, sort_keys=True))

    def test_silence_returns_no_speaker_segments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            silence = Path(directory) / "silence.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=16000:cl=mono",
                    "-t",
                    "3",
                    str(silence),
                ],
                check=True,
            )
            self.assertEqual(
                diarize.run_diarization(str(silence), device="cpu"),
                [],
            )

    def test_unachievable_exact_count_does_not_fabricate_a_speaker(self) -> None:
        self.assertTrue(SHORT_DIALOGUE.is_file())
        segments = diarize.run_diarization(
            str(SHORT_DIALOGUE),
            num_speakers=2,
            device="cpu",
        )

        self.assertEqual(len({segment["speaker"] for segment in segments}), 1)


if __name__ == "__main__":
    unittest.main()
