"""AC-4: reconcile (pure function) and its use against a server that re-splits chunks."""
from __future__ import annotations

from dify_kb_port.exporter import export_dataset
from dify_kb_port.importer import import_bundle
from dify_kb_port.model import Chunk
from dify_kb_port.reconcile import reconcile


def pairs(*chunks: Chunk):
    return [(f"s{i}", c) for i, c in enumerate(chunks)]


def test_noop_when_equal():
    chunks = [Chunk("a", keywords=("k",)), Chunk("b", enabled=False)]
    assert reconcile(chunks, pairs(*chunks)) == []


def test_operations_are_update_create_delete_then_state():
    desired = [Chunk("a2"), Chunk("b", enabled=False), Chunk("c")]
    actual = pairs(Chunk("a"), Chunk("b"))
    ops = reconcile(desired, actual)
    assert [(o.kind, o.index) for o in ops] == [("update", 0), ("create", 2), ("disable", 1)]
    assert [o.kind for o in reconcile([Chunk("a")], pairs(Chunk("a"), Chunk("x"), Chunk("y")))] == ["delete", "delete"]
    assert [o.kind for o in reconcile(desired, actual, include_state=False)] == ["update", "create"]


def test_server_resplit_fixed(client, mock, tmp_path):
    long_chunk = " ".join(f"word{i}" for i in range(12))
    dataset_id = client.create_dataset({"name": "s", "indexing_technique": "high_quality"})["id"]
    client.create_document_by_text(dataset_id, {"name": "d.md", "text": f"short one\n\n{long_chunk}\n\nshort two",
                                                "process_rule": {"mode": "automatic"}})
    export_dataset(client, dataset_id, tmp_path / "b")
    mock.faults.retokenize_limit = 5  # the target's tokenizer cuts the 12-word chunk into 3
    result = import_bundle(client, tmp_path / "b", name="t")
    assert result.report is not None and result.report.ok and result.report.total == 3
    # reconcile had real work to do: the extra pieces had to be deleted
    assert any(m == "DELETE" and "/segments/" in p for m, p in mock.requests)


def test_qa_answers_restored(client, mock, tmp_path):
    dataset_id = client.create_dataset({"name": "qa", "indexing_technique": "high_quality"})["id"]
    doc = client.create_document_by_text(dataset_id, {"name": "qa.md", "doc_form": "qa_model", "doc_language": "English",
                                                      "text": "Q one?\n\nQ two?\n\nQ three?",
                                                      "process_rule": {"mode": "automatic"}})["document"]
    segs = sorted(client.segments(dataset_id, doc["id"]), key=lambda s: s["position"])
    answers = ["Because A.", "Because B.", "한국어 답변."]
    for seg, answer in zip(segs, answers):
        client.update_segment(dataset_id, doc["id"], seg["id"], {"content": seg["content"], "answer": answer})
    export_dataset(client, dataset_id, tmp_path / "b")
    result = import_bundle(client, tmp_path / "b", name="qa-copy")
    assert result.report is not None and result.report.ok and result.report.total == 3
    target_doc = next(iter(client.documents(result.dataset_id)))
    got = sorted((s["position"], s["answer"]) for s in client.segments(result.dataset_id, target_doc["id"]))
    assert [a for _, a in got] == answers
