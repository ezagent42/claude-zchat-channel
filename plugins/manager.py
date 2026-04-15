"""PluginManager — 扫描 plugins/*.py，注册 on_* hook 并分发。

极简插件系统：
- 加载 `plugins_dir` 内的 `.py` 文件（跳过 `__init__`、`manager`、以 `_` 开头）
- 把模块内以 `on_` 开头的可调用注册为 hook
- `fire(hook_name, **kwargs)` 按注册顺序依次调用；sync/async 兼容
- 单个 hook 异常不中断其他 hook
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
import traceback
from pathlib import Path
from typing import Any, Callable


class PluginManager:
    def __init__(self, plugins_dir: Path):
        self._plugins_dir = plugins_dir
        self._hooks: dict[str, list[Callable[..., Any]]] = {}
        self._loaded_modules: list[str] = []
        if plugins_dir.is_dir():
            self._load_all()

    def _load_all(self) -> None:
        for py_file in sorted(self._plugins_dir.glob("*.py")):
            name = py_file.stem
            if name.startswith("_") or name in {"manager"}:
                continue
            self._load_module(py_file)

    def _load_module(self, py_file: Path) -> None:
        mod_name = f"_plugins_{py_file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, py_file)
            if spec is None or spec.loader is None:
                return
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        except Exception:
            print(f"[PluginManager] failed to load {py_file}:", file=sys.stderr)
            traceback.print_exc()
            return
        self._loaded_modules.append(mod_name)
        for attr_name, attr in inspect.getmembers(mod, callable):
            if attr_name.startswith("on_"):
                self._hooks.setdefault(attr_name, []).append(attr)

    def hook_names(self) -> set[str]:
        """Registered hook names（便于测试）。"""
        return set(self._hooks.keys())

    async def fire(self, hook_name: str, **kwargs: Any) -> None:
        """依次调用指定 hook 的所有函数。单个失败不影响其他。"""
        for fn in self._hooks.get(hook_name, []):
            try:
                result = fn(**kwargs)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                print(f"[PluginManager] hook {hook_name} failed:", file=sys.stderr)
                traceback.print_exc()
