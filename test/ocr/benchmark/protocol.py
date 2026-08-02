"""Typed response parsing for the OCR benchmark harness."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class RestFailure:
    """A network or protocol failure preserved for benchmark analysis."""

    message: str
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class Submission:
    """Server acknowledgement of a submitted OCR job."""

    job_id: str
    total_pages: int


@dataclass(frozen=True, slots=True)
class Progress:
    """One server-side OCR job observation."""

    status: str
    current: int
    total: int
    error: str | None


@dataclass(frozen=True, slots=True)
class ReturnedPage:
    """One page returned by the OCR server with its staged-PDF index."""

    page_index: int
    markdown: str


@dataclass(frozen=True, slots=True)
class OcrResult:
    """OCR output returned for one staged page subset."""

    page_count: int
    pages: tuple[ReturnedPage, ...]


@dataclass(frozen=True, slots=True)
class HttpReply:
    """Decoded JSON HTTP reply or serializable transport/protocol failure."""

    status_code: int
    payload: JsonObject | None
    error: str | None


@dataclass(frozen=True, slots=True)
class ResponseSchemaError(Exception):
    """Raised only while parsing a malformed OCR REST response."""

    field: str
    expected: str

    def __str__(self) -> str:
        return f"response field {self.field!r} must be {self.expected}"


def decode_reply(status_code: int, body: bytes, fallback_error: str | None = None) -> HttpReply:
    """Decode a JSON object reply, retaining malformed-response details as data."""
    try:
        decoded = json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return HttpReply(status_code=status_code, payload=None, error=fallback_error or str(error))
    if not isinstance(decoded, dict):
        return HttpReply(status_code=status_code, payload=None, error="JSON response is not an object")
    return HttpReply(
        status_code=status_code,
        payload={str(key): value for key, value in decoded.items()},
        error=fallback_error,
    )


def submission_from_reply(reply: HttpReply) -> Submission | RestFailure:
    """Parse submit endpoint data without allowing malformed JSON to escape the run."""
    payload = _usable_payload(reply)
    if isinstance(payload, RestFailure):
        return payload
    try:
        return Submission(
            job_id=_required_string(payload, "job_id"),
            total_pages=_required_int(payload, "total_pages"),
        )
    except ResponseSchemaError as error:
        return RestFailure(message=str(error), status_code=reply.status_code)


def progress_from_reply(reply: HttpReply) -> Progress | RestFailure:
    """Parse job progress into a typed snapshot or benchmark failure data."""
    payload = _usable_payload(reply)
    if isinstance(payload, RestFailure):
        return payload
    progress = payload.get("progress")
    if not isinstance(progress, dict):
        return RestFailure(message="status response lacks progress", status_code=reply.status_code)
    try:
        return Progress(
            status=_required_string(payload, "status"),
            current=_required_int(progress, "current"),
            total=_required_int(progress, "total"),
            error=_optional_string(payload, "error"),
        )
    except ResponseSchemaError as error:
        return RestFailure(message=str(error), status_code=reply.status_code)


def result_from_reply(reply: HttpReply) -> OcrResult | RestFailure:
    """Parse the completed-page payload from the OCR result endpoint."""
    payload = _usable_payload(reply)
    if isinstance(payload, RestFailure):
        return payload
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return RestFailure(message="result response lacks pages", status_code=reply.status_code)
    result_pages: list[ReturnedPage] = []
    try:
        for page in pages:
            if not isinstance(page, dict):
                return RestFailure(message="result page is not an object", status_code=reply.status_code)
            result_pages.append(
                ReturnedPage(
                    page_index=_required_int(page, "page_index"),
                    markdown=_required_string(page, "markdown"),
                )
            )
        return OcrResult(
            page_count=_required_int(payload, "page_count"),
            pages=tuple(result_pages),
        )
    except ResponseSchemaError as error:
        return RestFailure(message=str(error), status_code=reply.status_code)


def optional_number(payload: JsonObject, field: str) -> float | None:
    """Read a numeric health counter when this server version exposes it."""
    value = payload.get(field)
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _usable_payload(reply: HttpReply) -> JsonObject | RestFailure:
    if reply.error is not None or reply.payload is None or reply.status_code >= 400:
        return RestFailure(message=reply.error or f"HTTP {reply.status_code}", status_code=reply.status_code)
    return reply.payload


def _required_string(payload: JsonObject, field: str) -> str:
    value = payload.get(field)
    if isinstance(value, str):
        return value
    raise ResponseSchemaError(field=field, expected="a string")


def _required_int(payload: JsonObject, field: str) -> int:
    value = payload.get(field)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ResponseSchemaError(field=field, expected="an integer")


def _optional_string(payload: JsonObject, field: str) -> str | None:
    value = payload.get(field)
    return value if isinstance(value, str) else None
