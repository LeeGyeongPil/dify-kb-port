# Bundle format (`dify-kb-port/1`)

A bundle is a directory with two files. `manifest.json` is written last, so a bundle without it is incomplete.

## `manifest.json`

| Field | Meaning |
|---|---|
| `format` | `dify-kb-port/<major>`. This version reads major `1`; any other major exits with code 2. Minor changes only add fields. |
| `tool_version`, `created_at` | Writer version and UTC time. |
| `source.url`, `source.dataset_id` | Where it came from. The URL has credentials, query and fragment removed. |
| `dataset` | `name`, `indexing_technique`, `doc_form`, `embedding_model`, `embedding_model_provider` of the source (informational: the target re-embeds with its own model). |
| `metadata_fields` | `[{name, type}]` metadata field definitions (`string`, `number`, `time`). |
| `separator` | The unique separator used on import to fix chunk boundaries. Chosen so it occurs in no chunk. Never contains a newline or backslash. |
| `documents[]` | `name` (made unique with a ` (2)` suffix if needed), `doc_form`, `enabled`, `archived`, `metadata` (`[{name, type, value}]`), `chunk_count`, `checksum`. |

`checksum` is the SHA-256 of the concatenated chunk hashes of the document, in ordinal order. Reading a bundle recomputes it and refuses a modified or truncated bundle.

Reading also checks the shape before anything else is done: the manifest must be a JSON object; `dataset.name`, `dataset.indexing_technique`, `separator` and every document's `name`, `doc_form` and `checksum` must be strings; `documents`, `metadata_fields` and each document's `metadata` must be lists of objects. Anything else exits with code 2 and asks you to export again.

## `segments.jsonl`

One JSON object per line: `document`, `ordinal` (1-based), `content`, `answer`, `keywords`, `enabled`, `child_chunks` (`[{content}]`), `summary`.
Signed file URLs (`sign_content`) and ids are never stored.

## Chunk hash (the definition of "identical")

SHA-256 of the JSON (sorted keys, UTF-8) of: `content`, `answer`, sorted `keywords`, `enabled`, list of child chunk contents.
`null` and an empty string are the same. Chunks are matched by `(document name, ordinal)`.
Document-level `enabled`, `archived` and metadata values are compared separately.

## Checkpoint

`.import-state.json` inside the bundle directory while an import is unfinished: target URL (no credentials), target dataset id, metadata field ids and per-document status. It is deleted after a fully successful import.
