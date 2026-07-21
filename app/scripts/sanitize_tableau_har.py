from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DIAGNOSTICS_ROOT = Path("data/diagnostics/faa_tableau")
RAW_DIRECTORY = DIAGNOSTICS_ROOT / "raw"
SANITIZED_DIRECTORY = DIAGNOSTICS_ROOT / "sanitized"
_REDACTED = "[redacted]"
_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-csrf-token",
    "x-xsrf-token",
}
_SESSION_PATH = re.compile(r"(?i)(/sessions/)[^/?#]+")
_IDENTIFIER_PATH = re.compile(
    r"(?i)(/(?:sessionid|requestid|browserid|csrf|xsrf|token)/)[^/?#]+"
)
_TELEMETRY = re.compile(r"(?i)(akam|go-mpulse|boomr|beacon|telemetry)")
_TABLEAU_MARKERS = (
    "prebootstrap",
    "bootstrapsession",
    "sheetid",
    "sessionid",
    "getsessioninfo",
    "getsessionsheet",
    "metadata",
    "mark",
    "commands",
)


class HarSanitizationError(ValueError):
    """A stable error that never includes raw HAR content."""


@dataclass(frozen=True)
class HarSanitizationReport:
    input_file: str
    input_sha256: str
    input_byte_size: int
    output_file: str
    output_sha256: str
    output_byte_size: int
    entries: int
    removed_headers: int
    redacted_values: int
    removed_data_categories: tuple[str, ...]


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _sanitize_url(value: str, counters: dict[str, int]) -> str:
    parts = urlsplit(value)
    if _TELEMETRY.search(parts.path):
        counters["redacted_values"] += 1
        return urlunsplit((parts.scheme, parts.netloc, "/[telemetry-redacted]", "", ""))
    path = _SESSION_PATH.sub(r"\1[redacted]", parts.path)
    path = _IDENTIFIER_PATH.sub(r"\1[redacted]", path)
    query = []
    for name, item in parse_qsl(parts.query, keep_blank_values=True):
        query.append((name, _REDACTED))
        if item != _REDACTED:
            counters["redacted_values"] += 1
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))


def _header_names(headers: Any, counters: dict[str, int]) -> list[str]:
    if not isinstance(headers, list):
        return []
    names: list[str] = []
    for header in headers:
        if not isinstance(header, dict) or not isinstance(header.get("name"), str):
            continue
        name = header["name"]
        names.append(name)
        if name.lower() in _SENSITIVE_HEADERS:
            counters["removed_headers"] += 1
        elif header.get("value"):
            counters["redacted_values"] += 1
    return names


def _body_fields(post_data: Any, counters: dict[str, int]) -> tuple[str | None, list[str]]:
    if not isinstance(post_data, dict):
        return None, []
    media_type = post_data.get("mimeType") if isinstance(post_data.get("mimeType"), str) else None
    names: list[str] = []
    params = post_data.get("params")
    if isinstance(params, list):
        names = [item["name"] for item in params if isinstance(item, dict) and isinstance(item.get("name"), str)]
        counters["redacted_values"] += len(names)
    elif isinstance(post_data.get("text"), str):
        names = [name for name, _ in parse_qsl(post_data["text"], keep_blank_values=True)]
        counters["redacted_values"] += len(names)
    return media_type, names


def _response_markers(content: Any) -> list[str]:
    if not isinstance(content, dict) or not isinstance(content.get("text"), str):
        return []
    lowered = content["text"].lower()
    return [marker for marker in _TABLEAU_MARKERS if marker in lowered]


def _request_category(url: str, resource_type: Any) -> str:
    lowered = url.lower()
    if _TELEMETRY.search(lowered):
        return "telemetry"
    if "bootstrapsession" in lowered or "/sessions/" in lowered:
        return "session_or_bootstrap"
    if "commands" in lowered or "getsession" in lowered:
        return "worksheet_command"
    if "prebootstrap" in lowered or lowered.endswith((".js", ".css")):
        return "static_asset"
    if resource_type == "document":
        return "navigation"
    return "other"


def _sanitize_entry(entry: dict[str, Any], sequence: int, counters: dict[str, int]) -> dict[str, Any]:
    request = entry.get("request")
    response = entry.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise HarSanitizationError("HAR entry request/response structure is invalid.")
    request_url = request.get("url")
    method = request.get("method")
    if not isinstance(request_url, str) or not isinstance(method, str):
        raise HarSanitizationError("HAR request method or URL is invalid.")
    body_media_type, body_fields = _body_fields(request.get("postData"), counters)
    content = response.get("content") if isinstance(response.get("content"), dict) else {}
    redirect = response.get("redirectURL")
    return {
        "sequence": sequence,
        "category": _request_category(request_url, entry.get("_resourceType")),
        "startedDateTime": entry.get("startedDateTime"),
        "time_ms": entry.get("time"),
        "initiator_type": (entry.get("_initiator") or {}).get("type")
        if isinstance(entry.get("_initiator"), dict)
        else None,
        "request": {
            "method": method,
            "url": _sanitize_url(request_url, counters),
            "query_parameter_names": [
                item.get("name")
                for item in request.get("queryString", [])
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            ],
            "header_names": _header_names(request.get("headers"), counters),
            "body_media_type": body_media_type,
            "body_field_names": body_fields,
        },
        "response": {
            "status": response.get("status"),
            "media_type": content.get("mimeType"),
            "byte_size": content.get("size", response.get("bodySize")),
            "redirect_url": _sanitize_url(redirect, counters)
            if isinstance(redirect, str) and redirect
            else None,
            "header_names": _header_names(response.get("headers"), counters),
            "tableau_structure_markers": _response_markers(content),
        },
    }


def sanitize_tableau_har(
    input_path: Path,
    *,
    diagnostics_root: Path = DIAGNOSTICS_ROOT,
    allow_outside_diagnostics: bool = False,
) -> HarSanitizationReport:
    source = input_path.resolve()
    expected_raw = (diagnostics_root / "raw").resolve()
    if not allow_outside_diagnostics and not _is_within(source, expected_raw):
        raise HarSanitizationError("Input HAR must be inside the expected raw diagnostics directory.")
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise HarSanitizationError("Input HAR could not be read.") from exc
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarSanitizationError("Input HAR is not valid JSON.") from exc
    entries = parsed.get("log", {}).get("entries") if isinstance(parsed, dict) else None
    if not isinstance(entries, list):
        raise HarSanitizationError("Input JSON is not a valid HAR structure.")

    counters = {"removed_headers": 0, "redacted_values": 0}
    sanitized_entries = []
    removed_categories = {
        "request_and_response_bodies",
        "header_values",
        "query_values",
        "cookies_and_authorization",
        "session_and_request_identifiers",
        "browser_and_edge_telemetry",
        "client_ip_and_machine_values",
    }
    for sequence, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise HarSanitizationError("HAR entry structure is invalid.")
        sanitized_entries.append(_sanitize_entry(entry, sequence, counters))

    output = {
        "format": "rwi-tableau-har-analysis-v1",
        "entries": sanitized_entries,
    }
    output_bytes = (json.dumps(output, indent=2, sort_keys=True) + "\n").encode("utf-8")
    output_directory = (diagnostics_root / "sanitized").resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / f"{source.stem}.sanitized.json"
    output_path.write_bytes(output_bytes)
    return HarSanitizationReport(
        input_file=source.name,
        input_sha256=_digest(raw),
        input_byte_size=len(raw),
        output_file=output_path.name,
        output_sha256=_digest(output_bytes),
        output_byte_size=len(output_bytes),
        entries=len(entries),
        removed_headers=counters["removed_headers"],
        redacted_values=counters["redacted_values"],
        removed_data_categories=tuple(sorted(removed_categories)),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sanitize one local Tableau HAR without network access.")
    parser.add_argument("raw_har", type=Path)
    parser.add_argument("--allow-outside-diagnostics", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = sanitize_tableau_har(
            args.raw_har,
            allow_outside_diagnostics=args.allow_outside_diagnostics,
        )
    except HarSanitizationError as exc:
        print(f"HAR sanitization failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
