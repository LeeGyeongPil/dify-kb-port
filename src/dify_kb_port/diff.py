"""Snapshots of a bundle or a live knowledge base, and the comparison between two of them."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .bundle import read_bundle, unique_names
from .client import DifyClient
from .model import Chunk, chunk_hash, differing_fields


@dataclass
class Snapshot:
    label: str
    documents: dict[str, dict] = field(default_factory=dict)  # name -> {enabled, archived, metadata{name: value}}
    chunks: dict[tuple[str, int], Chunk] = field(default_factory=dict)  # (document, ordinal) -> Chunk


def _meta(items: list[dict]) -> dict[str, str]:
    return {m["name"]: str(m.get("value")) for m in items if m.get("name")}


def snapshot_bundle(path: Path) -> Snapshot:
    bundle = read_bundle(path)
    snap = Snapshot(label=str(path))
    for doc in bundle.documents:
        snap.documents[doc["name"]] = {"enabled": doc.get("enabled", True), "archived": doc.get("archived", False),
                                       "metadata": _meta(doc.get("metadata") or [])}
        for i, chunk in enumerate(bundle.chunks[doc["name"]], start=1):
            snap.chunks[(doc["name"], i)] = chunk
    return snap


def snapshot_instance(client: DifyClient, dataset_id: str, label: str) -> Snapshot:
    snap = Snapshot(label=label)
    docs = sorted(client.documents(dataset_id), key=lambda d: (d.get("name") or "", d.get("id") or ""))
    for doc, name in zip(docs, unique_names([d.get("name") or "" for d in docs])):
        snap.documents[name] = {"enabled": bool(doc.get("enabled", True)), "archived": bool(doc.get("archived", False)),
                                "metadata": _meta(doc.get("doc_metadata") or [])}
        segs = sorted(client.segments(dataset_id, doc["id"]), key=lambda s: s.get("position", 0))
        for i, seg in enumerate(segs, start=1):
            snap.chunks[(name, i)] = Chunk.from_api(seg)
    return snap


@dataclass
class Report:
    left: str
    right: str
    total: int  # chunks on the left (the expected side)
    identical: int
    changed: list[dict]
    missing: list[dict]
    added: list[dict]
    documents: dict

    @property
    def ok(self) -> bool:
        return not (self.changed or self.missing or self.added or any(self.documents.values()))

    def to_json(self) -> dict:
        return {"ok": self.ok, "left": self.left, "right": self.right, "total": self.total,
                "identical": self.identical, "changed": self.changed, "missing": self.missing,
                "added": self.added, "documents": self.documents}


def compare(left: Snapshot, right: Snapshot, *, ignore_documents: frozenset[str] = frozenset()) -> Report:
    """`left` is what we expect, `right` is what we found."""
    lkeys = {k: v for k, v in left.chunks.items() if k[0] not in ignore_documents}
    rkeys = {k: v for k, v in right.chunks.items() if k[0] not in ignore_documents}
    changed, missing, added = [], [], []
    identical = 0
    for key in sorted(lkeys):
        entry = {"document": key[0], "ordinal": key[1]}
        if key not in rkeys:
            missing.append(entry)
        elif chunk_hash(lkeys[key]) == chunk_hash(rkeys[key]):
            identical += 1
        else:
            changed.append({**entry, "fields": differing_fields(lkeys[key], rkeys[key])})
    for key in sorted(rkeys):
        if key not in lkeys:
            added.append({"document": key[0], "ordinal": key[1]})
    ldocs = {n: d for n, d in left.documents.items() if n not in ignore_documents}
    rdocs = {n: d for n, d in right.documents.items() if n not in ignore_documents}
    doc_changes = []
    for name in sorted(set(ldocs) & set(rdocs)):
        fields = [f for f in ("enabled", "archived", "metadata") if ldocs[name][f] != rdocs[name][f]]
        if fields:
            doc_changes.append({"document": name, "fields": fields})
    documents = {
        "missing": sorted(set(ldocs) - set(rdocs)),
        "added": sorted(set(rdocs) - set(ldocs)),
        "changed": doc_changes,
    }
    return Report(left.label, right.label, len(lkeys), identical, changed, missing, added, documents)
