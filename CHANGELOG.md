# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-11-19

### Added
- `export`, `import`, `verify`, `diff` commands for `text_model` and `qa_model` knowledge bases.
- Fixed chunk boundaries on import (unique separator) plus post-index reconcile.
- Hash-based verify/diff with exit codes 0/1/2/3.
- Resumable import with a per-document checkpoint, `--dry-run`.
- `import --into` with `--on-conflict skip|replace`; skipped documents are still compared and reported, never counted as verified.
- Practice mock Dify server: `demo` and `mock-server`; `mock-server --seed` creates the demo's hand-edited knowledge base and prints the commands to rehearse export, import and verify against it.
- Fix-it messages instead of tracebacks for wrong API URLs, unreachable servers, rejected keys, corrupted bundles and checkpoints.
- Create requests (POST) are never re-sent once they may have reached the server, to avoid duplicate documents.
- English and Korean output (`--lang`, `DIFY_KB_PORT_LANG`).
