"""AC-3: paging never loses or duplicates a chunk."""
from __future__ import annotations

import json

import pytest

from dify_kb_port.exporter import export_dataset


@pytest.fixture
def big(client):
    """One document with exactly 250 chunks."""
    dataset_id = client.create_dataset({"name": "big", "indexing_technique": "high_quality"})["id"]
    doc = client.create_document_by_text(dataset_id, {"name": "big.md", "text": "chunk 1", "process_rule": {"mode": "automatic"}})["document"]
    client.create_segments(dataset_id, doc["id"], [{"content": f"chunk {i}"} for i in range(2, 251)])
    return dataset_id, doc["id"]


def _exported_contents(client, dataset_id, tmp_path):
    export_dataset(client, dataset_id, tmp_path / "b")
    rows = [json.loads(line) for line in (tmp_path / "b" / "segments.jsonl").read_text(encoding="utf-8").splitlines()]
    return [r["content"] for r in rows]


def test_exports_250_exactly(client, mock, big, tmp_path):
    contents = _exported_contents(client, big[0], tmp_path)
    assert contents == [f"chunk {i}" for i in range(1, 251)]
    assert max(mock.limits_seen) <= 100  # never asks for more than the API allows


@pytest.mark.parametrize("mode", ["always_false", "always_true"])
def test_has_more_miscount(client, mock, big, tmp_path, mode):
    mock.faults.page_cap = 30  # the server quietly lowers `limit`...
    mock.faults.has_more = mode  # ...and reports has_more wrongly
    contents = _exported_contents(client, big[0], tmp_path)
    assert contents == [f"chunk {i}" for i in range(1, 251)]


def test_duplicate_across_pages_deduped(client, mock, big, tmp_path):
    mock.faults.overlap_pages = True  # each page repeats the previous page's last chunk
    contents = _exported_contents(client, big[0], tmp_path)
    assert contents == [f"chunk {i}" for i in range(1, 251)]
