"""R5: TechSignal must run from its own repository, without changedetection.io's source next to it,
and the workbench must keep API keys and logs of concurrent sessions apart."""
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import ROOT
from techsignal.workbench import viewmodel as vm
from techsignal.workbench.replay import fetch_live

from techsignal import upstream

CASE = ROOT / "cases" / "tenstorrent_blackhole"


# ---------------------------------------------------------------- upstream locator -----------------

def _fake_checkout(path: Path) -> Path:
    (path / "changedetectionio").mkdir(parents=True)
    (path / "changedetection.py").write_text("# stand-in\n")
    (path / "changedetectionio" / "changes_report.py").write_text("# stand-in\n")
    return path


def test_env_var_selects_a_checkout(tmp_path, monkeypatch):
    src = _fake_checkout(tmp_path / "cdio")
    monkeypatch.setenv("TECHSIGNAL_CDIO_SOURCE", str(src))
    assert upstream.cdio_source() == src.resolve()
    assert upstream.matches_pinned() is False          # a stand-in file is not the pinned version


def test_env_var_pointing_elsewhere_means_no_source(tmp_path, monkeypatch):
    monkeypatch.setenv("TECHSIGNAL_CDIO_SOURCE", str(tmp_path))
    assert upstream.cdio_source() is None and upstream.changes_report_path() is None


def _standalone_copy(tmp_path) -> Path:
    """The project as a standalone clone would see it: no changedetection.io anywhere above it."""
    dst = tmp_path / "TechSignal"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.egg-info"))
    return dst


def _run(cwd, code, **env):
    e = {k: v for k, v in os.environ.items() if k != "TECHSIGNAL_CDIO_SOURCE"}
    e.update(PYTHONPATH=str(cwd), PYTHONDONTWRITEBYTECODE="1", **env)
    return subprocess.run([sys.executable, *code], cwd=cwd, env=e, capture_output=True, text=True, timeout=120)


def test_standalone_clone_works_without_upstream(tmp_path):
    proj = _standalone_copy(tmp_path)
    r = _run(proj, ["-c", "from techsignal import upstream; print(upstream.cdio_source())"])
    assert r.returncode == 0 and r.stdout.strip() == "None"
    # the offline pipeline and the workbench data layer work
    r = _run(proj, ["-m", "techsignal", "run", "--changes", "demo/changes_report.json", "--theses", "demo/theses.json",
                    "--sources", "demo/sources.json", "--out", str(tmp_path / "o")])
    assert r.returncode == 0, r.stderr
    r = _run(proj, ["-c", "from techsignal.workbench import viewmodel as vm; c,p=vm.discover_cases(); "
                          "print(len(c), len(p), vm.load_batches(c[-1])[-1].overview()['headline'])"])
    assert r.returncode == 0 and r.stdout.startswith("2 0 "), r.stderr
    # developer tasks that need upstream say how to get it instead of failing obscurely
    r = _run(proj, ["-m", "techsignal.fixtures", "--check"])
    assert r.returncode == 2 and "fetch_changedetection.sh" in r.stderr
    r = _run(proj, ["-c", "from techsignal.live import LiveCdio, LiveCdioError\n"
                          "try:\n    LiveCdio('python').__enter__()\nexcept LiveCdioError as e:\n    print(e)"])
    assert "TECHSIGNAL_CDIO_SOURCE" in r.stdout


def test_cases_root_falls_back_to_current_directory(tmp_path, monkeypatch):
    shutil.copytree(CASE, tmp_path / "cases" / "tenstorrent_blackhole")
    monkeypatch.delenv("TECHSIGNAL_CASES_ROOT", raising=False)
    monkeypatch.setattr(vm, "CASES_ROOT", tmp_path / "not-there")
    monkeypatch.chdir(tmp_path)
    cases, problems = vm.discover_cases()
    assert len(cases) == 2 and problems == []


# ---------------------------------------------------------------- API key isolation ---------------

class FakeCdio:
    """Answers /api/v1/systeminfo and /api/v1/changes with the real captured report; records keys."""

    def __init__(self):
        self.keys = []
        report = (CASE / "output" / "source_changes_report.json").read_bytes()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.keys.append(self.headers.get("x-api-key"))
                body = b'{"version": "stub"}' if self.path.startswith("/api/v1/systeminfo") else report
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture()
def fake_cdio():
    f = FakeCdio()
    yield f
    f.httpd.shutdown()
    f.httpd.server_close()


def _case():
    return {c.case_id: c for c in vm.discover_cases()[0]}["tenstorrent-blackhole-70b"]


def test_live_fetch_passes_key_without_touching_the_environment(tmp_path, fake_cdio, monkeypatch):
    monkeypatch.delenv("TECHSIGNAL_CDIO_API_KEY", raising=False)
    secret = "sk-cdio-SECRET-0123456789"
    res = fetch_live(_case(), fake_cdio.url, secret, 0, workdir=tmp_path / "w")
    assert "TECHSIGNAL_CDIO_API_KEY" not in os.environ
    assert set(fake_cdio.keys) == {secret}
    for p in (tmp_path / "w").rglob("*"):
        if p.is_file():
            assert secret not in p.read_text(encoding="utf-8", errors="replace"), p.name
    assert secret not in res.log
    rec = json.loads((res.run_dirs[0] / "run_record.json").read_text(encoding="utf-8"))
    assert rec["input_mode"] == "live_api"


def test_concurrent_sessions_keep_keys_and_logs_apart(tmp_path, fake_cdio):
    results, errors = {}, []

    def session(name):
        try:
            results[name] = fetch_live(_case(), fake_cdio.url, f"key-{name}", 0, workdir=tmp_path / name)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=session, args=(n,)) for n in ("alice", "bob", "carol")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors
    counts = {k: fake_cdio.keys.count(k) for k in set(fake_cdio.keys)}
    assert set(counts) == {"key-alice", "key-bob", "key-carol"} and len(set(counts.values())) == 1
    for name, res in results.items():
        assert str(tmp_path / name) in res.log
        assert all(str(tmp_path / other) not in res.log for other in results if other != name)


def test_wrong_key_error_does_not_echo_the_key(tmp_path):
    class Deny(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(403)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b'""')
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Deny)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with pytest.raises(vm.LoadError) as e:
            fetch_live(_case(), f"http://127.0.0.1:{httpd.server_address[1]}", "sk-NEVER-SHOWN", 0, workdir=tmp_path)
        assert "sk-NEVER-SHOWN" not in str(e.value) and "鉴权失败" in str(e.value)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_fetch_script_pins_the_same_upstream_version():
    script = (ROOT / "scripts" / "fetch_changedetection.sh").read_text(encoding="utf-8")
    assert f'COMMIT="{upstream.PINNED_COMMIT}"' in script
    assert f'CHANGES_REPORT_SHA256="{upstream.CHANGES_REPORT_SHA256}"' in script
    assert upstream.UPSTREAM_REPO in script
