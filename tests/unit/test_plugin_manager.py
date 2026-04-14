"""PluginManager unit tests — 钩子加载 + 异步调用"""

from __future__ import annotations

import textwrap

import pytest

from engine.plugin_manager import PluginManager


@pytest.fixture
def plugins_dir(tmp_path):
    d = tmp_path / "plugins"
    d.mkdir()
    return d


def write_plugin(d, name: str, body: str) -> None:
    (d / f"{name}.py").write_text(textwrap.dedent(body))


def test_load_empty_dir(plugins_dir):
    pm = PluginManager(str(plugins_dir))
    pm.load_hooks_from_dir()
    assert pm.hooks("on_message") == []


def test_load_and_call_on_message(plugins_dir):
    write_plugin(
        plugins_dir,
        "greet",
        """
        def on_message(msg):
            msg['seen_by'] = 'greet'
            return msg
        """,
    )
    pm = PluginManager(str(plugins_dir))
    pm.load_hooks_from_dir()
    msg = {"content": "hi"}
    for hook in pm.hooks("on_message"):
        hook(msg)
    assert msg["seen_by"] == "greet"


@pytest.mark.asyncio
async def test_async_hook(plugins_dir):
    write_plugin(
        plugins_dir,
        "async_hook",
        """
        async def on_mode_changed(data):
            data['async_called'] = True
        """,
    )
    pm = PluginManager(str(plugins_dir))
    pm.load_hooks_from_dir()
    data = {}
    await pm.call_async("on_mode_changed", data)
    assert data["async_called"] is True


def test_multiple_plugins_accumulate(plugins_dir):
    write_plugin(
        plugins_dir,
        "a",
        """
        def on_message(msg):
            msg.setdefault('seen', []).append('a')
        """,
    )
    write_plugin(
        plugins_dir,
        "b",
        """
        def on_message(msg):
            msg.setdefault('seen', []).append('b')
        """,
    )
    pm = PluginManager(str(plugins_dir))
    pm.load_hooks_from_dir()
    msg = {}
    for hook in pm.hooks("on_message"):
        hook(msg)
    assert set(msg["seen"]) == {"a", "b"}


def test_ignores_non_py_files(plugins_dir):
    (plugins_dir / "README.md").write_text("not a plugin")
    (plugins_dir / "_private.py").write_text("def on_message(m): pass\n")
    write_plugin(
        plugins_dir,
        "real",
        "def on_message(msg): msg['ok'] = True\n",
    )
    pm = PluginManager(str(plugins_dir))
    pm.load_hooks_from_dir()
    msg = {}
    for hook in pm.hooks("on_message"):
        hook(msg)
    assert msg["ok"] is True
    # _private.py 以下划线开头应被跳过
    assert len(pm.hooks("on_message")) == 1
