"""AC-1: hand edits survive export -> import."""
from __future__ import annotations

from dify_kb_port.diff import snapshot_instance
from dify_kb_port.exporter import export_dataset
from dify_kb_port.importer import import_bundle

from conftest import make_docs


def test_roundtrip_preserves_edits(client, seeded, tmp_path):
    export_dataset(client, seeded, tmp_path / "b")
    result = import_bundle(client, tmp_path / "b", name="copy")
    report = result.report
    assert report is not None
    assert report.ok and report.total == 6 and report.identical == 6
    assert report.missing == [] and report.changed == []

    docs = list(client.documents(result.dataset_id))
    assert {m["name"]: m["value"] for m in docs[0]["doc_metadata"]} == {"team": "cs"}
    chunks = snapshot_instance(client, result.dataset_id, "t").chunks
    assert chunks[("faq.md", 1)].content.endswith("(edited).")
    assert chunks[("faq.md", 1)].keywords == ("password", "reset")
    assert chunks[("faq.md", 3)].content == "환불은 7일 이내 가능합니다."
    assert chunks[("faq.md", 4)].enabled is False  # the disabled chunk stays disabled
    assert chunks[("faq.md", 6)].content == "Added by hand."  # the manual chunk keeps its place


def test_checkpoint_removed_after_success(client, seeded, tmp_path):
    export_dataset(client, seeded, tmp_path / "b")
    import_bundle(client, tmp_path / "b")
    assert not (tmp_path / "b" / ".import-state.json").exists()


def test_into_skip_and_replace(client, mock, seeded, tmp_path):
    export_dataset(client, seeded, tmp_path / "b")
    first = import_bundle(client, tmp_path / "b", name="copy")
    dataset_id = first.dataset_id
    # skip: the existing document is left alone, nothing new is created
    calls = mock.create_text_calls
    skipped = import_bundle(client, tmp_path / "b", into=dataset_id, on_conflict="skip")
    assert skipped.skipped == ["faq.md"]
    assert mock.create_text_calls == calls
    assert len(list(client.documents(dataset_id))) == 1
    # replace: the document is recreated, still exactly one, still identical
    replaced = import_bundle(client, tmp_path / "b", into=dataset_id, on_conflict="replace")
    assert replaced.report is not None and replaced.report.ok
    assert mock.create_text_calls == calls + 1
    assert len(list(client.documents(dataset_id))) == 1


def test_hierarchical_documents_are_skipped_with_warning(client, tmp_path):
    dataset_id = client.create_dataset({"name": "h", "indexing_technique": "high_quality"})["id"]
    client.create_document_by_text(dataset_id, {"name": "tree.md", "text": "parent a\n\nparent b", "doc_form": "hierarchical_model",
                                                "process_rule": {"mode": "automatic"}})
    client.create_document_by_text(dataset_id, {"name": "plain.md", "text": "x\n\ny", "process_rule": {"mode": "automatic"}})
    export_dataset(client, dataset_id, tmp_path / "b")
    warnings: list[str] = []
    result = import_bundle(client, tmp_path / "b", progress=lambda key, **kw: warnings.append(key))
    assert "import.skip_form" in warnings
    assert result.skipped == ["tree.md"]
    assert result.report is not None and result.report.ok
    names = {d["name"] for d in client.documents(result.dataset_id)}
    assert names == {"plain.md"}


def test_import_keeps_multiple_documents(client, tmp_path):
    dataset_id = make_docs(client, ["a.md", "b.md"])
    export_dataset(client, dataset_id, tmp_path / "b")
    result = import_bundle(client, tmp_path / "b")
    assert result.report is not None and result.report.ok and result.report.total == 6
    assert len(list(client.documents(result.dataset_id))) == 2
