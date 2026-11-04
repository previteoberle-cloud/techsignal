#!/usr/bin/env bash
# Fetch the pinned changedetection.io commit that TechSignal's fixtures and saved cases were made
# with. Only needed for `python -m techsignal.fixtures --check`, `python -m techsignal.live` and the
# integration tests - NOT for installing, testing or running TechSignal itself.
#
#   scripts/fetch_changedetection.sh [DEST]        (default: .upstream/changedetection.io)
#   export TECHSIGNAL_CDIO_SOURCE="$PWD/.upstream/changedetection.io"
#   pip install -r "$TECHSIGNAL_CDIO_SOURCE/requirements.txt"   # only for live runs / integration tests
set -euo pipefail
REPO="https://github.com/dgtlmoon/changedetection.io.git"
COMMIT="5e086dddf83e6ff4f43751db2386a33e006b1e3b"                                   # keep in sync with techsignal/upstream.py
CHANGES_REPORT_SHA256="606d97790b70b27374925169ee1abbd6b33b91c05c0e987f8ba9537268749db7"  # idem
DEST="${1:-.upstream/changedetection.io}"

if [ ! -d "$DEST/.git" ]; then
  mkdir -p "$DEST"
  git -C "$DEST" init -q
fi
git -C "$DEST" fetch -q --depth=1 "$REPO" "$COMMIT"
git -C "$DEST" checkout -q --detach FETCH_HEAD
actual=$(sha256sum "$DEST/changedetectionio/changes_report.py" | cut -d' ' -f1)
if [ "$actual" != "$CHANGES_REPORT_SHA256" ]; then
  echo "changes_report.py sha256 mismatch: $actual" >&2
  exit 1
fi
echo "changedetection.io $COMMIT checked out in $DEST"
echo "export TECHSIGNAL_CDIO_SOURCE=\"$(cd "$DEST" && pwd)\""
