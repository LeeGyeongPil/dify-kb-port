"""Keyless demo: hand-edited knowledge base -> export -> import -> verify, against the practice mock."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

from .client import DifyClient
from .diff import compare, snapshot_bundle, snapshot_instance
from .exporter import export_dataset
from .i18n import t
from .importer import import_bundle
from .mock_server import DEFAULT_KEY, MockDify

SEED_NAME = "FAQ (source)"  # the knowledge base `demo` and `mock-server --seed` create

# The "source file" a user once uploaded. Short invented FAQ lines; the mail address uses a reserved domain.
SOURCE_TEXT = "\n\n".join([
    "Reset your password in Settings.",
    "Billing runs monthly.",
    "환불은 7일 이내 가능합니다.",
    "Old policy text.",
    "Contact support@example.com.",
])


def seed_source(client: DifyClient, name: str = SEED_NAME) -> str:
    """Create a knowledge base, then edit it by hand the way people do in the console."""
    dataset_id = client.create_dataset({"name": name, "indexing_technique": "high_quality"})["id"]
    field_id = client.create_metadata_field(dataset_id, "team", "string")["id"]
    doc = client.create_document_by_text(dataset_id, {
        "name": "faq.md", "text": SOURCE_TEXT, "indexing_technique": "high_quality",
        "process_rule": {"mode": "automatic"}})["document"]
    segs = sorted(client.segments(dataset_id, doc["id"]), key=lambda s: s["position"])
    # edit 1: rewrite a chunk and add keywords
    client.update_segment(dataset_id, doc["id"], segs[0]["id"], {
        "content": "Reset your password in Settings > Security (edited).", "keywords": ["password", "reset"]})
    # edit 2: disable an outdated chunk
    client.update_segment(dataset_id, doc["id"], segs[3]["id"], {"enabled": False})
    # edit 3: add a chunk by hand
    client.create_segments(dataset_id, doc["id"], [{"content": "Added by hand.", "keywords": ["manual"]}])
    # edit 4: document metadata
    client.set_document_metadata(dataset_id, doc["id"], [{"id": field_id, "name": "team", "value": "cs"}])
    return dataset_id


def run_demo(say: Callable[[str], None] = print) -> int:
    with MockDify() as mock, tempfile.TemporaryDirectory(prefix="dify-kb-port-demo-") as tmp:
        say(t("demo.banner"))
        client = DifyClient(mock.url, DEFAULT_KEY)
        try:
            say(t("demo.step_seed"))
            source_id = seed_source(client)
            expected = snapshot_instance(client, source_id, "source")

            say(t("demo.step_export"))
            bundle = Path(tmp) / "faq-bundle"
            export_dataset(client, source_id, bundle)

            say(t("demo.step_import"))
            mock.faults.index_polls = 2  # the target indexes asynchronously, like a real server
            result = import_bundle(client, bundle, name="FAQ (migrated)")
            report = compare(snapshot_bundle(bundle), snapshot_instance(client, result.dataset_id or "", "target"))
            say(t("demo.identical", identical=report.identical, total=report.total))
            target_docs = list(client.documents(result.dataset_id or ""))
            metadata = {m["name"]: m["value"] for m in target_docs[0].get("doc_metadata", [])}
            say(t("demo.metadata", metadata=metadata))
            target_chunks = snapshot_instance(client, result.dataset_id or "", "target").chunks
            say(t("demo.disabled", value=not target_chunks[("faq.md", 4)].enabled))

            say(t("demo.step_naive"))
            naive_id = client.create_dataset({"name": "FAQ (naive re-upload)", "indexing_technique": "high_quality"})["id"]
            client.create_document_by_text(naive_id, {"name": "faq.md", "text": SOURCE_TEXT,
                                                      "indexing_technique": "high_quality",
                                                      "process_rule": {"mode": "automatic"}})
            naive = compare(expected, snapshot_instance(client, naive_id, "naive"))
            say(t("demo.naive", bad=len(naive.changed) + len(naive.missing), total=naive.total))
            say(t("demo.next"))
            return 0 if report.ok else 1
        finally:
            client.close()
