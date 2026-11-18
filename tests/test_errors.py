"""Round-1 QA fixes: mock-server start, bad input without tracebacks, translated errors."""
from __future__ import annotations

import http.server
import json
import threading

import pytest

from dify_kb_port import cli
from dify_kb_port.cli import main
from dify_kb_port.exporter import export_dataset


class _Html(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # the Dify web UI answers 200 with HTML on a URL without /v1
        body = b"<html>Dify</html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_mock_server_starts_and_prints_listening_line(monkeypatch, capsys, lang):
    class StopNow:  # replaces cli.threading so only the CLI's own wait is interrupted
        class Event:
            def wait(self):
                raise KeyboardInterrupt

    monkeypatch.setattr(cli, "threading", StopNow)
    assert main(["mock-server", "--port", "0", "--lang", lang], {}) == 0
    out = capsys.readouterr().out
    assert "127.0.0.1" in out and "dataset-mock" in out
    assert "Ctrl-C" in out


def test_html_response_gives_fix_it_message(capsys):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Html)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        code = main(["export", "--dataset", "x", "--out", "unused"], {"DIFY_SRC_API_URL": url, "DIFY_SRC_API_KEY": "k"})
    finally:
        server.shutdown()
        server.server_close()
    err = capsys.readouterr().err
    assert code == 2
    assert "/v1" in err and "Traceback" not in err


def _bundle(client, seeded, tmp_path):
    out = tmp_path / "b"
    export_dataset(client, seeded, out)
    return out


@pytest.mark.parametrize("damage", ["bad_line", "bad_utf8", "no_documents", "missing_field"])
def test_broken_bundle_exits_2_without_traceback(client, seeded, env, tmp_path, capsys, damage):
    out = _bundle(client, seeded, tmp_path)
    seg, man = out / "segments.jsonl", out / "manifest.json"
    if damage == "bad_line":
        seg.write_text(seg.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    elif damage == "bad_utf8":
        seg.write_bytes(b"\xff\xfe\x00bad\n")
    elif damage == "no_documents":
        data = json.loads(man.read_text(encoding="utf-8"))
        del data["documents"]
        man.write_text(json.dumps(data), encoding="utf-8")
    else:
        seg.write_text('{"document": "x"}\n', encoding="utf-8")
    for command in (["verify", str(out), "--dataset", seeded], ["import", str(out), "--dry-run"]):
        assert main(command, env) == 2
        err = capsys.readouterr().err
        assert "Traceback" not in err and "export it again" in err


def test_korean_errors_have_no_english_sentence(client, seeded, env, tmp_path, capsys):
    out = _bundle(client, seeded, tmp_path)
    assert main(["export", "--dataset", seeded, "--out", str(out), "--lang", "ko"], env) == 2
    err = capsys.readouterr().err
    assert err.startswith("오류:") and "already exists" not in err and "--force" in err
    assert main(["import", str(out), "--resume", "--lang", "ko"], env) == 2
    err = capsys.readouterr().err
    assert "does not exist" not in err and "가져오기를 실행하세요" in err
    assert main(["verify", str(tmp_path), "--dataset", seeded, "--lang", "ko"], env) == 2
    assert "is not a bundle" not in capsys.readouterr().err


def test_export_checks_out_dir_before_downloading(client, seeded, tmp_path):
    out = tmp_path / "full"
    out.mkdir()
    (out / "x").write_text("x")
    calls = []
    original = client.get_dataset
    client.get_dataset = lambda *a, **k: calls.append(1) or original(*a, **k)
    from dify_kb_port.errors import UsageError
    with pytest.raises(UsageError):
        export_dataset(client, seeded, out)
    assert calls == []


def test_export_failure_has_no_resume_hint(env, capsys):
    assert main(["export", "--dataset", "nope", "--out", "unused-out"], env) == 3
    assert "--resume" not in capsys.readouterr().err


def test_index_timeout_must_be_positive(capsys):
    for bad in ("0", "-1", "abc"):
        with pytest.raises(SystemExit) as exit_info:
            main(["import", "somewhere", "--index-timeout", bad], {})
        assert exit_info.value.code == 2
    assert "0" in capsys.readouterr().err


def test_post_5xx_is_not_retried(client, mock, sleeps):
    from dify_kb_port.errors import ApiError
    dataset_id = client.create_dataset({"name": "x", "indexing_technique": "high_quality"})["id"]
    mock.faults.create_500_after = 0
    with pytest.raises(ApiError) as err:
        client.create_document_by_text(dataset_id, {"name": "a", "text": "t", "doc_form": "text_model",
                                                    "indexing_technique": "high_quality"})
    assert err.value.status == 500
    assert sleeps == []  # no automatic retry of a write that may have been committed


# -- round 2 (Q-3): manifest that is valid JSON but has the wrong shape ----------------------

def _set_manifest(man, change):
    data = json.loads(man.read_text(encoding="utf-8"))
    if callable(change):
        change(data)
        man.write_text(json.dumps(data), encoding="utf-8")
    else:
        man.write_text(change, encoding="utf-8")


MANIFEST_SHAPES = {
    "list": "[]",
    "string": '"manifest"',
    "null": "null",
    "documents_not_list": lambda d: d.update(documents={"faq.md": {}}),
    "document_not_object": lambda d: d.update(documents=[1]),
    "document_name_not_string": lambda d: d["documents"][0].update(name=["faq.md"]),
    "document_no_checksum": lambda d: d["documents"][0].pop("checksum"),
    "metadata_not_list": lambda d: d["documents"][0].update(metadata="team=cs"),
    "metadata_fields_not_list": lambda d: d.update(metadata_fields="team"),
    "dataset_missing": lambda d: d.pop("dataset"),
    "dataset_no_indexing": lambda d: d["dataset"].pop("indexing_technique"),
    "separator_missing": lambda d: d.pop("separator"),
}


@pytest.mark.parametrize("lang, needle", [("en", "export it again"), ("ko", "다시 내보내세요")])
@pytest.mark.parametrize("shape", sorted(MANIFEST_SHAPES))
def test_manifest_with_wrong_shape_exits_2(client, seeded, env, tmp_path, capsys, shape, lang, needle):
    out = _bundle(client, seeded, tmp_path)
    _set_manifest(out / "manifest.json", MANIFEST_SHAPES[shape])
    for command in (["verify", str(out), "--dataset", seeded], ["import", str(out), "--dry-run"],
                    ["import", str(out)], ["diff", str(out), f"src:{seeded}"]):
        assert main(command + ["--lang", lang], env) == 2, command
        err = capsys.readouterr().err
        assert "Traceback" not in err and needle in err, (command, err)


@pytest.mark.parametrize("content", ["[]", '{"documents": []}', '{"documents": {}, "target_url": "x"}', '{"documents": {"faq.md": "done"}}', "{bad"])
def test_checkpoint_with_wrong_shape_exits_2(client, seeded, env, tmp_path, capsys, content):
    out = _bundle(client, seeded, tmp_path)
    (out / ".import-state.json").write_text(content, encoding="utf-8")
    assert main(["import", str(out), "--resume"], env) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and "delete it" in err


def test_exported_bundle_passes_shape_check(client, seeded, tmp_path):
    from dify_kb_port.bundle import read_bundle
    out = _bundle(client, seeded, tmp_path)
    assert [d["name"] for d in read_bundle(out).documents] == ["faq.md"]
