"""Build ChangesReport fixtures with changedetection.io's OWN report builder.

changedetectionio/changes_report.py imports only difflib/datetime, so it can be loaded by file path
without installing changedetection.io's dependencies. The upstream source is optional and located
by techsignal.upstream (TECHSIGNAL_CDIO_SOURCE, or scripts/fetch_changedetection.sh for the pinned
commit); without it, fixture checks are skipped, nothing else is affected. We feed it minimal stand-in watch objects that
expose exactly the attributes it reads (history, get_history_snapshot, label, last_changed, get) and
store its real output. That keeps fixtures faithful to GET /api/v1/changes?format=json.

    python -m techsignal.fixtures            # regenerate every scenario/demo report in place
    python -m techsignal.fixtures --check    # fail if any stored report differs from a fresh build
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from . import upstream

TECHSIGNAL_ROOT = upstream.PROJECT_ROOT


def load_upstream_builder():
    """The upstream changes_report module, or None when no changedetection.io source is available."""
    path = upstream.changes_report_path()
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("_cdio_changes_report_upstream", path)
    mod = importlib.util.module_from_spec(spec)
    # don't leave a __pycache__ behind inside the upstream package
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = previous
    return mod


class StandInWatch(dict):
    """Only what changes_report.watch_changes()/group_changes() read from a Watch."""

    def __init__(self, uuid: str, title: str, url: str, snapshots: dict, error: str | None = None):
        super().__init__(uuid=uuid, title=title, url=url, last_error=error)
        self._snapshots = {int(k): v for k, v in snapshots.items()}

    @property
    def history(self):
        return {str(k): f"{k}.txt" for k in sorted(self._snapshots)}

    def get_history_snapshot(self, timestamp=None):
        return self._snapshots.get(int(timestamp))

    @property
    def label(self):
        return self.get("title") or self.get("url")

    @property
    def last_changed(self):
        keys = sorted(self._snapshots)
        return keys[-1] if len(keys) > 1 else 0


def build_report(spec: dict, builder=None) -> dict:
    builder = builder or load_upstream_builder()
    if builder is None:
        raise RuntimeError(upstream.HOW_TO)
    watches = [StandInWatch(w["uuid"], w["title"], w["url"], w["snapshots"], w.get("error"))
               for w in spec["watches"]]
    return builder.group_changes(watches, spec["from"], spec["to"], mode=spec.get("mode", "net_change"),
                                 context=spec.get("context", 1), max_lines=spec.get("max_lines", 200))


def fixture_files() -> list[Path]:
    files = sorted((TECHSIGNAL_ROOT / "tests" / "scenarios").glob("*.json"))
    files.append(TECHSIGNAL_ROOT / "demo" / "demo_source.json")
    return [f for f in files if f.exists()]


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    check = "--check" in argv
    builder = load_upstream_builder()
    if builder is None:
        print(upstream.HOW_TO, file=sys.stderr)
        return 2
    if upstream.matches_pinned():
        print(f"上游 changes_report.py 与固定版本一致（changedetection.io {upstream.PINNED_VERSION}，"
              f"提交 {upstream.PINNED_COMMIT[:10]}）")
    else:
        print(f"注意：上游 changes_report.py 与固定版本（{upstream.PINNED_COMMIT[:10]}）不同，"
              "以下比较针对你提供的版本", file=sys.stderr)
    bad = 0
    for path in fixture_files():
        spec = json.loads(path.read_text(encoding="utf-8"))
        if "watches" not in spec:
            continue  # raw-input scenarios carry their own (deliberately broken) input
        report = build_report(spec, builder)
        target = TECHSIGNAL_ROOT / "demo" / "changes_report.json" if path.name == "demo_source.json" else path
        if path.name == "demo_source.json":
            stored = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
            if check:
                bad += stored != report
            else:
                target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            if check:
                bad += spec.get("report") != report
            else:
                spec["report"] = report
                path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{'checked' if check else 'built'} {target.relative_to(TECHSIGNAL_ROOT)}")
    if bad:
        print(f"{bad} 个 Fixture 与上游生成结果不一致", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
