"""AC-9: two languages, chosen by flag, environment or locale."""
from __future__ import annotations

import re

import pytest

from dify_kb_port import i18n
from dify_kb_port.cli import main


@pytest.mark.parametrize("lang, needle", [("en", "Export, import and verify"), ("ko", "내보내고, 가져오고, 검증")])
def test_help_both_langs(capsys, lang, needle):
    with pytest.raises(SystemExit) as exit_info:
        main(["--lang", lang, "--help"], {})
    assert exit_info.value.code == 0
    assert needle in capsys.readouterr().out


def test_subcommand_help_in_korean(capsys):
    with pytest.raises(SystemExit):
        main(["import", "--help", "--lang=ko"], {})
    assert "중단된 가져오기를 체크포인트에서 이어서 합니다" in capsys.readouterr().out


def test_lang_priority():
    env = {"DIFY_KB_PORT_LANG": "ko", "LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}
    assert i18n.resolve_lang("en", env) == "en"  # --lang beats everything
    assert i18n.resolve_lang(None, env) == "ko"  # then DIFY_KB_PORT_LANG
    assert i18n.resolve_lang(None, {"LC_ALL": "ko_KR.UTF-8", "LC_MESSAGES": "en_US", "LANG": "en"}) == "ko"  # LC_ALL
    assert i18n.resolve_lang(None, {"LC_MESSAGES": "ko_KR", "LANG": "en_US.UTF-8"}) == "ko"  # LC_MESSAGES
    assert i18n.resolve_lang(None, {"LANG": "ko_KR.UTF-8"}) == "ko"
    assert i18n.resolve_lang(None, {"LANG": "de_DE.UTF-8"}) == "en"  # not Korean -> English
    assert i18n.resolve_lang(None, {}) == "en"
    assert i18n.resolve_lang("fr", {"LANG": "ko_KR"}) == "en"


def test_env_variable_changes_cli_output(capsys):
    main(["diff", "/nonexistent", "/nonexistent"], {"DIFY_KB_PORT_LANG": "ko"})
    assert "오류:" in capsys.readouterr().err
    main(["diff", "/nonexistent", "/nonexistent"], {"LANG": "en_US.UTF-8"})
    assert "error:" in capsys.readouterr().err


def test_catalog_keys_match():
    en, ko = i18n.catalog("en"), i18n.catalog("ko")
    assert set(en) == set(ko)
    for key in en:  # same placeholders in both languages
        assert sorted(re.findall(r"\{(\w+)", en[key])) == sorted(re.findall(r"\{(\w+)", ko[key])), key
        assert en[key].strip() and ko[key].strip()
