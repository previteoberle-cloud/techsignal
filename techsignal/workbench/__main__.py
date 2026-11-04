"""python -m techsignal.workbench [streamlit 参数…]  —— 启动研究工作台。"""
import sys
from pathlib import Path


def main() -> int:
    try:
        from streamlit.web import cli as stcli
    except ImportError:
        print("缺少 streamlit：请先执行 pip install -e \".[ui]\"", file=sys.stderr)
        return 2
    app = Path(__file__).with_name("app.py")
    sys.argv = ["streamlit", "run", str(app), "--server.headless", "true",
                "--browser.gatherUsageStats", "false", *sys.argv[1:]]
    return stcli.main()


if __name__ == "__main__":
    sys.exit(main())
