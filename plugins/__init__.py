"""App-level plugins for channel-server (SLA timers, etc.)."""
from pkgutil import extend_path
__path__ = extend_path(__path__, __name__)  # namespace package — 允许 src/plugins/ 子包合并
