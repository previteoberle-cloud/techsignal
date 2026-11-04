"""Optional changedetection.io source checkout.

TechSignal itself never needs changedetection.io's source: it talks to a running instance over HTTP.
A source checkout is only used by two developer tasks:
  - techsignal.fixtures: build/verify test fixtures with upstream's own report builder
    (changedetectionio/changes_report.py, standard library only);
  - techsignal.live / tests/integration: start a real changedetection.io process.

Where it is looked for, in order:
  1. TECHSIGNAL_CDIO_SOURCE - path to a changedetection.io checkout;
  2. the parent directory, when TechSignal sits inside a changedetection.io checkout (the original
     development fork kept it in techsignal/).
scripts/fetch_changedetection.sh clones the pinned upstream commit below.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

UPSTREAM_REPO = "https://github.com/dgtlmoon/changedetection.io.git"
# The upstream commit every fixture and saved case in this repository was produced with.
PINNED_COMMIT = "5e086dddf83e6ff4f43751db2386a33e006b1e3b"
PINNED_VERSION = "0.60.10"
CHANGES_REPORT_SHA256 = "606d97790b70b27374925169ee1abbd6b33b91c05c0e987f8ba9537268749db7"

HOW_TO = ("需要 changedetection.io 源码：运行 scripts/fetch_changedetection.sh（克隆固定版本），"
          "或设置环境变量 TECHSIGNAL_CDIO_SOURCE 指向一个 changedetection.io 源码目录。")


def _is_checkout(path: Path) -> bool:
    return (path / "changedetection.py").is_file() and (path / "changedetectionio" / "changes_report.py").is_file()


def cdio_source() -> Path | None:
    env = os.getenv("TECHSIGNAL_CDIO_SOURCE")
    if env:
        p = Path(env).expanduser().resolve()
        return p if _is_checkout(p) else None
    parent = PROJECT_ROOT.parent
    return parent if _is_checkout(parent) else None


def changes_report_path() -> Path | None:
    src = cdio_source()
    return src / "changedetectionio" / "changes_report.py" if src else None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def matches_pinned() -> bool | None:
    p = changes_report_path()
    return None if p is None else sha256_file(p) == CHANGES_REPORT_SHA256
