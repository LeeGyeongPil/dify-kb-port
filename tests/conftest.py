"""Shared fixtures: a practice mock Dify per test, no network, no real waiting, isolated env."""
from __future__ import annotations

import pytest

from dify_kb_port import client as client_mod
from dify_kb_port import i18n
from dify_kb_port.client import DifyClient
from dify_kb_port.demo import seed_source
from dify_kb_port.mock_server import MockDify

KEY = "dataset-test-key-0123456789"


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    """Record sleeps instead of sleeping; clear every variable that could pick a language or server."""
    sleeps: list[float] = []
    monkeypatch.setattr(client_mod, "_sleep", sleeps.append)
    for var in ("DIFY_KB_PORT_LANG", "LC_ALL", "LC_MESSAGES", "LANG", "DIFY_SRC_API_URL", "DIFY_SRC_API_KEY",
                "DIFY_DST_API_URL", "DIFY_DST_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    i18n.set_lang("en")
    yield sleeps
    i18n.set_lang("en")


@pytest.fixture
def sleeps(isolate):
    return isolate


@pytest.fixture
def mock():
    with MockDify(key=KEY) as server:
        yield server


@pytest.fixture
def client(mock):
    with DifyClient(mock.url, KEY) as c:
        yield c


@pytest.fixture
def env(mock):
    """Environment for the CLI: source and target are the same mock server."""
    return {"DIFY_SRC_API_URL": mock.url, "DIFY_SRC_API_KEY": KEY, "DIFY_DST_API_URL": mock.url, "DIFY_DST_API_KEY": KEY}


@pytest.fixture
def seeded(client):
    """Dataset id of the hand-edited demo knowledge base (6 chunks)."""
    return seed_source(client)


def make_docs(client: DifyClient, names: list[str], name: str = "multi") -> str:
    """A dataset with one small document per name, three chunks each."""
    dataset_id = client.create_dataset({"name": name, "indexing_technique": "high_quality"})["id"]
    for n in names:
        client.create_document_by_text(dataset_id, {
            "name": n, "text": f"{n} one\n\n{n} two\n\n{n} three", "indexing_technique": "high_quality",
            "process_rule": {"mode": "automatic"}})
    return dataset_id
