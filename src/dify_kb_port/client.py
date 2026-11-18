"""Thin Dify Knowledge API client: Bearer auth, paging, retries."""
from __future__ import annotations

import time
from typing import Any, Iterator

import httpx

from .errors import ApiError, AuthError, ConnectError, UsageError
from .i18n import t

PAGE_LIMIT = 100  # the service API caps `limit` at 100


def _sleep(seconds: float) -> None:
    """Indirection so tests can run without real waiting."""
    time.sleep(seconds)


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


class DifyClient:
    def __init__(self, base_url: str, api_key: str, *, max_retries: int = 5, connect_retries: int = 3,
                 not_completed_retries: int = 120, timeout: float = 120.0):
        if not base_url.startswith(("http://", "https://")):
            raise UsageError(t("err.url_scheme", url=base_url))
        self.base_url = base_url.rstrip("/")
        self._key = api_key
        self.max_retries = max_retries
        self.connect_retries = connect_retries  # no answer at all: about 7 s of waiting, then a clear error
        self.not_completed_retries = not_completed_retries
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "DifyClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- core request ----------------------------------------------------
    def _redact(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    @staticmethod
    def _message(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            if isinstance(body, dict):
                return str(body.get("message") or body.get("code") or resp.text[:200])
        except ValueError:
            pass
        return resp.text[:200]

    def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
        attempt = 0  # counts 429/5xx/network retries
        waits = 0  # counts "document not completed" waits
        while True:
            try:
                resp = self._http.request(method, path, params=params, json=json)
            except httpx.TransportError as exc:
                # 연결 자체가 안 된 경우만 POST 를 다시 보낸다. 요청이 나간 뒤 응답이 끊겼다면
                # 서버가 이미 썼을 수 있어 재시도하면 문서가 둘 생긴다(5xx POST 를 재시도하지 않는 이유와 같다).
                never_sent = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
                if attempt >= self.connect_retries or (method.upper() == "POST" and not never_sent):
                    raise ConnectError(self.base_url, self._redact(str(exc) or type(exc).__name__)) from exc
                _sleep(min(2.0 ** attempt, 30.0))
                attempt += 1
                continue
            status = resp.status_code
            if status < 400:
                if not resp.content:
                    return {}
                try:
                    return resp.json()
                except ValueError as exc:
                    raise UsageError(t("err.not_json", url=self.base_url)) from exc
            msg = self._redact(self._message(resp))
            if status in (401, 403):
                raise AuthError(status, msg, self.base_url)
            # a 5xx on POST may mean the server committed the write; retrying could duplicate it
            retryable = status == 429 or (status >= 500 and method.upper() != "POST")
            if retryable:
                if attempt >= self.max_retries:
                    raise ApiError(status, msg)
                wait = _retry_after(resp)
                _sleep(wait if wait is not None else min(2.0 ** attempt, 30.0))
                attempt += 1
                continue
            if status == 400 and "not completed" in msg.lower():
                if waits >= self.not_completed_retries:
                    raise ApiError(status, msg)
                _sleep(1.0)
                waits += 1
                continue
            raise ApiError(status, msg)

    # -- paging ----------------------------------------------------------
    def pages(self, path: str, params: dict | None = None) -> Iterator[dict]:
        """Yield every item once. Ends on has_more, total or an empty page, whichever is reliable.

        Servers have capped `limit` without fixing `has_more`, and repeated an item across
        pages, so ids are de-duplicated and `total` wins when `has_more` says "no" too early.
        """
        seen: set[str] = set()
        page = 1
        while True:
            body = self.request("GET", path, params={**(params or {}), "page": page, "limit": PAGE_LIMIT})
            data = body.get("data") or []
            fresh = [item for item in data if item.get("id") not in seen]
            for item in fresh:
                seen.add(item["id"])
                yield item
            total = body.get("total")
            if isinstance(total, int) and len(seen) >= total:
                return
            if not data:
                if isinstance(total, int) and len(seen) < total:
                    raise ApiError(200, f"{path}: server reported total={total} but only {len(seen)} items were returned")
                return
            if not fresh:
                raise ApiError(200, f"{path}: page {page} returned only items already seen")
            if not isinstance(total, int) and not body.get("has_more"):
                return
            page += 1

    # -- endpoints ---------------------------------------------------------
    def list_datasets(self, limit: int = 1) -> dict:
        return self.request("GET", "/datasets", params={"page": 1, "limit": limit})

    def get_dataset(self, dataset_id: str) -> dict:
        return self.request("GET", f"/datasets/{dataset_id}")

    def create_dataset(self, body: dict) -> dict:
        return self.request("POST", "/datasets", json=body)

    def metadata_fields(self, dataset_id: str) -> list[dict]:
        return self.request("GET", f"/datasets/{dataset_id}/metadata").get("doc_metadata") or []

    def create_metadata_field(self, dataset_id: str, name: str, type_: str) -> dict:
        return self.request("POST", f"/datasets/{dataset_id}/metadata", json={"type": type_, "name": name})

    def set_document_metadata(self, dataset_id: str, document_id: str, metadata_list: list[dict]) -> None:
        self.request("POST", f"/datasets/{dataset_id}/documents/metadata", json={
            "operation_data": [{"document_id": document_id, "metadata_list": metadata_list}]})

    def documents(self, dataset_id: str) -> Iterator[dict]:
        return self.pages(f"/datasets/{dataset_id}/documents")

    def create_document_by_text(self, dataset_id: str, body: dict) -> dict:
        return self.request("POST", f"/datasets/{dataset_id}/document/create-by-text", json=body)

    def indexing_status(self, dataset_id: str, batch: str) -> list[dict]:
        return self.request("GET", f"/datasets/{dataset_id}/documents/{batch}/indexing-status").get("data") or []

    def delete_document(self, dataset_id: str, document_id: str) -> None:
        self.request("DELETE", f"/datasets/{dataset_id}/documents/{document_id}")

    def set_documents_status(self, dataset_id: str, action: str, document_ids: list[str]) -> None:
        self.request("PATCH", f"/datasets/{dataset_id}/documents/status/{action}", json={"document_ids": document_ids})

    def segments(self, dataset_id: str, document_id: str) -> Iterator[dict]:
        return self.pages(f"/datasets/{dataset_id}/documents/{document_id}/segments")

    def create_segments(self, dataset_id: str, document_id: str, items: list[dict]) -> list[dict]:
        out = self.request("POST", f"/datasets/{dataset_id}/documents/{document_id}/segments", json={"segments": items})
        return out.get("data") or []

    def update_segment(self, dataset_id: str, document_id: str, segment_id: str, fields: dict) -> dict:
        out = self.request("POST", f"/datasets/{dataset_id}/documents/{document_id}/segments/{segment_id}",
                           json={"segment": fields})
        return out.get("data") or {}

    def delete_segment(self, dataset_id: str, document_id: str, segment_id: str) -> None:
        self.request("DELETE", f"/datasets/{dataset_id}/documents/{document_id}/segments/{segment_id}")
