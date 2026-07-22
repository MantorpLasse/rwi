import json
from pathlib import Path

import pytest

from app.scripts.sanitize_tableau_har import (
    HarSanitizationError,
    main,
    sanitize_tableau_har,
)


def synthetic_har() -> dict:
    return {
        "log": {
            "entries": [
                {
                    "startedDateTime": "2026-01-02T03:04:05Z",
                    "time": 12.5,
                    "_initiator": {"type": "script", "url": "machine-secret"},
                    "request": {
                        "method": "POST",
                        "url": "https://example.test/bootstrapSession/sessions/fake-session?requestId=fake-request",
                        "queryString": [{"name": "requestId", "value": "fake-request"}],
                        "headers": [
                            {"name": "Cookie", "value": "sid=fake-cookie"},
                            {"name": "Authorization", "value": "Bearer fake-auth"},
                            {"name": "X-CSRF-Token", "value": "fake-csrf"},
                            {"name": "Accept", "value": "application/json"},
                        ],
                        "postData": {
                            "mimeType": "application/x-www-form-urlencoded",
                            "text": "sheetId=Main&sessionid=fake-session&browserId=fake-browser",
                        },
                    },
                    "response": {
                        "status": 200,
                        "headers": [
                            {"name": "Set-Cookie", "value": "sid=fake-cookie"},
                            {"name": "Content-Type", "value": "application/octet-stream"},
                        ],
                        "content": {
                            "mimeType": "application/octet-stream",
                            "size": 321,
                            "text": "bootstrapSession metadata mark fake-session",
                        },
                        "redirectURL": "",
                        "bodySize": 321,
                    },
                },
                {
                    "startedDateTime": "2026-01-02T03:04:06Z",
                    "time": 4,
                    "request": {
                        "method": "GET",
                        "url": "https://example.test/static/app.js",
                        "queryString": [],
                        "headers": [],
                    },
                    "response": {
                        "status": 304,
                        "headers": [],
                        "content": {"mimeType": "application/javascript", "size": 0},
                        "redirectURL": "https://example.test/next?token=fake-token",
                    },
                },
            ]
        }
    }


def write_raw(tmp_path: Path, value: object) -> tuple[Path, Path]:
    root = tmp_path / "faa_tableau"
    raw_directory = root / "raw"
    raw_directory.mkdir(parents=True)
    path = raw_directory / "capture.har"
    path.write_text(json.dumps(value), encoding="utf-8")
    return root, path


def test_valid_har_is_reduced_deterministically_and_preserves_order(tmp_path):
    root, path = write_raw(tmp_path, synthetic_har())
    first = sanitize_tableau_har(path, diagnostics_root=root)
    first_bytes = (root / "sanitized" / first.output_file).read_bytes()
    second = sanitize_tableau_har(path, diagnostics_root=root)
    assert first.output_sha256 == second.output_sha256
    assert first_bytes == (root / "sanitized" / second.output_file).read_bytes()
    output = json.loads(first_bytes)
    assert [item["sequence"] for item in output["entries"]] == [1, 2]
    assert output["entries"][0]["startedDateTime"] == "2026-01-02T03:04:05Z"


def test_secrets_are_removed_but_safe_names_and_metadata_remain(tmp_path):
    root, path = write_raw(tmp_path, synthetic_har())
    report = sanitize_tableau_har(path, diagnostics_root=root)
    output_path = root / "sanitized" / report.output_file
    text = output_path.read_text(encoding="utf-8")
    for secret in ("fake-session", "fake-request", "fake-cookie", "fake-auth", "fake-csrf", "fake-browser", "machine-secret"):
        assert secret not in text
    entry = json.loads(text)["entries"][0]
    assert entry["category"] == "session_or_bootstrap"
    assert entry["request"]["header_names"] == ["Cookie", "Authorization", "X-CSRF-Token", "Accept"]
    assert entry["request"]["body_field_names"] == ["sheetId", "sessionid", "browserId"]
    assert entry["response"]["status"] == 200
    assert entry["response"]["media_type"] == "application/octet-stream"
    assert entry["response"]["byte_size"] == 321
    assert "bootstrapsession" in entry["response"]["tableau_structure_markers"]
    assert report.removed_headers == 4
    assert report.redacted_values > 0


@pytest.mark.parametrize("raw", [b"not-json", b"[]"])
def test_malformed_or_invalid_har_fails_without_content(tmp_path, raw, capsys):
    root = tmp_path / "faa_tableau"
    path = root / "raw" / "capture.har"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    with pytest.raises(HarSanitizationError) as caught:
        sanitize_tableau_har(path, diagnostics_root=root)
    assert "not-json" not in str(caught.value)
    assert "fake" not in (capsys.readouterr().out + capsys.readouterr().err)


def test_outside_file_is_rejected_unless_explicitly_allowed(tmp_path):
    root = tmp_path / "faa_tableau"
    path = tmp_path / "outside.har"
    path.write_text(json.dumps(synthetic_har()), encoding="utf-8")
    with pytest.raises(HarSanitizationError):
        sanitize_tableau_har(path, diagnostics_root=root)
    report = sanitize_tableau_har(path, diagnostics_root=root, allow_outside_diagnostics=True)
    assert report.entries == 2


def test_report_has_hashes_sizes_categories_and_secret_free_cli_output(tmp_path, capsys, monkeypatch):
    root, path = write_raw(tmp_path, synthetic_har())
    report = sanitize_tableau_har(path, diagnostics_root=root)
    assert len(report.input_sha256) == len(report.output_sha256) == 64
    assert report.input_byte_size > 0
    assert report.output_byte_size > 0
    assert "cookies_and_authorization" in report.removed_data_categories
    monkeypatch.chdir(tmp_path)
    expected = Path("data/diagnostics/faa_tableau/raw")
    expected.mkdir(parents=True)
    cli_path = expected / "capture.har"
    cli_path.write_text(json.dumps(synthetic_har()), encoding="utf-8")
    assert main([str(cli_path)]) == 0
    stdout = capsys.readouterr().out
    assert "fake-" not in stdout
    assert "input_sha256" in stdout


def test_edge_telemetry_url_is_reduced_without_changing_sequence(tmp_path):
    value = synthetic_har()
    value["log"]["entries"][1]["request"]["url"] = (
        "https://example.test/akam/beacon/fake-edge-identifier?token=fake-token"
    )
    root, path = write_raw(tmp_path, value)
    report = sanitize_tableau_har(path, diagnostics_root=root)
    entries = json.loads(
        (root / "sanitized" / report.output_file).read_text(encoding="utf-8")
    )["entries"]
    assert [item["sequence"] for item in entries] == [1, 2]
    assert entries[1]["category"] == "telemetry"
    assert entries[1]["request"]["url"] == (
        "https://example.test/[telemetry-redacted]"
    )


def test_static_asset_text_does_not_create_protocol_markers(tmp_path):
    value = synthetic_har()
    entry = value["log"]["entries"][1]
    entry["request"]["url"] = "https://example.test/tableau.css"
    entry["response"]["content"] = {
        "mimeType": "text/css",
        "size": 10,
        "text": ".mark-command { display: block; }",
    }
    root, path = write_raw(tmp_path, value)
    report = sanitize_tableau_har(path, diagnostics_root=root)
    entries = json.loads(
        (root / "sanitized" / report.output_file).read_text(encoding="utf-8")
    )["entries"]
    assert entries[1]["category"] == "static_asset"
    assert entries[1]["response"]["tableau_structure_markers"] == []


def test_multipart_boundary_and_opaque_tile_cache_path_are_removed(tmp_path):
    value = synthetic_har()
    value["log"]["entries"][0]["request"]["postData"]["mimeType"] = (
        "multipart/form-data; boundary=fake-transient-boundary"
    )
    value["log"]["entries"][1]["request"]["url"] = (
        "https://example.test/vizql/tilecache/fake-opaque-cache-key/image.png"
    )
    root, path = write_raw(tmp_path, value)
    report = sanitize_tableau_har(path, diagnostics_root=root)
    entries = json.loads(
        (root / "sanitized" / report.output_file).read_text(encoding="utf-8")
    )["entries"]
    assert entries[0]["request"]["body_media_type"] == "multipart/form-data"
    assert entries[1]["request"]["url"] == (
        "https://example.test/vizql/tilecache/[opaque-path-redacted]"
    )


def test_established_session_command_is_not_misclassified_as_bootstrap(tmp_path):
    value = synthetic_har()
    value["log"]["entries"][0]["request"]["url"] = (
        "https://example.test/vizql/t/Site/w/Book/v/View/"
        "sessions/fake-session/commands/tabdoc/select"
    )
    root, path = write_raw(tmp_path, value)
    report = sanitize_tableau_har(path, diagnostics_root=root)
    entries = json.loads(
        (root / "sanitized" / report.output_file).read_text(encoding="utf-8")
    )["entries"]
    assert entries[0]["category"] == "worksheet_command"
