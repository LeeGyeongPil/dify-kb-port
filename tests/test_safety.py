"""AC-8: no secrets on disk; dry-run writes nothing."""
from __future__ import annotations

from dify_kb_port.cli import main
from dify_kb_port.exporter import export_dataset
from dify_kb_port.importer import import_bundle

from conftest import KEY, make_docs


def _all_text(folder) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in folder.rglob("*") if p.is_file())


def test_bundle_has_no_secrets(client, mock, env, seeded, tmp_path, capsys):
    export_dataset(client, seeded, tmp_path / "b")
    text = _all_text(tmp_path / "b")
    assert "sign_content" not in text  # the API returned signed URLs; none were stored
    assert "sign=" not in text and "timestamp=" not in text
    assert KEY not in text

    # a failed import leaves a checkpoint; it must be clean as well
    mock.faults.create_500_after = mock.create_text_calls
    assert main(["import", str(tmp_path / "b")], env) == 3
    assert (tmp_path / "b" / ".import-state.json").exists()
    text = _all_text(tmp_path / "b")
    assert KEY not in text and "sign=" not in text
    assert KEY not in capsys.readouterr().err  # and not in error output


def test_dry_run_no_writes(client, mock, env, seeded, tmp_path, capsys):
    export_dataset(client, seeded, tmp_path / "b")
    writes_before = len(mock.write_requests())
    capsys.readouterr()
    assert main(["import", str(tmp_path / "b"), "--dry-run"], env) == 0
    assert len(mock.write_requests()) == writes_before  # no POST / PATCH / DELETE
    out = capsys.readouterr().out
    assert "would create 1 documents with 6 chunks" in out
    assert not (tmp_path / "b" / ".import-state.json").exists()


def test_export_refuses_non_empty_directory(client, seeded, tmp_path):
    export_dataset(client, make_docs(client, ["a.md"]), tmp_path / "b")
    import pytest
    from dify_kb_port.errors import UsageError
    with pytest.raises(UsageError):
        export_dataset(client, seeded, tmp_path / "b")
    export_dataset(client, seeded, tmp_path / "b", force=True)


def test_import_refuses_tampered_bundle(client, seeded, tmp_path):
    import pytest
    from dify_kb_port.errors import UsageError
    export_dataset(client, seeded, tmp_path / "b")
    seg_file = tmp_path / "b" / "segments.jsonl"
    seg_file.write_text(seg_file.read_text(encoding="utf-8").replace("Billing runs monthly.", "Billing runs never."),
                        encoding="utf-8")
    with pytest.raises(UsageError, match="checksum"):
        import_bundle(client, tmp_path / "b")
