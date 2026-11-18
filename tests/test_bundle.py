"""Bundle helpers: separator choice, name uniqueness, URL scrubbing, format check."""
from __future__ import annotations

import json

import pytest

from dify_kb_port.bundle import choose_separator, public_url, read_bundle, unique_names
from dify_kb_port.errors import UsageError
from dify_kb_port.exporter import export_dataset


def test_separator_avoids_text_that_contains_it():
    first = choose_separator(["plain"])
    second = choose_separator([f"text with {first} inside"])
    assert second != first and second not in "text with {first} inside"
    assert "\n" not in first and "\\" not in first  # the server only interprets backslash-n


def test_unique_names():
    assert unique_names(["a", "b", "a", "a"]) == ["a", "b", "a (2)", "a (3)"]


def test_public_url_drops_credentials_and_query():
    assert public_url("https://user:pw@dify.example.com:8443/v1?sign=zzz#x") == "https://dify.example.com:8443/v1"


def test_unknown_format_is_refused(client, seeded, tmp_path):
    export_dataset(client, seeded, tmp_path / "b")
    manifest = tmp_path / "b" / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["format"] = "dify-kb-port/2"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UsageError, match="unsupported bundle format"):
        read_bundle(tmp_path / "b")


def test_not_a_bundle(tmp_path):
    with pytest.raises(UsageError, match="export"):
        read_bundle(tmp_path)
