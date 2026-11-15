"""工质物性：水/蒸汽、氦气（CoolProp）与熔盐（工程拟合）。

本工具刻意不依赖 TESPy，只用 CoolProp + 拟合式，因此业主端装一个 CoolProp
就能运行。熔盐的焓式与 `C2N_HTGR_Salt` 保持一致（cp = 1443 + 0.172 T，
h 为其积分），两边差值 < 0.01 %。

关于"换一种盐"
--------------
目前登记在 ``c2n.fluids`` 里的两种盐（太阳盐、新型硝酸盐）**共用同一套输运
物性拟合**，只有分解温度不同——这正是超临界方案的全部物理前提（见
``fluids.py``）。拟合系数从 :data:`c2n.fluids.SOLAR_SALT` 读取，避免"代码里
一份、配置里一份"的漂移。若将来引入真正不同的盐（氯化盐、FLiBe），在
``fluids.SaltFluid`` 里给系数赋值并在本模块按流体分派即可，求解器不用动。
"""

from __future__ import annotations

import CoolProp.CoolProp as CP

from .fluids import SOLAR_SALT

#: 熔盐 cp(T) 拟合，J/(kg K)，取自流体库（当前两种盐共用）
_CP_A = SOLAR_SALT.cp_a
_CP_B = SOLAR_SALT.cp_b
_RHO_A = SOLAR_SALT.rho_a
_RHO_B = SOLAR_SALT.rho_b


# ---------------------------------------------------------------------------
# 水 / 蒸汽
# ---------------------------------------------------------------------------
def h_water(p_bar: float, t_c: float) -> float:
    """水/蒸汽比焓 kJ/kg，由 (p, T) 确定。"""

    return CP.PropsSI("H", "P", p_bar * 1e5, "T", t_c + 273.15, "Water") / 1000.0


def t_water(p_bar: float, h_kj_kg: float) -> float:
    """温度 °C，由 (p, h) 确定（两相区返回饱和温度）。"""

    return CP.PropsSI("T", "P", p_bar * 1e5, "H", h_kj_kg * 1000.0, "Water") - 273.15


def t_sat(p_bar: float) -> float:
    """饱和温度 °C。"""

    return CP.PropsSI("T", "P", p_bar * 1e5, "Q", 0.0, "Water") - 273.15


def p_sat(t_c: float) -> float:
    """饱和压力 bar（用于把给水温度折算成"最后一级加热器的抽汽压力"）。"""

    return CP.PropsSI("P", "T", t_c + 273.15, "Q", 0.0, "Water") / 1e5


def h_sat_liq(p_bar: float) -> float:
    return CP.PropsSI("H", "P", p_bar * 1e5, "Q", 0.0, "Water") / 1000.0


def h_sat_vap(p_bar: float) -> float:
    return CP.PropsSI("H", "P", p_bar * 1e5, "Q", 1.0, "Water") / 1000.0


def s_water(p_bar: float, t_c: float) -> float:
    """比熵 kJ/(kg K)。"""

    return CP.PropsSI("S", "P", p_bar * 1e5, "T", t_c + 273.15, "Water") / 1000.0


# ---------------------------------------------------------------------------
# 氦气
# ---------------------------------------------------------------------------
def h_helium(p_bar: float, t_c: float) -> float:
    return CP.PropsSI("H", "P", p_bar * 1e5, "T", t_c + 273.15, "Helium") / 1000.0


def s_helium(p_bar: float, t_c: float) -> float:
    return CP.PropsSI("S", "P", p_bar * 1e5, "T", t_c + 273.15, "Helium") / 1000.0


def t_helium(p_bar: float, h_kj_kg: float) -> float:
    return (
        CP.PropsSI("T", "P", p_bar * 1e5, "H", h_kj_kg * 1000.0, "Helium") - 273.15
    )


def h_helium_ps(p_bar: float, s_kj_kgk: float) -> float:
    return (
        CP.PropsSI("H", "P", p_bar * 1e5, "S", s_kj_kgk * 1000.0, "Helium") / 1000.0
    )


# ---------------------------------------------------------------------------
# 太阳盐（60 wt% NaNO3 + 40 wt% KNO3）
# ---------------------------------------------------------------------------
def salt_h(t_c: float) -> float:
    """太阳盐比焓 kJ/kg（参考 0 °C）。"""

    return (_CP_A * t_c + 0.5 * _CP_B * t_c * t_c) / 1000.0


def salt_t(h_kj_kg: float) -> float:
    """由焓反解温度 °C（取正根）。"""

    import math

    h = h_kj_kg * 1000.0
    return (-_CP_A + math.sqrt(_CP_A * _CP_A + 2.0 * _CP_B * h)) / _CP_B


def salt_cp(t_c: float) -> float:
    """太阳盐比热 kJ/(kg K)。"""

    return (_CP_A + _CP_B * t_c) / 1000.0


def salt_density(t_c: float) -> float:
    """熔盐密度 kg/m3（工程拟合）。"""

    return _RHO_A + _RHO_B * t_c


# ---------------------------------------------------------------------------
# 超临界水的拟临界点
# ---------------------------------------------------------------------------
def pseudo_critical_t(p_bar: float) -> float:
    """拟临界温度 °C：定压下 cp 最大的温度（只在超临界压力下有意义）。

    超临界没有相变点，"蒸发段"的位置由 cp 峰值（拟临界点）界定。程序按 1 K
    步长扫描 350~460 °C 找 cp 峰值，与 ``C2N_SCPP`` 的实现完全一致。
    """

    best_t, best_cp = 400.0, -1.0
    for t in range(350, 460):
        cp = CP.PropsSI("C", "P", p_bar * 1e5, "T", t + 273.15, "Water")
        if cp > best_cp:
            best_cp, best_t = cp, float(t)
    return best_t

