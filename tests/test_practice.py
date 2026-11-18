"""Round-2 QA fixes: `mock-server --seed` practice path, honest summary for skipped documents,
connection errors without "HTTP 0", busy port."""
from __future__ import annotations

import re
import shlex
import socket
import threading
import types

import httpx
import pytest

from dify_kb_port import cli
from dify_kb_port.cli import main
from dify_kb_port.client import DifyClient
from dify_kb_port.errors import ConnectError
from dify_kb_port.exporter import export_dataset
from dify_kb_port.i18n import catalog
from dify_kb_port.mock_server import DEFAULT_KEY

from conftest import make_docs


class _ServerInThread:
    """Runs `dify-kb-port mock-server ...` through cli.main in a thread; stop() ends it like Ctrl-C."""

    def __init__(self, monkeypatch, argv: list[str]):
        self.ready, self._stop = threading.Event(), threading.Event()
        outer = self

        class Event:  # replaces the CLI's endless wait only
            def wait(self):
                outer.ready.set()
                outer._stop.wait(30)
                raise KeyboardInterrupt

        monkeypatch.setattr(cli, "threading", types.SimpleNamespace(Event=Event))
        self.code: int | None = None
        self._thread = threading.Thread(target=self._run, args=(argv,), daemon=True)
        self._thread.start()
        assert self.ready.wait(10), "mock-server did not start"

    def _run(self, argv: list[str]) -> None:
        self.code = main(argv, {})

    def stop(self) -> int | None:
        self._stop.set()
        self._thread.join(10)
        return self.code


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_mock_server_seed_supports_full_practice_run(monkeypatch, capsys, tmp_path, lang):
    server = _ServerInThread(monkeypatch, ["mock-server", "--port", "0", "--seed", "--lang", lang])
    try:
        out = capsys.readouterr().out
        url = re.search(r"(http://127\.0\.0\.1:\d+/v1)", out).group(1)
        dataset = re.search(r"dataset id ([0-9a-f-]{36})", out).group(1)
        env = {"DIFY_SRC_API_URL": url, "DIFY_SRC_API_KEY": DEFAULT_KEY,
               "DIFY_DST_API_URL": url, "DIFY_DST_API_KEY": DEFAULT_KEY}
        # 서버가 출력한 명령을 그대로(묶음 경로만 tmp 로 바꿔) 실행한다: 안내문 자체가 동작해야 한다.
        commands = [line.strip() for line in out.splitlines() if line.strip().startswith("dify-kb-port ")]
        assert len(commands) == 4
        bundle = str(tmp_path / "faq-bundle")
        imported = None
        for line in commands:
            if line.startswith("dify-kb-port verify"):  # the placeholder is "<id printed by import>"
                assert "<" in line
                line = line.split("--dataset")[0] + f"--dataset {imported}"
            argv = shlex.split(line.replace("./faq-bundle", bundle))[1:]
            assert main(argv + ["--lang", lang], env) == 0, line
            step = capsys.readouterr().out
            if argv[0] == "export":
                assert dataset in line
            if argv[0] == "import" and "--dry-run" not in argv:
                imported = re.search(r"([0-9a-f-]{36})\s*$", step.split("\n")[0]).group(1)
                assert "6/6" in step
            if argv[0] == "verify":
                assert "6/6" in step
        assert imported and imported != dataset
    finally:
        assert server.stop() == 0


def test_mock_server_without_seed_stays_empty(monkeypatch, capsys):
    server = _ServerInThread(monkeypatch, ["mock-server", "--port", "0"])
    try:
        out = capsys.readouterr().out
        assert "dataset id" not in out and "--dataset" not in out
    finally:
        assert server.stop() == 0


def test_demo_next_line_points_to_seed():
    for lang in ("en", "ko"):
        assert "mock-server --seed" in catalog(lang)["demo.next"]


def test_mock_server_busy_port_exits_2(capsys):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert main(["mock-server", "--port", str(port)], {}) == 2
    err = capsys.readouterr().err
    assert "--port" in err and "Traceback" not in err


# -- Q-4: --into with the default --on-conflict skip ---------------------------------------

def _first_doc(client, dataset_id):
    doc = next(iter(client.documents(dataset_id)))
    segs = sorted(client.segments(dataset_id, doc["id"]), key=lambda s: s["position"])
    return doc, segs


def test_into_skip_warns_when_kept_document_differs(client, seeded, env, tmp_path, capsys):
    bundle = tmp_path / "b"
    export_dataset(client, seeded, bundle)
    doc, segs = _first_doc(client, seeded)
    client.update_segment(seeded, doc["id"], segs[1]["id"], {"content": "edited on the target first"})
    code = main(["import", str(bundle), "--into", seeded], env)
    out = capsys.readouterr().out
    assert code == 0  # skipping is what the user asked for; the import itself did not fail
    assert "no differences" not in out and "0/0" not in out
    assert "nothing was imported" in out
    assert "1 skipped document(s) differ from the bundle" in out and "faq.md" in out


def test_into_skip_reports_match_when_kept_document_is_identical(client, seeded, env, tmp_path, capsys):
    bundle = tmp_path / "b"
    export_dataset(client, seeded, bundle)
    assert main(["import", str(bundle), "--into", seeded, "--lang", "ko"], env) == 0
    out = capsys.readouterr().out
    assert "결과: 차이 없음" not in out and "가져온 문서가 없습니다" in out
    assert "건너뛴 문서는 이미 묶음과 같습니다(1개): faq.md" in out


def test_into_skip_mixed_imported_and_kept(client, env, tmp_path, capsys):
    dataset = make_docs(client, ["a.md", "b.md"])
    bundle = tmp_path / "b"
    export_dataset(client, dataset, bundle)
    docs = {d["name"]: d for d in client.documents(dataset)}
    client.delete_document(dataset, docs["b.md"]["id"])  # b.md will be imported again
    segs = sorted(client.segments(dataset, docs["a.md"]["id"]), key=lambda s: s["position"])
    client.update_segment(dataset, docs["a.md"]["id"], segs[0]["id"], {"content": "drifted"})  # a.md is kept
    code = main(["import", str(bundle), "--into", dataset], env)
    out = capsys.readouterr().out
    assert code == 0
    assert "identical: 3/3" in out  # the imported b.md verifies
    assert "1 skipped document(s) differ from the bundle" in out and "a.md" in out
    # the documented way to see the difference really shows it
    assert main(["verify", str(bundle), "--dataset", dataset], env) == 1


# -- Q-5: connection errors ----------------------------------------------------------------

@pytest.mark.parametrize("lang, needle", [("en", "cannot reach"), ("ko", "연결할 수 없습니다")])
def test_unreachable_server_has_hint_and_no_http_0(capsys, sleeps, lang, needle):
    url = f"http://127.0.0.1:{_free_port()}/v1"
    code = main(["export", "--dataset", "x", "--out", "unused", "--lang", lang],
                {"DIFY_SRC_API_URL": url, "DIFY_SRC_API_KEY": "k"})
    err = capsys.readouterr().err
    assert code == 3
    assert needle in err and url in err and "DIFY_SRC_API_URL" in err
    assert "HTTP 0" not in err and "Traceback" not in err
    assert sleeps == [1.0, 2.0, 4.0]  # a few short retries (about 7 s), not half a minute of silence


def _client_with(handler) -> DifyClient:
    client = DifyClient("http://mock.invalid/v1", "k")
    client._http.close()
    client._http = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(handler))
    return client


def test_get_is_retried_after_connect_error(sleeps):
    calls = []

    def handler(request):
        calls.append(request.method)
        if len(calls) < 3:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"id": "d"})

    with _client_with(handler) as client:
        assert client.get_dataset("d") == {"id": "d"}
    assert len(calls) == 3 and len(sleeps) == 2


def test_post_is_not_resent_after_read_timeout(sleeps):
    calls = []

    def handler(request):
        calls.append(request.method)
        raise httpx.ReadTimeout("timed out", request=request)  # the request reached the server

    with _client_with(handler) as client, pytest.raises(ConnectError):
        client.create_dataset({"name": "x"})
    assert calls == ["POST"] and sleeps == []


def test_post_is_retried_when_never_sent(sleeps):
    calls = []

    def handler(request):
        calls.append(request.method)
        if len(calls) == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"id": "new"})

    with _client_with(handler) as client:
        assert client.create_dataset({"name": "x"}) == {"id": "new"}
    assert calls == ["POST", "POST"]
