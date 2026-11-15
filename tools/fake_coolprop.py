"""**离线自检用的 CoolProp 替身** —— 不是物性库，不能用于任何工程结论。

背景
----
本机（或 CI 的离线环节）可能装不上 CoolProp。没有它，`:mod:`c2n` 一行都跑不
起来，于是"语法对不对"能查、"装配有没有接错"查不了。

这个模块提供一个**单调、量级合理**的假物性接口，只为让求解链跑通，用来发现：

* 属性名/字段名写错（``AttributeError``）；
* 某个分支返回 ``None`` 却参与算术（``TypeError``）；
* 契约装配漏字段、JSON 化时出现 ``inf``/``nan``；
* 变工况、盐侧二分、模块数反解这类**结构性**逻辑是否走得通。

它**故意不追求数值准确**：饱和温度用 Antoine 型幂律、汽化潜热用拟合幂律、
氦气按理想气体。用它对标工程数值是没有意义的——凡是要断言具体数字的测试，
都必须用真的 CoolProp（见 ``tests/``）。

用法::

    import tools.fake_coolprop as fake
    fake.install()          # 注入 sys.modules["CoolProp"]
    import c2n.assess       # 之后再导入内核
"""

from __future__ import annotations

import math
import sys
import types

T_CRIT_C = 373.946
P_CRIT_BAR = 220.64
P_ATM_BAR = 1.01325


# ---------------------------------------------------------------------------
# 水 / 蒸汽
# ---------------------------------------------------------------------------
def _t_sat(p_bar: float) -> float:
    """饱和温度 °C（量级正确即可）。"""

    return 100.0 * (max(p_bar, 1.0e-6) / P_ATM_BAR) ** 0.25


def _p_sat(t_c: float) -> float:
    return P_ATM_BAR * (max(t_c, 1.0) / 100.0) ** 4


def _h_liq(t_c: float) -> float:
    return 4.18 * t_c


def _h_vap(p_bar: float) -> float:
    ts = _t_sat(p_bar)
    latent = 2257.0 * max(0.0, 1.0 - ts / T_CRIT_C) ** 0.317
    return _h_liq(ts) + latent


def _h_water(p_bar: float, t_c: float) -> float:
    ts = _t_sat(p_bar)
    if t_c <= ts:
        return _h_liq(t_c)
    return _h_vap(p_bar) + 2.15 * (t_c - ts)


def _t_water(p_bar: float, h_kj: float) -> float:
    ts = _t_sat(p_bar)
    hf = _h_liq(ts)
    hg = _h_vap(p_bar)
    if h_kj <= hf:
        return h_kj / 4.18
    if h_kj <= hg:
        return ts
    return ts + (h_kj - hg) / 2.15


def _pseudo_critical(p_bar: float) -> float:
    return T_CRIT_C + 0.25 * (p_bar - P_CRIT_BAR)


def _cp_water(p_bar: float, t_c: float) -> float:
    base = 4.18 if t_c <= _t_sat(p_bar) else 2.15
    if p_bar > P_CRIT_BAR:
        tpc = _pseudo_critical(p_bar)
        amp = min(18.0, 1.2 * (p_bar - P_CRIT_BAR))
        base += amp * math.exp(-((t_c - tpc) / 14.0) ** 2)
    return base * 1000.0


def _s_water(p_bar: float, t_c: float) -> float:
    return 4.18 * math.log((t_c + 273.15) / 273.15)


# ---------------------------------------------------------------------------
# 氦气（理想气体）
# ---------------------------------------------------------------------------
_CP_HE = 5.193       # kJ/(kg K)
_R_HE = 2.077        # kJ/(kg K)


def _h_helium(p_bar: float, t_c: float) -> float:
    return _CP_HE * t_c


def _t_helium(p_bar: float, h_kj: float) -> float:
    return h_kj / _CP_HE


def _s_helium(p_bar: float, t_c: float) -> float:
    return _CP_HE * math.log((t_c + 273.15) / 273.15) - _R_HE * math.log(
        max(p_bar, 1.0e-6) / 1.0
    )


def _h_helium_ps(p_bar: float, s_kj: float) -> float:
    t_k = 273.15 * math.exp((s_kj + _R_HE * math.log(max(p_bar, 1.0e-6))) / _CP_HE)
    return _CP_HE * (t_k - 273.15)


# ---------------------------------------------------------------------------
# PropsSI 门面
# ---------------------------------------------------------------------------
def _pair(inputs: dict[str, float]) -> tuple[float, float]:
    """取出 (p_bar, 目标量)；保留给需要拆参数的调用方。"""

    p_bar = inputs["P"] / 1.0e5
    if "T" in inputs:
        return p_bar, inputs["T"] - 273.15
    if "H" in inputs:
        return p_bar, inputs["H"] / 1000.0
    if "S" in inputs:
        return p_bar, inputs["S"] / 1000.0
    if "Q" in inputs:
        return p_bar, inputs["Q"]
    raise ValueError(f"不支持的输入组合 {sorted(inputs)}")


def PropsSI(output: str, n1: str, v1: float, n2: str, v2: float, fluid: str) -> float:
    inputs = {n1: float(v1), n2: float(v2)}

    if fluid.lower() == "helium":
        p_bar = inputs["P"] / 1.0e5 if "P" in inputs else None
        if output == "H":
            if "T" in inputs:
                return _h_helium(p_bar, inputs["T"] - 273.15) * 1000.0
            return _h_helium_ps(p_bar, inputs["S"] / 1000.0) * 1000.0
        if output == "T":
            return _t_helium(p_bar, inputs["H"] / 1000.0) + 273.15
        if output == "S":
            return _s_helium(p_bar, inputs["T"] - 273.15) * 1000.0
        if output == "C":
            return _CP_HE * 1000.0
        raise ValueError(f"fake CoolProp: helium 不支持输出 {output!r}")

    if fluid.lower() != "water":
        raise ValueError(f"fake CoolProp: 不支持流体 {fluid!r}")

    # (T, Q) -> P：饱和压力
    if "Q" in inputs and "T" in inputs:
        if output != "P":
            raise ValueError(f"fake CoolProp: (T,Q) 只支持输出 P，收到 {output!r}")
        return _p_sat(inputs["T"] - 273.15) * 1.0e5

    # (P, Q) -> T / H：饱和线上
    if "Q" in inputs and "P" in inputs:
        p_bar = inputs["P"] / 1.0e5
        if output == "T":
            return _t_sat(p_bar) + 273.15
        if output == "H":
            h = _h_liq(_t_sat(p_bar)) if inputs["Q"] == 0.0 else _h_vap(p_bar)
            return h * 1000.0
        raise ValueError(f"fake CoolProp: (P,Q) 不支持输出 {output!r}")

    # (P, T)
    if "P" in inputs and "T" in inputs:
        p_bar = inputs["P"] / 1.0e5
        t_c = inputs["T"] - 273.15
        return {
            "H": lambda: _h_water(p_bar, t_c) * 1000.0,
            "T": lambda: inputs["T"],
            "C": lambda: _cp_water(p_bar, t_c),
            "S": lambda: _s_water(p_bar, t_c) * 1000.0,
            "P": lambda: p_bar * 1.0e5,
        }.get(output, lambda: _unsupported("(P,T)", output))()

    # (P, H)
    if "P" in inputs and "H" in inputs:
        p_bar = inputs["P"] / 1.0e5
        if output == "T":
            return _t_water(p_bar, inputs["H"] / 1000.0) + 273.15
        raise ValueError(f"fake CoolProp: (P,H) 不支持输出 {output!r}")

    # (P, S)：等熵理想化，仅供跑通
    if "P" in inputs and "S" in inputs:
        p_bar = inputs["P"] / 1.0e5
        t_c = 273.15 * math.exp(inputs["S"] / 1000.0 / 4.18) - 273.15
        if output == "H":
            return _h_water(p_bar, t_c) * 1000.0
        if output == "T":
            return t_c + 273.15
        raise ValueError(f"fake CoolProp: (P,S) 不支持输出 {output!r}")

    raise ValueError(f"fake CoolProp: 无法解析输入 {sorted(inputs)}")


def _unsupported(kind: str, output: str) -> float:
    raise ValueError(f"fake CoolProp: {kind} 不支持输出 {output!r}")


def install() -> types.ModuleType:
    """把假实现注入 ``sys.modules``；若真 CoolProp 已在，则不覆盖。"""

    try:
        import CoolProp.CoolProp as real  # noqa: F401

        return real
    except Exception:
        pass

    package = types.ModuleType("CoolProp")
    module = types.ModuleType("CoolProp.CoolProp")
    module.PropsSI = PropsSI  # type: ignore[attr-defined]
    module.__version__ = "0.0.0-fake"  # type: ignore[attr-defined]
    package.CoolProp = module  # type: ignore[attr-defined]
    sys.modules["CoolProp"] = package
    sys.modules["CoolProp.CoolProp"] = module
    return module


def has_real_coolprop() -> bool:
    try:
        import CoolProp.CoolProp as real

        return not str(getattr(real, "__version__", "")).endswith("-fake")
    except Exception:
        return False
