"""官方插件包（V4 新实现）。"""
from pkgutil import extend_path
__path__ = extend_path(__path__, __name__)  # namespace package — 与根 plugins/ 合并
