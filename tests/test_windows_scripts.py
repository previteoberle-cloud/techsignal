"""Static checks of the Windows scripts (no Windows machine is available to this project's CI).
They catch the usual cmd mistakes; the scripts still need an acceptance run on real Windows."""
import re

import pytest
from conftest import ROOT

SCRIPTS = {name: (ROOT / "scripts" / name).read_text(encoding="utf-8") for name in ("start_windows.bat", "windows_schedule.bat")}


def _code(text):
    return [ln for ln in text.splitlines() if not ln.strip().upper().startswith("REM")]


@pytest.mark.parametrize("name", SCRIPTS)
def test_labels_and_gotos_match(name):
    lines = _code(SCRIPTS[name])
    labels = {ln.strip()[1:].lower() for ln in lines if ln.strip().startswith(":") and not ln.strip().startswith("::")}
    targets = {m.lower() for ln in lines for m in re.findall(r"goto\s+:(\w+)", ln, re.I)}
    assert targets <= labels, targets - labels


@pytest.mark.parametrize("name", SCRIPTS)
def test_parentheses_balance_and_no_stale_errorlevel_in_blocks(name):
    depth = 0
    for ln in _code(SCRIPTS[name]):
        body = re.sub(r'"[^"]*"', "", ln)                       # quoted strings do not count
        depth += body.count("(") - body.count(")")
        assert depth >= 0, ln
        if depth > 0 or body.rstrip().endswith("("):
            assert "%errorlevel%" not in ln.lower(), f"%errorlevel% inside a block is expanded too early: {ln}"
    assert depth == 0


@pytest.mark.parametrize("name", SCRIPTS)
def test_utf8_console_and_crlf_on_checkout(name):
    assert "chcp 65001" in SCRIPTS[name]
    assert "*.bat text eol=crlf" in (ROOT / ".gitattributes").read_text(encoding="utf-8")


def test_start_script_checks_python_and_install_and_never_touches_data():
    s = SCRIPTS["start_windows.bat"]
    assert "sys.version_info >= (3, 11)" in s and "没有找到 Python" in s
    assert re.search(r'pip install -e "\.\[ui\]"\s*\n\s*if errorlevel 1', s)       # install failure stops
    assert "techsignal-installed.txt" in s                                           # half-installed venv repaired
    assert ".techsignal" not in re.sub(r"(?im)^\s*REM.*$", "", s)                    # data dir never touched
    assert not re.search(r"(?i)\b(rmdir|rd)\b", s)
    assert "0.0.0.0" not in s and "--server.address" not in s                        # local-only default kept
    assert 'cd /d "%~dp0.."' in s                                                    # quoted: spaces / CJK in path


def test_schedule_script_creates_a_task_only_on_enable():
    s = SCRIPTS["windows_schedule.bat"]
    creates = [ln for ln in _code(s) if "schtasks /Create" in ln]
    assert len(creates) == 1
    enable_block = s[s.index("\n:enable"):s.index("\n:usage")]
    assert creates[0].strip() in enable_block
    assert "collect-all" in creates[0] and '\\"%RUNNER%\\"' in creates[0]           # quoted path with spaces
    assert "schtasks /Delete" in s and ".techsignal" not in re.sub(r"(?im)^\s*REM.*$", "", s)


def test_commands_used_by_the_scripts_exist():
    from techsignal import collector as C
    for argv in (["collect-all", "--help"], ["--help"]):
        with pytest.raises(SystemExit) as e:
            C.main(argv)
        assert e.value.code == 0
