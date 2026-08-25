"""自动化测试入口：运行全部单测。"""

import sys
from pathlib import Path

# 把 src/ 加到 sys.path，让 from mc_translator_mcp import ... 可用
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

if __name__ == "__main__":
    sys.exit(pytest.main([
        "-v",
        "--tb=short",
        str(Path(__file__).parent),
    ]))
