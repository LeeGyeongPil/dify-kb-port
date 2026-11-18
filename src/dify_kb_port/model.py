"""Chunk value object and the hash that defines "identical"."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    content: str
    answer: str = ""
    keywords: tuple[str, ...] = ()
    enabled: bool = True
    children: tuple[str, ...] = ()  # child chunk contents (hierarchical_model), exported and hashed only

    @classmethod
    def from_api(cls, seg: dict) -> "Chunk":
        """Normalise a segment from the API or from segments.jsonl (None and "" are the same)."""
        children = tuple(c.get("content", "") for c in (seg.get("child_chunks") or []))
        return cls(
            content=seg.get("content") or "",
            answer=seg.get("answer") or "",
            keywords=tuple(sorted(seg.get("keywords") or [])),
            enabled=bool(seg.get("enabled", True)),
            children=children,
        )


def chunk_hash(chunk: Chunk) -> str:
    """SHA-256 over content, answer, sorted keywords, enabled and child chunk contents."""
    payload = json.dumps(
        {
            "content": chunk.content,
            "answer": chunk.answer,
            "keywords": sorted(chunk.keywords),
            "enabled": chunk.enabled,
            "children": list(chunk.children),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def differing_fields(a: Chunk, b: Chunk) -> list[str]:
    """Names of the fields that differ, for human-readable reports."""
    names = ("content", "answer", "keywords", "enabled", "children")
    return [n for n in names if getattr(a, n) != getattr(b, n)]
