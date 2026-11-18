"""AC-5: an interrupted import resumes at document granularity."""
from __future__ import annotations

from dify_kb_port.cli import main
from dify_kb_port.exporter import export_dataset

from conftest import make_docs


def _target_dataset(mock, name):
    return next(d for d in mock.datasets.values() if d["name"] == name)


def test_resume_after_failure(client, mock, env, tmp_path, capsys):
    dataset_id = make_docs(client, ["a.md", "b.md", "c.md"])
    export_dataset(client, dataset_id, tmp_path / "b")
    bundle = str(tmp_path / "b")

    # the server answers 500 once document 1 exists, i.e. while document 2 is being processed
    mock.faults.create_500_after = mock.create_text_calls + 1
    assert main(["import", bundle, "--name", "copy"], env) == 3
    assert (tmp_path / "b" / ".import-state.json").exists()
    assert "--resume" in capsys.readouterr().err

    mock.faults.create_500_after = None
    before = mock.create_text_calls
    assert main(["import", bundle, "--name", "copy", "--resume"], env) == 0
    assert mock.create_text_calls - before == 2  # document 1 was not created again

    target = _target_dataset(mock, "copy")
    docs = list(client.documents(target["id"]))
    assert sorted(d["name"] for d in docs) == ["a.md", "b.md", "c.md"]  # exactly 3, no duplicates
    assert "no differences" in capsys.readouterr().out
    assert not (tmp_path / "b" / ".import-state.json").exists()


def test_rerun_without_resume_is_refused(client, mock, env, tmp_path, capsys):
    dataset_id = make_docs(client, ["a.md", "b.md"])
    export_dataset(client, dataset_id, tmp_path / "b")
    mock.faults.create_500_after = mock.create_text_calls
    assert main(["import", str(tmp_path / "b")], env) == 3
    mock.faults.create_500_after = None
    assert main(["import", str(tmp_path / "b")], env) == 2
    assert "--resume" in capsys.readouterr().err


def test_resume_without_checkpoint_is_refused(client, env, tmp_path):
    dataset_id = make_docs(client, ["a.md"])
    export_dataset(client, dataset_id, tmp_path / "b")
    assert main(["import", str(tmp_path / "b"), "--resume"], env) == 2
