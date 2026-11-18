"""Bundle -> target knowledge base.

Per document: create it from the chunks joined by a unique separator (so the server splits
exactly where we did), wait for indexing, then reconcile what the server built against the
exported chunks, then restore enabled/disabled state, metadata values and document status.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import client as client_mod
from .bundle import Bundle, atomic_write, public_url, read_bundle
from .client import DifyClient
from .diff import Report, compare, snapshot_bundle, snapshot_instance
from .errors import IndexingError, UsageError
from .i18n import t
from .model import Chunk
from .reconcile import Op, reconcile

STATE_FILE = ".import-state.json"
SUPPORTED_FORMS = ("text_model", "qa_model")
MAX_TOKENS = 4000  # Dify's default upper bound for a custom segment length
POLL_SECONDS = 1.0

Progress = Callable[..., None]


def _noop(key: str, **kw: object) -> None:
    pass


@dataclass
class ImportResult:
    dataset_id: str | None = None
    report: Report | None = None  # bundle vs. target for the documents this run imported
    skipped_report: Report | None = None  # bundle vs. target for documents left alone (--on-conflict skip)
    plan: dict | None = None  # filled for --dry-run
    skipped: list[str] = field(default_factory=list)  # unsupported forms and existing documents that were kept
    kept_existing: list[str] = field(default_factory=list)  # documents already in the target, not touched


def _load_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # UnicodeDecodeError is a ValueError
        raise UsageError(t("err.checkpoint_unreadable", path=path, detail=str(exc))) from exc
    # 읽히는 JSON 이라도 모양이 다르면(목록 등) 깔끔한 안내로 끝낸다 — 묶음 manifest 와 같은 원칙.
    if not (isinstance(state, dict) and isinstance(state.get("documents"), dict)
            and "dataset_id" in state and isinstance(state["dataset_id"], (str, type(None)))
            and isinstance(state.get("metadata_ids", {}), dict)
            and all(isinstance(v, dict) for v in state["documents"].values())):
        raise UsageError(t("err.checkpoint_unreadable", path=path, detail="unexpected structure"))
    return state


def _save_state(path: Path, state: dict) -> None:
    atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def _wait_indexed(client: DifyClient, dataset_id: str, batch: str, timeout: float) -> None:
    waited = 0.0
    while True:
        statuses = client.indexing_status(dataset_id, batch)
        if any(s.get("indexing_status") == "error" for s in statuses):
            err = next(s for s in statuses if s.get("indexing_status") == "error")
            raise IndexingError(t("err.index_failed", detail=err.get("error") or "unknown"))
        if statuses and all(s.get("indexing_status") == "completed" for s in statuses):
            return
        if waited >= timeout:
            raise IndexingError(t("err.index_timeout", seconds=f"{timeout:.0f}"))
        client_mod._sleep(POLL_SECONDS)
        waited += POLL_SECONDS


def _actual(client: DifyClient, dataset_id: str, document_id: str) -> list[tuple[str, Chunk]]:
    segs = sorted(client.segments(dataset_id, document_id), key=lambda s: s.get("position", 0))
    return [(s["id"], Chunk.from_api(s)) for s in segs]


def _apply(client: DifyClient, dataset_id: str, document_id: str, ops: list[Op], form: str) -> None:
    creates: list[dict] = []

    def flush() -> None:
        if creates:
            client.create_segments(dataset_id, document_id, list(creates))
            creates.clear()

    for op in ops:
        if op.kind == "create":
            assert op.chunk is not None
            item = {"content": op.chunk.content, "keywords": list(op.chunk.keywords)}
            if form == "qa_model":
                item["answer"] = op.chunk.answer
            creates.append(item)
            continue
        flush()
        if op.kind == "update":
            assert op.chunk is not None and op.segment_id
            fields = {"content": op.chunk.content, "keywords": list(op.chunk.keywords)}
            if form == "qa_model":
                fields["answer"] = op.chunk.answer  # always explicit: partial updates dropped answers in some versions
            client.update_segment(dataset_id, document_id, op.segment_id, fields)
        elif op.kind == "delete":
            assert op.segment_id
            client.delete_segment(dataset_id, document_id, op.segment_id)
        elif op.kind in ("enable", "disable"):
            assert op.segment_id
            client.update_segment(dataset_id, document_id, op.segment_id, {"enabled": op.kind == "enable"})
    flush()


def _reconcile_document(client: DifyClient, dataset_id: str, document_id: str, desired: list[Chunk], form: str) -> None:
    for round_no in range(4):
        ops = reconcile(desired, _actual(client, dataset_id, document_id), include_state=round_no > 0)
        if not ops:
            return
        _apply(client, dataset_id, document_id, ops, form)
    # Anything still different is reported by the verify step instead of looping forever.


def _plan(bundle: Bundle) -> dict:
    docs = [d for d in bundle.documents if d["doc_form"] in SUPPORTED_FORMS]
    skipped = [d["name"] for d in bundle.documents if d["doc_form"] not in SUPPORTED_FORMS]
    chunks = sum(len(bundle.chunks[d["name"]]) for d in docs)
    return {"documents": len(docs), "chunks": chunks, "skipped": skipped,
            "qa_documents": [d["name"] for d in docs if d["doc_form"] == "qa_model"],
            "max_write_requests": 1 + len(bundle.manifest["metadata_fields"]) + 3 * len(docs) + chunks}


def import_bundle(client: DifyClient, bundle_path: Path, *, name: str | None = None, into: str | None = None,
                  on_conflict: str = "skip", dry_run: bool = False, resume: bool = False, verify: bool = True,
                  index_timeout: float = 600.0, progress: Progress = _noop) -> ImportResult:
    if on_conflict not in ("skip", "replace"):
        raise UsageError(t("err.on_conflict"))
    bundle = read_bundle(bundle_path)
    plan = _plan(bundle)
    result = ImportResult(skipped=list(plan["skipped"]))
    for doc_name in plan["skipped"]:
        progress("import.skip_form", name=doc_name)

    if dry_run:
        client.list_datasets()  # proves the URL and key work; read-only
        if into:
            ds = client.get_dataset(into)
            if ds.get("embedding_available") is False and bundle.manifest["dataset"]["indexing_technique"] == "high_quality":
                progress("dry.no_embedding", dataset=into)
        if plan["qa_documents"]:
            progress("dry.qa_needs_llm", count=len(plan["qa_documents"]))
        result.plan = plan
        progress("dry.summary", documents=plan["documents"], chunks=plan["chunks"], requests=plan["max_write_requests"])
        return result

    state_path = bundle_path / STATE_FILE
    target = public_url(str(client.base_url))
    if state_path.exists():
        if not resume:
            raise UsageError(t("err.checkpoint_exists", path=state_path))
        state = _load_state(state_path)
        if state.get("target_url") != target:
            raise UsageError(t("err.checkpoint_target", saved=state.get("target_url"), target=target))
    else:
        if resume:
            raise UsageError(t("err.no_checkpoint", path=state_path))
        state = {"target_url": target, "dataset_id": None, "created_dataset": False, "metadata_ids": {}, "documents": {}}

    # 1. target knowledge base
    if state["dataset_id"] is None:
        if into:
            client.get_dataset(into)  # fails clearly (404) if it does not exist
            state["dataset_id"] = into
        else:
            src = bundle.manifest["dataset"]
            created = client.create_dataset({"name": name or src["name"] or "imported",
                                             "indexing_technique": src["indexing_technique"], "permission": "only_me"})
            state["dataset_id"], state["created_dataset"] = created["id"], True
        _save_state(state_path, state)
    dataset_id = state["dataset_id"]
    result.dataset_id = dataset_id
    progress("import.dataset", dataset=dataset_id)

    # 2. metadata fields (by name)
    have = {f["name"]: f["id"] for f in client.metadata_fields(dataset_id)}
    for fld in bundle.manifest["metadata_fields"]:
        if fld["name"] not in have:
            have[fld["name"]] = client.create_metadata_field(dataset_id, fld["name"], fld["type"])["id"]
    state["metadata_ids"] = have
    _save_state(state_path, state)

    # 3. documents
    existing = {d["name"]: d["id"] for d in client.documents(dataset_id)}
    todo = [d for d in bundle.documents if d["doc_form"] in SUPPORTED_FORMS]
    for index, doc in enumerate(todo, start=1):
        doc_name = doc["name"]
        entry = state["documents"].get(doc_name, {})
        if entry.get("status") in ("done", "skipped_existing"):
            progress("import.doc_resumed", index=index, count=len(todo), name=doc_name)
            if entry["status"] == "skipped_existing":
                result.skipped.append(doc_name)
                result.kept_existing.append(doc_name)
            continue
        if entry.get("status") != "in_progress" and doc_name in existing and into and on_conflict == "skip":
            state["documents"][doc_name] = {"status": "skipped_existing"}
            _save_state(state_path, state)
            result.skipped.append(doc_name)
            result.kept_existing.append(doc_name)
            progress("import.doc_exists", index=index, count=len(todo), name=doc_name)
            continue
        state["documents"][doc_name] = {"status": "in_progress", "dst_id": None}
        _save_state(state_path, state)
        if doc_name in existing:  # a half-built copy from an interrupted run, or --on-conflict replace
            client.delete_document(dataset_id, existing.pop(doc_name))
        dst_id = _create_document(client, dataset_id, bundle, doc, index_timeout)
        state["documents"][doc_name] = {"status": "in_progress", "dst_id": dst_id}
        _save_state(state_path, state)
        _finish_document(client, dataset_id, dst_id, bundle, doc, state["metadata_ids"])
        state["documents"][doc_name] = {"status": "done", "dst_id": dst_id}
        _save_state(state_path, state)
        progress("import.doc_done", index=index, count=len(todo), name=doc_name, chunks=len(bundle.chunks[doc_name]))

    # 4. verify, then drop the checkpoint
    if verify:
        left = snapshot_bundle(bundle_path)
        right = snapshot_instance(client, dataset_id, f"dataset {dataset_id}")
        if into:
            right.documents = {n: d for n, d in right.documents.items() if n in left.documents}
            right.chunks = {k: c for k, c in right.chunks.items() if k[0] in left.documents}
        result.report = compare(left, right, ignore_documents=frozenset(result.skipped))
        if result.kept_existing:
            # 건너뛴 기존 문서도 비교는 한다: 가져오기는 실패로 치지 않지만, 묶음과 다른데
            # "차이 없음"처럼 보이면 안 된다(CI 에서 일치로 오해). 나머지 문서는 비교에서 뺀다.
            others = frozenset(n for n in left.documents if n not in result.kept_existing)
            result.skipped_report = compare(left, right, ignore_documents=others)
    state_path.unlink(missing_ok=True)
    return result


def _create_document(client: DifyClient, dataset_id: str, bundle: Bundle, doc: dict, index_timeout: float) -> str:
    chunks = bundle.chunks[doc["name"]]
    if not chunks:
        raise UsageError(t("err.no_chunks", name=repr(doc["name"])))
    form = doc["doc_form"]
    sep = bundle.separator
    if any(sep in c.content for c in chunks):
        raise UsageError(t("err.separator_conflict", sep=repr(sep), name=repr(doc["name"])))
    # qa_model: one question only, so the target generates as little as possible; reconcile replaces it.
    text = chunks[0].content if form == "qa_model" else sep.join(c.content for c in chunks)
    body = {
        "name": doc["name"], "text": text, "doc_form": form,
        "indexing_technique": bundle.manifest["dataset"]["indexing_technique"],
        "process_rule": {"mode": "custom", "rules": {
            "pre_processing_rules": [{"id": "remove_extra_spaces", "enabled": False},
                                     {"id": "remove_urls_emails", "enabled": False}],
            "segmentation": {"separator": sep, "max_tokens": MAX_TOKENS}}},
    }
    if form == "qa_model":
        body["doc_language"] = "English"
    created = client.create_document_by_text(dataset_id, body)
    _wait_indexed(client, dataset_id, created["batch"], index_timeout)
    return created["document"]["id"]


def _finish_document(client: DifyClient, dataset_id: str, dst_id: str, bundle: Bundle, doc: dict,
                     metadata_ids: dict[str, str]) -> None:
    desired = bundle.chunks[doc["name"]]
    _reconcile_document(client, dataset_id, dst_id, desired, doc["doc_form"])
    if doc.get("metadata"):
        items = []
        for m in doc["metadata"]:
            if m["name"] not in metadata_ids:
                metadata_ids[m["name"]] = client.create_metadata_field(dataset_id, m["name"], m.get("type", "string"))["id"]
            items.append({"id": metadata_ids[m["name"]], "name": m["name"], "value": m.get("value")})
        client.set_document_metadata(dataset_id, dst_id, items)
    # Document status goes last: chunk edits are refused on a disabled document.
    if doc.get("archived"):
        client.set_documents_status(dataset_id, "archive", [dst_id])
    elif not doc.get("enabled", True):
        client.set_documents_status(dataset_id, "disable", [dst_id])
