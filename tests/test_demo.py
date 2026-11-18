"""AC-2 and AC-10: the keyless demo says it is a mock and computes its numbers."""
from __future__ import annotations

from dify_kb_port.cli import main


def test_demo_english(capsys):
    assert main(["demo", "--lang", "en"], {}) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("This is a practice mock")
    assert "identical: 6/6" in out
    assert "naive re-upload: 3/6 changed or missing" in out
    assert "disabled chunk stays disabled in the target: True" in out
    assert "{'team': 'cs'}" in out


def test_demo_korean(capsys):
    assert main(["demo", "--lang", "ko"], {}) == 0
    out = capsys.readouterr().out
    assert "연습용 목(mock)" in out and "동일: 6/6" in out and "6개 중 3개 바뀌거나 빠짐" in out
