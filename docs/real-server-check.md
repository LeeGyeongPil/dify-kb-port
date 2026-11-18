# Checking against a real Dify server

The tests and `demo` use a practice mock that reproduces the Dify 1.20.0 service API contract. It is not a real Dify server, so run this checklist once against a real instance (or two) before trusting a production migration.

1. Start Dify (for example the official docker compose) and configure an embedding model in the workspace.
2. In the console, create a knowledge base `faq-src` and upload a text file with five short paragraphs.
3. Make the same hand edits as `dify-kb-port demo`: rewrite one chunk and add keywords, disable one chunk, add one chunk manually, add a metadata field `team` and set `team=cs` on the document.
4. Create a Knowledge API key and set `DIFY_SRC_API_URL`, `DIFY_SRC_API_KEY`, `DIFY_DST_API_URL`, `DIFY_DST_API_KEY` (the same instance is fine for the check).
5. Run:
   ```
   dify-kb-port export --dataset <faq-src id> --out ./faq-bundle
   dify-kb-port import ./faq-bundle --dry-run
   dify-kb-port import ./faq-bundle --name "faq-copy"
   ```
   Expected: `identical: 6/6`, `result: no differences`, exit code 0.
6. Things worth watching (they differ between Dify versions and the mock cannot prove them):
   - editing only `enabled` through the segment API without sending `content`;
   - Q&A (`qa_model`) documents: the target generates one question/answer pair when the document is created, so a system model must be configured;
   - a long chunk that the server splits again (reconcile should fix it);
   - Dify Cloud rate limits (HTTP 429): the tool waits and retries.
7. Report differences as an issue with the exact output of `verify --json`.
