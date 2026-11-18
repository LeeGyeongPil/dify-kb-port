"""Knowledge base -> bundle directory."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .bundle import unique_names, write_bundle
from .client import DifyClient
from .errors import UsageError
from .i18n import t
from .model import Chunk

Progress = Callable[..., None]


def _noop(key: str, **kw: object) -> None:
    pass


def export_dataset(client: DifyClient, dataset_id: str, out: Path, *, force: bool = False,
                   progress: Progress = _noop) -> dict:
    """Export one knowledge base. Returns {"documents": n, "chunks": n}."""
    if out.exists() and any(out.iterdir()) and not force:  # fail before downloading anything
        raise UsageError(t("err.out_exists", out=out))
    dataset = client.get_dataset(dataset_id)
    if dataset.get("provider") == "external":
        raise UsageError(t("err.external"))
    dataset.setdefault("id", dataset_id)
    fields = client.metadata_fields(dataset_id)

    docs = sorted(client.documents(dataset_id), key=lambda d: (d.get("name") or "", d.get("id") or ""))
    names = unique_names([d.get("name") or "" for d in docs])
    documents: list[dict] = []
    chunks: dict[str, list[Chunk]] = {}
    raw: dict[str, list[dict]] = {}
    total = 0
    for index, (doc, name) in enumerate(zip(docs, names), start=1):
        segs = sorted(client.segments(dataset_id, doc["id"]), key=lambda s: s.get("position", 0))
        raw[name] = segs
        chunks[name] = [Chunk.from_api(s) for s in segs]
        documents.append({
            "name": name,
            "doc_form": doc.get("doc_form") or "text_model",
            "enabled": bool(doc.get("enabled", True)),
            "archived": bool(doc.get("archived", False)),
            "metadata": [{"name": m["name"], "type": m.get("type", "string"), "value": m.get("value")}
                         for m in (doc.get("doc_metadata") or []) if m.get("name")],
        })
        total += len(segs)
        progress("export.doc", index=index, count=len(docs), name=name, chunks=len(segs))
    write_bundle(out, source_url=str(client.base_url), dataset=dataset, metadata_fields=fields,
                 documents=documents, chunks=chunks, raw_segments=raw, force=force)
    progress("export.done", documents=len(docs), chunks=total, out=str(out))
    return {"documents": len(docs), "chunks": total}
