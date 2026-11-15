"""C2N 统一内核：一个模型，两种机组。

原来的两个工具（``C2N_CPP`` 亚临界、``C2N_SCPP`` 超临界）差异只有两处：
**蒸汽侧分段基准**（饱和温度 vs 水冷壁出口温度）与**盐的分解温度**。本包把
这两处变成"策略"与"流体属性"，求解器（盐侧两次二分、沿程积分、变工况）只有
一份。

入口
----
``assess(case)``
    输入 JSON -> 技术结果 JSON（契约 + 界面素材）。网站与 CLI 都走它。
``load_config(path)``
    读 TOML；``auto`` 机组类型在此判定。
``run_design(cfg)``
    只要 Python 对象、不要 JSON 时的直接入口。

为什么 ``__init__`` 是惰性的
---------------------------
物性计算依赖 CoolProp。若在包初始化时就 import 求解链，那么只想读配置或跑
文档的人也会被"没装 CoolProp"挡住。这里用模块级 ``__getattr__`` 延迟加载。
"""

from __future__ import annotations

from .version import __version__

__all__ = [
    "__version__",
    "assess",
    "build_envelope",
    "report_markdown",
    "DEFAULT_CONFIG",
    "ConfigError",
    "PlantConfig",
    "dump_config",
    "finalize",
    "load_config",
    "resolve",
    "validate",
    "DesignResult",
    "run_design",
    "build_states",
]

_LAZY = {
    "assess": ("c2n.assess", "assess"),
    "build_envelope": ("c2n.assess", "build_envelope"),
    "report_markdown": ("c2n.assess", "report_markdown"),
    "DEFAULT_CONFIG": ("c2n.config", "DEFAULT_CONFIG"),
    "ConfigError": ("c2n.config", "ConfigError"),
    "PlantConfig": ("c2n.config", "PlantConfig"),
    "dump_config": ("c2n.config", "dump_config"),
    "finalize": ("c2n.config", "finalize"),
    "load_config": ("c2n.config", "load_config"),
    "resolve": ("c2n.config", "resolve"),
    "validate": ("c2n.config", "validate"),
    "DesignResult": ("c2n.design", "DesignResult"),
    "run_design": ("c2n.design", "run_design"),
    "build_states": ("c2n.states", "build_states"),
}


def __getattr__(name: str):  # PEP 562
    if name in _LAZY:
        import importlib

        module_name, attr = _LAZY[name]
        return getattr(importlib.import_module(module_name), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
