"""unit_v4 测试配置。

确保 src/ 优先于根目录，使 `import plugins` 指向 src/plugins/ 而非旧 plugins/。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 将 src/ 插入 sys.path 最前端，覆盖根目录下旧 plugins/ 包
_src = str(Path(__file__).parent.parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
elif sys.path[0] != _src:
    sys.path.remove(_src)
    sys.path.insert(0, _src)
