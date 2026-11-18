"""AC-7: verify and diff find real differences, bundle-to-instance and instance-to-instance."""
from __future__ import annotations

import json

from dify_kb_port.cli import main
from dify_kb_port.client import DifyClient
from dify_kb_port.exporter import export_dataset
from dify_kb_port.importer import import_bundle
from dify_kb_port.mock_server import MockDify

from conftest import KEY


def _segments(client, dataset_id):
    doc = next(iter(client.documents(dataset_id)))
    return doc["id"], sorted(client.segments(dataset_id, doc["id"]), key=lambda s: s["position"])


def test_verify_detects_change(client, env, seeded, tmp_path, capsys):
    export_dataset(client, seeded, tmp_path / "b")
    target = import_bundle(client, tmp_path / "b", name="copy").dataset_id
    capsys.readouterr()
    assert main(["verify", str(tmp_path / "b"), "--dataset", target, "--json"], env) == 0

    doc_id, segs = _segments(client, target)
    client.update_segment(target, doc_id, segs[1]["id"], {"content": "Billing runs weekly.", "keywords": []})  # change #2
    client.delete_segment(target, doc_id, segs[5]["id"])  # delete the last chunk (#6)
    capsys.readouterr()
    assert main(["verify", str(tmp_path / "b"), "--dataset", target, "--json"], env) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["changed"] == [{"document": "faq.md", "ordinal": 2, "fields": ["content"]}]
    assert out["missing"] == [{"document": "faq.md", "ordinal": 6}]
    assert out["identical"] == 4 and out["total"] == 6


def test_verify_table_output_both_languages(client, env, seeded, tmp_path, capsys):
    export_dataset(client, seeded, tmp_path / "b")
    target = import_bundle(client, tmp_path / "b", name="copy").dataset_id
    doc_id, segs = _segments(client, target)
    client.delete_segment(target, doc_id, segs[5]["id"])
    capsys.readouterr()
    assert main(["verify", str(tmp_path / "b"), "--dataset", target, "--lang", "en"], env) == 1
    en = capsys.readouterr().out
    assert "identical: 5/6" in en and "missing (1):" in en and "faq.md #6" in en
    assert main(["verify", str(tmp_path / "b"), "--dataset", target, "--lang", "ko"], env) == 1
    ko = capsys.readouterr().out
    assert "동일: 5/6" in ko and "빠짐 (1):" in ko and "faq.md #6" in ko


def test_diff_instance_to_instance(client, seeded, tmp_path, capsys):
    with MockDify(key=KEY) as other:
        env = {"DIFY_SRC_API_URL": client.base_url, "DIFY_SRC_API_KEY": KEY,
               "DIFY_DST_API_URL": other.url, "DIFY_DST_API_KEY": KEY}
        export_dataset(client, seeded, tmp_path / "b")
        with DifyClient(other.url, KEY) as dst_client:
            target = import_bundle(dst_client, tmp_path / "b", name="copy").dataset_id
            capsys.readouterr()
            assert main(["diff", f"src:{seeded}", f"dst:{target}"], env) == 0
            assert main(["diff", str(tmp_path / "b"), f"dst:{target}"], env) == 0  # bundle vs instance too

            doc_id, segs = _segments(dst_client, target)
            dst_client.update_segment(target, doc_id, segs[0]["id"], {"content": "different", "keywords": []})
            dst_client.delete_segment(target, doc_id, segs[5]["id"])
            capsys.readouterr()
            assert main(["diff", f"src:{seeded}", f"dst:{target}", "--json"], env) == 1
        out = json.loads(capsys.readouterr().out)
        assert [(c["document"], c["ordinal"]) for c in out["changed"]] == [("faq.md", 1)]
        assert [(m["document"], m["ordinal"]) for m in out["missing"]] == [("faq.md", 6)]


def test_diff_rejects_unreadable_side(env, capsys):
    assert main(["diff", "/nonexistent/bundle", "dst:abc"], env) == 2
    assert "bundle" in capsys.readouterr().err
