"""Bundle directory: manifest.json + segments.jsonl (format: docs/bundle-format.md)."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import FORMAT, SEPARATOR_CANDIDATES, __version__
from .errors import UsageError
from .i18n import t
from .model import Chunk, chunk_hash

MANIFEST = "manifest.json"
SEGMENTS = "segments.jsonl"


def atomic_write(path: Path, text: str) -> None:
    """Write via a temp file and rename so a crash never leaves a half-written file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def unique_names(names: list[str]) -> list[str]:
    """Make document names unique ("a", "a (2)") so (name, ordinal) is a usable key."""
    seen: dict[str, int] = {}
    out = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return out


def public_url(url: str) -> str:
    """Drop credentials, query and fragment so nothing secret lands in a bundle."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    return f"{parts.scheme}://{host}{parts.path}"


def choose_separator(contents: list[str]) -> str:
    joined = "\n".join(contents)
    for cand in SEPARATOR_CANDIDATES:
        if cand not in joined:
            return cand
    n = 2
    while True:  # extremely unlikely: every built-in candidate occurs in the text
        cand = f"␞DIFYKBPORT{n}␞"
        if cand not in joined:
            return cand
        n += 1


def doc_checksum(chunks: list[Chunk]) -> str:
    return hashlib.sha256("".join(chunk_hash(c) for c in chunks).encode()).hexdigest()


class Bundle:
    def __init__(self, manifest: dict, chunks: dict[str, list[Chunk]]):
        self.manifest = manifest
        self.chunks = chunks

    @property
    def documents(self) -> list[dict]:
        return self.manifest["documents"]

    @property
    def separator(self) -> str:
        return self.manifest["separator"]


def write_bundle(out: Path, *, source_url: str, dataset: dict, metadata_fields: list[dict],
                 documents: list[dict], chunks: dict[str, list[Chunk]], raw_segments: dict[str, list[dict]],
                 force: bool = False) -> None:
    """`documents` entries need name/doc_form/enabled/archived/metadata; chunks are in ordinal order."""
    if out.exists() and any(out.iterdir()) and not force:
        raise UsageError(t("err.out_exists", out=out))
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    for doc in documents:
        for i, seg in enumerate(raw_segments[doc["name"]], start=1):
            lines.append(json.dumps({
                "document": doc["name"],
                "ordinal": i,
                "content": seg.get("content") or "",
                "answer": seg.get("answer") or "",
                "keywords": list(seg.get("keywords") or []),
                "enabled": bool(seg.get("enabled", True)),
                "child_chunks": [{"content": c.get("content", "")} for c in (seg.get("child_chunks") or [])],
                "summary": seg.get("summary") or "",
            }, ensure_ascii=False))
    all_contents = [c.content for cs in chunks.values() for c in cs] + [c.answer for cs in chunks.values() for c in cs]
    manifest = {
        "format": FORMAT,
        "tool_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"url": public_url(source_url), "dataset_id": dataset.get("id", "")},
        "dataset": {
            "name": dataset.get("name") or "",
            "indexing_technique": dataset.get("indexing_technique") or "high_quality",
            "doc_form": dataset.get("doc_form") or "text_model",
            "embedding_model": dataset.get("embedding_model"),
            "embedding_model_provider": dataset.get("embedding_model_provider"),
        },
        "metadata_fields": [{"name": f["name"], "type": f.get("type", "string")} for f in metadata_fields],
        "separator": choose_separator(all_contents),
        "documents": [{**d, "chunk_count": len(chunks[d["name"]]), "checksum": doc_checksum(chunks[d["name"]])}
                      for d in documents],
    }
    atomic_write(out / SEGMENTS, "".join(line + "\n" for line in lines))
    atomic_write(out / MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")  # written last: marks completeness


def _check_manifest_shape(manifest: object) -> str | None:
    """Return what is wrong with a parsed manifest, or None. Valid JSON is not enough: `[]` or a
    string in place of an object must end in the same clean error as a broken file."""
    if not isinstance(manifest, dict):
        return f"expected a JSON object, got {type(manifest).__name__}"
    dataset = manifest.get("dataset")
    if not isinstance(dataset, dict):
        return "field 'dataset' must be an object"
    for key in ("name", "indexing_technique"):
        if not isinstance(dataset.get(key), str):
            return f"dataset.{key} must be a string"
    if not isinstance(manifest.get("separator"), str) or not manifest["separator"]:
        return "field 'separator' must be a non-empty string"
    fields = manifest.get("metadata_fields", [])
    if not isinstance(fields, list) or not all(isinstance(f, dict) and isinstance(f.get("name"), str) for f in fields):
        return "field 'metadata_fields' must be a list of objects with a name"
    docs = manifest.get("documents")
    if not isinstance(docs, list):
        return "field 'documents' must be a list"
    for i, doc in enumerate(docs):
        if not isinstance(doc, dict):
            return f"documents[{i}] must be an object"
        for key in ("name", "doc_form", "checksum"):
            if not isinstance(doc.get(key), str):
                return f"documents[{i}].{key} must be a string"
        metadata = doc.get("metadata") or []
        if not isinstance(metadata, list) or not all(isinstance(m, dict) and isinstance(m.get("name"), str)
                                                     for m in metadata):
            return f"documents[{i}].metadata must be a list of objects with a name"
    return None


def read_bundle(path: Path) -> Bundle:
    manifest_path = path / MANIFEST
    if not manifest_path.is_file():
        raise UsageError(t("err.not_bundle", path=path, manifest=MANIFEST))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UsageError(t("err.bundle_corrupt", file=manifest_path, detail=str(exc))) from exc
    # 형식 표시부터 본다: 다른 버전 묶음이면 "손상"보다 "지원하지 않는 형식"이 맞는 안내다.
    fmt = str(manifest.get("format", "")) if isinstance(manifest, dict) else ""
    name, _, version = fmt.partition("/")
    if isinstance(manifest, dict) and (name != FORMAT.partition("/")[0]
                                       or version.split(".")[0] != FORMAT.partition("/")[2]):
        raise UsageError(t("err.bad_format", fmt=repr(fmt), supported=repr(FORMAT)))
    # JSON 으로는 읽혀도 모양이 틀리면(목록·문자열·필드 누락) 같은 "손상" 안내로 끝낸다.
    problem = _check_manifest_shape(manifest)
    if problem:
        raise UsageError(t("err.bundle_corrupt", file=manifest_path, detail=problem))
    assert isinstance(manifest, dict)
    seg_path = path / SEGMENTS
    chunks: dict[str, list[Chunk]] = {d["name"]: [] for d in manifest["documents"]}
    if not seg_path.is_file():
        raise UsageError(t("err.segments_missing", file=seg_path))
    rows: dict[str, list[dict]] = {name: [] for name in chunks}
    try:
        lines = seg_path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise UsageError(t("err.bundle_corrupt", file=seg_path, detail=str(exc))) from exc
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            row["ordinal"] = int(row["ordinal"])
            Chunk.from_api(row)
            rows.setdefault(row["document"], []).append(row)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise UsageError(t("err.bundle_corrupt", file=f"{seg_path}:{number}", detail=str(exc))) from exc
    try:
        for name, items in rows.items():
            items.sort(key=lambda r: r["ordinal"])
            chunks[name] = [Chunk.from_api(r) for r in items]
        for doc in manifest["documents"]:
            if doc_checksum(chunks[doc["name"]]) != doc["checksum"]:
                raise UsageError(t("err.checksum", name=repr(doc["name"])))
    except (KeyError, TypeError) as exc:
        raise UsageError(t("err.bundle_corrupt", file=manifest_path, detail=f"missing or invalid field {exc}")) from exc
    return Bundle(manifest, chunks)
