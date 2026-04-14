"""PluginManager — 从目录扫描 .py 文件，注册钩子函数 (spec §3)

约定：
- 插件目录中每个 `foo.py` 会被导入为模块
- 模块里以 `on_*` 命名的 callable 自动注册为钩子
- 下划线开头的文件跳过
"""

from __future__ import annotations

import importlib.util
import inspect
import os
from collections import defaultdict
from typing import Any, Callable


class PluginManager:
    def __init__(self, plugins_dir: str):
        self._plugins_dir = plugins_dir
        self._hooks: dict[str, list[Callable[..., Any]]] = defaultdict(list)
        self._loaded: list[str] = []

    def load_hooks_from_dir(self) -> None:
        if not os.path.isdir(self._plugins_dir):
            return
        for fname in sorted(os.listdir(self._plugins_dir)):
            if not fname.endswith(".py"):
                continue
            if fname.startswith("_"):
                continue
            self._load_file(os.path.join(self._plugins_dir, fname), fname[:-3])

    def _load_file(self, path: str, modname: str) -> None:
        spec = importlib.util.spec_from_file_location(f"_plugin_{modname}", path)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name, obj in inspect.getmembers(module):
            if not name.startswith("on_"):
                continue
            if not callable(obj):
                continue
            self._hooks[name].append(obj)
        self._loaded.append(modname)

    def hooks(self, name: str) -> list[Callable[..., Any]]:
        return list(self._hooks.get(name, []))

    async def call_async(self, name: str, *args: Any, **kwargs: Any) -> list[Any]:
        """调用指定名称的所有钩子（支持 sync + async），返回结果列表。"""
        results: list[Any] = []
        for hook in self._hooks.get(name, []):
            result = hook(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            results.append(result)
        return results
