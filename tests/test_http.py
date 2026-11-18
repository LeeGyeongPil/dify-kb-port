"""AC-6: retries, waiting for indexing, and clear authentication errors."""
from __future__ import annotations

import pytest

from dify_kb_port.cli import main
from dify_kb_port.client import DifyClient
from dify_kb_port.errors import ApiError, AuthError, UsageError
from dify_kb_port.exporter import export_dataset

from conftest import KEY, make_docs


def test_retry_after_429(client, mock, sleeps):
    mock.faults.rate_limit = 1
    assert client.list_datasets()["data"] == []
    assert sleeps == [2.0]  # waited exactly what Retry-After said


def test_exponential_backoff_then_give_up(mock, sleeps):
    mock.faults.rate_limit = 99
    with DifyClient(mock.url, KEY, max_retries=3) as c:
        with pytest.raises(ApiError) as err:
            c.list_datasets()
    assert err.value.status == 429
    assert len(sleeps) == 3


def test_waits_until_completed(client, mock, sleeps):
    dataset_id = make_docs(client, ["a.md"])
    doc = next(iter(client.documents(dataset_id)))
    mock.faults.reject_segment_ops = 2  # "Document is not completed." twice
    created = client.create_segments(dataset_id, doc["id"], [{"content": "late chunk"}])
    assert created[0]["content"] == "late chunk"
    assert sleeps == [1.0, 1.0]


@pytest.mark.parametrize("lang, needle", [("en", "Knowledge API key"), ("ko", "Knowledge API 키")])
def test_auth_error_exit_2(client, mock, env, tmp_path, capsys, lang, needle):
    dataset_id = make_docs(client, ["a.md"])
    export_dataset(client, dataset_id, tmp_path / "b")
    bad = {**env, "DIFY_DST_API_KEY": "dataset-wrong"}
    assert main(["verify", str(tmp_path / "b"), "--dataset", dataset_id, "--lang", lang], bad) == 2
    err = capsys.readouterr().err
    assert needle in err and "1.18" in err
    assert "dataset-wrong" not in err  # the key is never echoed


def test_missing_environment_variable_exit_2(tmp_path, capsys):
    assert main(["export", "--dataset", "x", "--out", str(tmp_path / "o")], {}) == 2
    assert "DIFY_SRC_API_URL" in capsys.readouterr().err


def test_url_must_be_http():
    with pytest.raises(UsageError):
        DifyClient("localhost/v1", "k")


def test_auth_error_type(mock):
    with DifyClient(mock.url, "nope") as c:
        with pytest.raises(AuthError):
            c.list_datasets()
