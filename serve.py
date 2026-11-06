"""兼容壳：入口已收敛到 app/main.py（唯一组合根）。

历史用法继续可用，参数原样转发：

    python serve.py --detect a.jpg
    python serve.py --selftest --port 8399
    python serve.py --serve-net

新代码请直接用 `python -m app.main ...`。本壳存在的唯一理由是不打断
既有的调试习惯与文档里的命令行；不要往这里加逻辑。
"""

from __future__ import annotations

import sys

from app.main import main

if __name__ == "__main__":
    print("[deprecated] serve.py 已并入 app/main.py，请改用 python -m app.main",
          file=sys.stderr)
    sys.exit(main())
