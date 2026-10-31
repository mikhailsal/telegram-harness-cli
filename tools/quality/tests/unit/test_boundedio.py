from pathlib import Path

import pytest

from quality.boundedio import ReportError, load_json, load_xml, parse_json, parse_xml, read_bounded


def test_read_bounded_returns_small_regular_files(tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    report.write_bytes(b"12345")
    assert read_bounded(report, limit=5) == b"12345"


def test_read_bounded_rejects_oversized_files(tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    report.write_bytes(b"123456")
    with pytest.raises(ReportError, match="exceeds 5 bytes"):
        read_bounded(report, limit=5)


def test_read_bounded_rejects_files_that_grow_past_their_stat_size() -> None:
    # procfs reports size 0 for files that have content.
    with pytest.raises(ReportError, match="grew beyond"):
        read_bounded(Path("/proc/self/status"), limit=8)


def test_read_bounded_rejects_missing_directories_and_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    target.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    for path in (tmp_path / "missing.json", tmp_path, link):
        with pytest.raises(ReportError, match="not a regular file"):
            read_bounded(path)


def test_parse_json_accepts_standard_documents() -> None:
    assert parse_json(b'{"a": [1, 2.5, true, null]}', "x") == {"a": [1, 2.5, True, None]}


@pytest.mark.parametrize(
    "data",
    [
        b'{"a": NaN}',
        b"[Infinity]",
        b"[-Infinity]",
        b"{",
        b"\xff",
        b"[" * 100_000,
        b"[" * 513 + b"]" * 513,
    ],
    ids=[
        "nan",
        "infinity",
        "negative-infinity",
        "truncated",
        "non-utf8",
        "deep-nesting",
        "too-deep",
    ],
)
def test_parse_json_rejects_non_standard_or_malformed_input(data: bytes) -> None:
    with pytest.raises(ReportError, match="malformed JSON in origin"):
        parse_json(data, "origin")


def test_load_json_reads_from_disk(tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    report.write_text('{"ok": true}')
    assert load_json(report) == {"ok": True}


def test_parse_xml_accepts_plain_documents() -> None:
    root = parse_xml(b'<coverage><file name="a"/></coverage>', "x")
    assert root.tag == "coverage"
    assert root[0].get("name") == "a"


@pytest.mark.parametrize(
    "data",
    [
        b'<!DOCTYPE r [<!ENTITY e "boom">]><r>&e;</r>',
        b'<!DOCTYPE r SYSTEM "file:///etc/passwd"><r/>',
        b"<r><unclosed></r>",
        b"",
    ],
)
def test_parse_xml_rejects_dtds_entities_and_malformed_input(data: bytes) -> None:
    with pytest.raises(ReportError, match="malformed or unsafe XML in origin"):
        parse_xml(data, "origin")


def test_load_xml_reads_from_disk(tmp_path: Path) -> None:
    report = tmp_path / "r.xml"
    report.write_text("<r/>")
    assert load_xml(report).tag == "r"
