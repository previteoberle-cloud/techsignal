"""python -m techsignal.workbench [streamlit 参数…]  —— 启动 TechSignal 信息采集工作台（默认只监听本机 127.0.0.1）。"""
import sys
from pathlib import Path


def main() -> int:
    try:
        from streamlit.web import cli as stcli
    except ImportError:
        print("缺少 streamlit：请先执行 pip install -e \".[ui]\"", file=sys.stderr)
        return 2
    app = Path(__file__).with_name("app.py")
    extra = sys.argv[1:]
    # Local use only by default: listen on 127.0.0.1, not on every network interface. Pass
    # --server.address explicitly to change it (the workbench has no login; see README).
    local = [] if any(a.startswith("--server.address") for a in extra) else ["--server.address", "127.0.0.1"]
    sys.argv = ["streamlit", "run", str(app), "--server.headless", "true",
                "--browser.gatherUsageStats", "false", *local, *extra]
    return stcli.main()


if __name__ == "__main__":
    sys.exit(main())
