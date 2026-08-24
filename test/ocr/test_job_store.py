from __future__ import annotations

import errno
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ocr import job_store as job_store_module
from ocr.job_manifest import ChunkStatus, JobManifest
from ocr.job_store import JobSourceError, JobStore


def _write_pdf(path: Path, *, page_count: int) -> None:
    page_objects = tuple(3 + index * 2 for index in range(page_count))
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids ["
        + " ".join(f"{page_object} 0 R" for page_object in page_objects)
        + f"] /Count {page_count} >>",
    ]
    for page_object in page_objects:
        objects.extend(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Contents {page_object + 1} 0 R >>",
                "<< /Length 0 >>\nstream\n\nendstream",
            )
        )
    encoded_objects = tuple(
        f"{index} 0 obj\n{value}\nendobj\n".encode("ascii")
        for index, value in enumerate(objects, start=1)
    )
    header = b"%PDF-1.4\n"
    offsets: list[int] = []
    position = len(header)
    for encoded in encoded_objects:
        offsets.append(position)
        position += len(encoded)
    xref = b"xref\n0 " + str(len(encoded_objects) + 1).encode("ascii") + b"\n0000000000 65535 f \n"
    xref += b"".join(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets)
    trailer = (
        b"trailer\n<< /Size "
        + str(len(encoded_objects) + 1).encode("ascii")
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(position).encode("ascii")
        + b"\n%%EOF\n"
    )
    path.write_bytes(header + b"".join(encoded_objects) + xref + trailer)


class TestJobStore(TestCase):
    def test_create_rejects_unsupported_source_without_creating_a_job(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "unsupported.txt"
            source.write_text("not an image", encoding="utf-8")
            store = JobStore(root / "jobs")

            with self.assertRaisesRegex(JobSourceError, "unsupported file type"):
                store.create(source)

            self.assertFalse((root / "jobs").exists())

    def test_create_rejects_corrupt_pdf_and_removes_partial_job(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "corrupt.pdf"
            source.write_bytes(b"%PDF-1.7\nnot a valid PDF\n")
            jobs = root / "jobs"
            store = JobStore(jobs)

            with self.assertRaisesRegex(JobSourceError, "cannot stage"):
                store.create(source)

            self.assertEqual(tuple(jobs.iterdir()), ())

    def test_create_pdf_job_covers_all_chunk_boundaries_exactly_once(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root / "jobs")

            for page_count in (1, 23, 24, 25, 48, 49, 96):
                with self.subTest(page_count=page_count):
                    source = root / f"{page_count}-pages.pdf"
                    _write_pdf(source, page_count=page_count)

                    manifest = store.create(source)

                    self.assertEqual(manifest.page_count, page_count)
                    self.assertEqual(
                        tuple(
                            page
                            for chunk in manifest.chunks
                            for page in chunk.source_pages
                        ),
                        tuple(range(1, page_count + 1)),
                    )
                    self.assertEqual(
                        len(manifest.chunks),
                        (page_count + 23) // 24,
                    )

    def test_create_pdf_job_uses_one_chunk_at_twenty_four_pages(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            source = temporary_root / "twenty-four.pdf"
            _write_pdf(source, page_count=24)
            store = JobStore(temporary_root / "jobs")

            # When
            manifest = store.create(source)

            # Then
            self.assertEqual(len(manifest.chunks), 1)
            self.assertEqual(manifest.chunks[0].source_pages, tuple(range(1, 25)))
            self.assertEqual(manifest.chunks[0].staged_path, Path("chunks/chunk-001.pdf"))

    def test_create_pdf_job_splits_twenty_five_pages_into_staged_chunks(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            source = temporary_root / "twenty-five.pdf"
            _write_pdf(source, page_count=25)
            store = JobStore(temporary_root / "jobs")

            # When
            manifest = store.create(source)

            # Then
            self.assertEqual(
                tuple(chunk.source_pages for chunk in manifest.chunks),
                (tuple(range(1, 25)), (25,)),
            )
            self.assertEqual(
                tuple(chunk.staged_path for chunk in manifest.chunks),
                (Path("chunks/chunk-001.pdf"), Path("chunks/chunk-002.pdf")),
            )
            self.assertTrue(
                all(
                    (store.job_directory(manifest.job_id) / chunk.staged_path).is_file()
                    for chunk in manifest.chunks
                )
            )

    def test_create_image_job_stages_one_relative_chunk(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            source = temporary_root / "scan.png"
            source.write_bytes(b"png-fixture")
            store = JobStore(temporary_root / "jobs")

            # When
            manifest = store.create(source)

            # Then
            chunk = manifest.chunks[0]
            job_directory = store.job_directory(manifest.job_id)
            self.assertEqual(len(manifest.chunks), 1)
            self.assertEqual(chunk.source_pages, (1,))
            self.assertEqual(chunk.staged_path, Path("chunks/chunk-001.png"))
            self.assertEqual(manifest.input_path, Path("input/scan.png"))
            self.assertEqual(chunk.artifact_path, Path("chunks/chunk-001.md"))
            self.assertTrue((job_directory / "manifest.json").is_file())
            self.assertTrue((job_directory / "input").is_dir())
            self.assertTrue((job_directory / "chunks").is_dir())

    def test_recover_reuses_completed_chunk_only_when_digest_matches_artifact(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            source = temporary_root / "scan.png"
            source.write_bytes(b"png-fixture")
            store = JobStore(temporary_root / "jobs")
            manifest = store.create(source)
            completed = store.complete_chunk(manifest.job_id, chunk_index=1, markdown="# done\n")

            # When
            recovered = store.recover(completed.job_id)

            # Then
            self.assertIs(recovered.chunks[0].status, ChunkStatus.COMPLETED)
            self.assertEqual(recovered.chunks[0].artifact_digest, completed.chunks[0].artifact_digest)

    def test_recover_returns_missing_completed_artifact_to_pending(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            source = temporary_root / "scan.png"
            source.write_bytes(b"png-fixture")
            store = JobStore(temporary_root / "jobs")
            manifest = store.create(source)
            completed = store.complete_chunk(manifest.job_id, chunk_index=1, markdown="# done\n")
            artifact = store.job_directory(completed.job_id) / completed.chunks[0].artifact_path
            artifact.unlink()

            # When
            recovered = store.recover(completed.job_id)

            # Then
            self.assertIs(recovered.chunks[0].status, ChunkStatus.PENDING)
            self.assertIsNone(recovered.chunks[0].artifact_digest)
            self.assertIs(store.load(completed.job_id).chunks[0].status, ChunkStatus.PENDING)

    def test_recover_returns_corrupt_completed_artifact_to_pending(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            source = temporary_root / "scan.png"
            source.write_bytes(b"png-fixture")
            store = JobStore(temporary_root / "jobs")
            manifest = store.create(source)
            completed = store.complete_chunk(manifest.job_id, chunk_index=1, markdown="# done\n")
            artifact = store.job_directory(completed.job_id) / completed.chunks[0].artifact_path
            artifact.write_text("different", encoding="utf-8")

            # When
            recovered = store.recover(completed.job_id)

            # Then
            self.assertIs(recovered.chunks[0].status, ChunkStatus.PENDING)
            self.assertIsNone(recovered.chunks[0].artifact_digest)
            self.assertIs(store.load(completed.job_id).chunks[0].status, ChunkStatus.PENDING)

    def test_recover_returns_interrupted_running_chunk_to_pending(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            source = temporary_root / "scan.png"
            source.write_bytes(b"png-fixture")
            store = JobStore(temporary_root / "jobs")
            manifest = store.create(source)
            running = store.mark_running(manifest.job_id, chunk_index=1)

            # When
            recovered = store.recover(running.job_id)

            # Then
            self.assertIs(recovered.chunks[0].status, ChunkStatus.PENDING)
            self.assertIs(store.load(running.job_id).chunks[0].status, ChunkStatus.PENDING)

    def test_complete_chunk_writes_artifact_before_completed_manifest(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            source = temporary_root / "scan.png"
            source.write_bytes(b"png-fixture")
            store = JobStore(temporary_root / "jobs")
            manifest = store.create(source)
            artifact = store.job_directory(manifest.job_id) / Path("chunks/chunk-001.md")
            observed_artifact_durability: list[bool] = []
            original_write = store._write_manifest

            def observe_manifest(candidate: JobManifest) -> None:
                if candidate.chunks[0].status is ChunkStatus.COMPLETED:
                    observed_artifact_durability.append(
                        artifact.is_file() and artifact.read_text(encoding="utf-8") == "# done\n"
                    )
                original_write(candidate)

            # When
            with patch.object(store, "_write_manifest", side_effect=observe_manifest):
                store.complete_chunk(manifest.job_id, chunk_index=1, markdown="# done\n")

            # Then
            self.assertEqual(observed_artifact_durability, [True])

    def test_artifact_disk_full_does_not_publish_completed_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scan.png"
            source.write_bytes(b"png-fixture")
            store = JobStore(root / "jobs")
            manifest = store.create(source)
            running = store.mark_running(manifest.job_id, chunk_index=1)
            original_write = job_store_module.atomic_write_text

            def fail_artifact(path: Path, text: str) -> None:
                if path.suffix == ".md":
                    raise OSError(errno.ENOSPC, "simulated output disk full")
                original_write(path, text)

            with patch.object(
                job_store_module,
                "atomic_write_text",
                side_effect=fail_artifact,
            ), self.assertRaisesRegex(OSError, "output disk full"):
                store.complete_chunk(
                    running.job_id,
                    chunk_index=1,
                    markdown="# partial\n",
                )

            self.assertIs(store.load(running.job_id).chunks[0].status, ChunkStatus.RUNNING)
            recovered = store.recover(running.job_id)
            self.assertIs(recovered.chunks[0].status, ChunkStatus.PENDING)
            artifact = store.job_directory(running.job_id) / running.chunks[0].artifact_path
            self.assertFalse(artifact.exists())

    def test_manifest_write_failure_never_marks_artifact_completed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scan.png"
            source.write_bytes(b"png-fixture")
            store = JobStore(root / "jobs")
            manifest = store.create(source)
            running = store.mark_running(manifest.job_id, chunk_index=1)

            with patch.object(
                store,
                "_write_manifest",
                side_effect=OSError(errno.ENOSPC, "simulated manifest disk full"),
            ), self.assertRaisesRegex(OSError, "manifest disk full"):
                store.complete_chunk(
                    running.job_id,
                    chunk_index=1,
                    markdown="# durable but unpublished\n",
                )

            self.assertIs(store.load(running.job_id).chunks[0].status, ChunkStatus.RUNNING)
            recovered = store.recover(running.job_id)
            self.assertIs(recovered.chunks[0].status, ChunkStatus.PENDING)

    def test_read_only_job_root_fails_without_partial_job(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("permission semantics cannot be asserted as root")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scan.png"
            source.write_bytes(b"png-fixture")
            jobs = root / "jobs"
            jobs.mkdir()
            jobs.chmod(0o500)
            try:
                with self.assertRaises(PermissionError):
                    JobStore(jobs).create(source)
                self.assertEqual(tuple(jobs.iterdir()), ())
            finally:
                jobs.chmod(0o700)
