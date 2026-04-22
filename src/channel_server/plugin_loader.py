"""Plugin 配置驱动加载器（V7）。

职责：从 plugins.toml 读配置 + 扫 plugin 包 + 签名驱动 DI + 注册到 PluginRegistry。

设计要点（见 .artifacts/eval-docs/eval-cs-plugin-config-autonomy-014.md）：

1. **Plugin discovery** — 扫 builtin `plugins/` 包 + 可选用户 `~/.zchat/plugins/`。
   每个子目录下必须有 `plugin.py` 且暴露恰好一个 `*Plugin` 类（继承 BasePlugin），
   且类属性 `name` 等于目录名。

2. **Config-driven** — plugins.toml `[plugins.<name>]` section 是每个 plugin 的
   配置来源。data_dir 未填则走默认（`default_plugin_data_dir(name)`）。
   `enabled = false` 跳过加载。

3. **Signature-driven DI** — Loader 内省 plugin `__init__` 签名：
     - 必有第一位参数 `config: dict`
     - 其余 keyword 参数按名字匹配 `injections` 字典（如 `emit_event`, `emit_command`）
     - kw-only 参数若名字是已注册的 plugin 名，从 registry 注入（csat 的 `audit`）
   Plugin 只声明自己需要的，loader 只注入声明了的，不多不少。

4. **Package discovery 顺序**：用户 `~/.zchat/plugins/` > builtin `plugins/`。
   用户可用同名目录覆盖 builtin。
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import pkgutil
import sys
from pathlib import Path
from typing import Any

from .plugin import BasePlugin, Plugin, PluginRegistry

log = logging.getLogger(__name__)


_USER_PLUGIN_DIR = Path(os.environ.get("ZCHAT_USER_PLUGIN_DIR") or "~/.zchat/plugins").expanduser()


def default_plugin_data_dir(name: str, routing_path: str | Path) -> Path:
    """Plugin state 默认路径：`<routing.toml 所在目录>/plugins/<name>/`。

    CS_DATA_DIR env 已在 V7 删除——plugin 数据位置统一由 plugins.toml 决定，
    未声明时走此默认（per-project 目录）。
    """
    return Path(routing_path).parent / "plugins" / name


def _prepare_user_plugin_path() -> None:
    """把用户 plugin 目录加到 sys.path，以便 importlib 能 import。

    约定：`~/.zchat/plugins/<name>/plugin.py` → 以 `<name>.plugin` 作为 module。
    与 builtin `plugins/<name>/plugin.py` 同构（builtin 是 `plugins` namespace package）。
    """
    if _USER_PLUGIN_DIR.is_dir() and str(_USER_PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(_USER_PLUGIN_DIR))


def _iter_plugin_names() -> list[str]:
    """枚举可发现的 plugin 目录名。

    来源（按优先级合并，同名用户覆盖 builtin）：
      1. 用户 `~/.zchat/plugins/<name>/`
      2. Builtin `plugins` namespace package（通过 pkgutil）
    """
    names: dict[str, str] = {}  # name → source ('user' / 'builtin')

    # Builtin
    try:
        import plugins as _builtin_pkg
        for m in pkgutil.iter_modules(_builtin_pkg.__path__):
            if m.ispkg:
                names[m.name] = "builtin"
    except Exception:
        log.exception("failed to iter builtin plugins/")

    # 用户（覆盖）
    if _USER_PLUGIN_DIR.is_dir():
        for child in _USER_PLUGIN_DIR.iterdir():
            if child.is_dir() and (child / "plugin.py").is_file():
                names[child.name] = "user"

    return sorted(names.keys())


def _import_plugin_class(name: str) -> type[Plugin] | None:
    """Import `plugins.<name>.plugin` 并找到其中的 Plugin 类。

    约定：每个 plugin.py 暴露恰好一个继承 BasePlugin 的类，且其 `name` 属性
    等于目录名 `<name>`。不强制类名格式（允许 AuditPlugin / ShopifyExporter 等）。
    """
    try:
        mod = importlib.import_module(f"plugins.{name}.plugin")
    except Exception:
        log.exception("failed to import plugins.%s.plugin", name)
        return None

    candidates: list[type] = []
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        if not inspect.isclass(attr):
            continue
        if attr is BasePlugin:
            continue
        if issubclass(attr, BasePlugin) and getattr(attr, "name", "") == name:
            candidates.append(attr)

    if len(candidates) == 0:
        log.warning("plugins.%s: no *Plugin class with name=%r found", name, name)
        return None
    if len(candidates) > 1:
        log.warning(
            "plugins.%s: multiple plugin classes found (%s), using first",
            name, [c.__name__ for c in candidates],
        )
    return candidates[0]


def _resolve_init_kwargs(
    cls: type[Plugin],
    config: dict,
    injections: dict[str, Any],
    registry: PluginRegistry,
) -> dict[str, Any] | None:
    """按 `__init__` 签名决定要传哪些 kwarg。

    规则：
      - 位置参数 `config`（第一个非 self）必须存在，传 config dict
      - 其余 keyword 参数按名字从 `injections` 取（emit_event/emit_command 等）
      - kw-only 参数名如果是已 register 的 plugin name，从 `registry.get_plugin(name)` 注入
      - 签名有但 injections/registry 都拿不到的 → 省略（fallback 到参数默认值）；
        如果参数无默认值会导致 TypeError，log 后返回 None 让 loader 跳过该 plugin

    返回 kwargs dict；不可构造时返回 None。
    """
    sig = inspect.signature(cls.__init__)
    params = list(sig.parameters.values())[1:]  # skip self

    kwargs: dict[str, Any] = {}

    # 找 config 参数（第一个 positional-or-keyword 或 keyword-only 且叫 'config'）
    config_param = None
    for p in params:
        if p.name == "config":
            config_param = p
            break
    if config_param is None:
        log.warning(
            "plugin %s.__init__ has no `config` parameter; legacy signature not supported",
            cls.__name__,
        )
        return None
    kwargs["config"] = config

    for p in params:
        if p.name == "config":
            continue
        # injections 里有就取（emit_event / emit_command / ...）
        if p.name in injections:
            kwargs[p.name] = injections[p.name]
            continue
        # 否则尝试从 registry 按名字取已注册的 plugin（cross-plugin DI）
        peer = registry.get_plugin(p.name)
        if peer is not None:
            kwargs[p.name] = peer
            continue
        # 既没 injection 也没 peer；若有默认值就省略让它 fallback
        if p.default is inspect.Parameter.empty:
            log.warning(
                "plugin %s: required param %r has no injection and no peer; skipping",
                cls.__name__, p.name,
            )
            return None

    return kwargs


def load_plugins(
    *,
    registry: PluginRegistry,
    plugins_toml: dict[str, Any],
    routing_path: str | Path,
    injections: dict[str, Any],
) -> list[str]:
    """发现并注册所有 plugin。

    参数：
        registry: 目标 PluginRegistry
        plugins_toml: 解析后的 plugins.toml 内容（`{"plugins": {"audit": {...}}, ...}`
                      或直接是 `{"audit": {...}}`；自适应）
        routing_path: routing.toml 路径（用于 default_plugin_data_dir）
        injections: {"emit_event": fn, "emit_command": fn, ...} 供签名驱动注入

    返回：实际 register 的 plugin name 列表（顺序 = 实际注册顺序）。
    """
    _prepare_user_plugin_path()

    # plugins.toml 可以是 `[plugins.audit]` 嵌套或扁平 `[audit]`；统一拍平
    config_map = plugins_toml.get("plugins", plugins_toml) if isinstance(plugins_toml, dict) else {}

    registered: list[str] = []

    # 两阶段加载以支持 cross-plugin DI：
    # 阶段 1：先 register 所有无 peer 依赖的 plugin（config 里没有"别人 plugin 名"的 kw 参数需求）
    # 阶段 2：register 剩下的（如 csat 需要 audit 引用）
    # 实现上简化：按目录名排序，第一遍尽力而为，第二遍补齐
    names = _iter_plugin_names()
    pending: list[tuple[str, type[Plugin], dict]] = []

    for name in names:
        raw_cfg = config_map.get(name, {}) or {}
        if raw_cfg.get("enabled") is False:
            log.info("plugin %r disabled via plugins.toml; skip", name)
            continue

        cls = _import_plugin_class(name)
        if cls is None:
            continue

        # 注入默认 data_dir 到 config（plugin 不必自己算）
        config = dict(raw_cfg)
        config.setdefault("data_dir", str(default_plugin_data_dir(name, routing_path)))

        pending.append((name, cls, config))

    # Pass 1: 尝试 register 所有；对签名里有"未知 peer"的延后
    deferred: list[tuple[str, type[Plugin], dict]] = []
    for name, cls, config in pending:
        kwargs = _resolve_init_kwargs(cls, config, injections, registry)
        if kwargs is None:
            deferred.append((name, cls, config))
            continue
        try:
            instance = cls(**kwargs)
            registry.register(instance)
            registered.append(name)
            log.info("plugin %r registered", name)
        except Exception:
            log.exception("plugin %r failed to instantiate/register", name)

    # Pass 2: 再试一遍 deferred（此时 peer plugin 可能已注册）
    for name, cls, config in deferred:
        kwargs = _resolve_init_kwargs(cls, config, injections, registry)
        if kwargs is None:
            log.warning("plugin %r still cannot resolve injections after pass 2; skip", name)
            continue
        try:
            instance = cls(**kwargs)
            registry.register(instance)
            registered.append(name)
            log.info("plugin %r registered (deferred pass)", name)
        except Exception:
            log.exception("plugin %r failed to instantiate/register (deferred)", name)

    return registered


def load_plugins_toml(routing_path: str | Path) -> dict[str, Any]:
    """从 routing.toml 同目录下寻找 plugins.toml 并解析。无文件返回空 dict。"""
    try:
        import tomllib  # py3.11+
    except ImportError:
        import tomli as tomllib  # type: ignore

    candidate = Path(routing_path).parent / "plugins.toml"
    if not candidate.is_file():
        log.info("no plugins.toml at %s; all plugins use defaults", candidate)
        return {}
    with open(candidate, "rb") as f:
        return tomllib.load(f)
