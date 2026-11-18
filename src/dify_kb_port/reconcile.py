"""Pure reconcile logic: turn "what the server built" into "what we exported".

No network here. `reconcile` compares the exported chunk list with the chunks the target
created (in order) and returns the operations that make them equal. Alignment is by ordinal,
which is the only thing that keeps chunk order stable (the API appends new chunks at the end).
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import Chunk


@dataclass(frozen=True)
class Op:
    kind: str  # update | create | delete | enable | disable
    index: int  # 0-based ordinal in the document
    segment_id: str | None = None
    chunk: Chunk | None = None


def reconcile(desired: list[Chunk], actual: list[tuple[str, Chunk]], *, include_state: bool = True) -> list[Op]:
    """`actual` is [(segment_id, Chunk)] in position order.

    Content operations (update/create/delete) come first. Enable/disable operations come last
    because editing a disabled chunk is refused by some Dify versions; pass
    `include_state=False` to get the content operations only.
    """
    ops: list[Op] = []
    shared = min(len(desired), len(actual))
    for i in range(shared):
        seg_id, have = actual[i]
        want = desired[i]
        if (have.content, have.answer, have.keywords, have.children) != (want.content, want.answer, want.keywords, want.children):
            ops.append(Op("update", i, seg_id, want))
    for i in range(shared, len(desired)):
        ops.append(Op("create", i, None, desired[i]))
    for i in range(shared, len(actual)):
        ops.append(Op("delete", i, actual[i][0]))
    if include_state:
        for i in range(shared):
            seg_id, have = actual[i]
            want = desired[i]
            if have.enabled != want.enabled:
                ops.append(Op("enable" if want.enabled else "disable", i, seg_id))
        # created chunks start enabled; disabling them needs their ids, so a second pass handles it
    return ops
