"""传热与泵功的通用小工具。

kA 的算法
---------
端点 LMTD（``kA = Q / LMTD``）只在两侧比热恒定时严格成立。盐-汽换热器里
蒸汽在饱和线附近比热可以到 8~14 kJ/(kg·K)，温度沿程"几乎不升"，T-Q 曲线
强烈弯曲，端点法会带来 −24 %~+42 % 的误差。

因此这里用**沿程积分**：

    kA = ∫ dQ / ΔT(f)

把换热面按热负荷等分，逐点求两侧温度，累加 1/ΔT。40 个点已足够（200/800/3000
点对比，kA 变化 < 0.1 %）。同时给出沿程真实最小温差 —— 这是判断夹点的正确位置，
端点法看不到内部的温度交叉。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

#: 沿程积分的分段数（40 点已收敛到 0.1 % 以内）
N_SEGMENTS = 60


def lmtd(dt_hot_end: float, dt_cold_end: float) -> float:
    """对数平均温差（端点法）；任一端出现温度交叉返回 nan。"""

    if not (dt_hot_end > 0.0 and dt_cold_end > 0.0):
        return float("nan")
    if abs(dt_hot_end - dt_cold_end) < 1.0e-9:
        return dt_hot_end
    return (dt_hot_end - dt_cold_end) / math.log(dt_hot_end / dt_cold_end)


@dataclass(frozen=True)
class Section:
    """一台换热器的设计结果。"""

    name: str
    q_mw: float
    ka_mw_k: float
    """所需换热能力（沿程积分），MW/K。"""

    lmtd_k: float
    """等效对数平均温差 = Q / kA。"""

    dt_hot_end_k: float
    dt_cold_end_k: float
    dt_min_k: float
    """沿程真实最小温差（端点法看不到内部夹点）。"""

    ka_selected_mw_k: float = 0.0
    """选型 kA = 所需 kA × ua_margin。"""

    area_m2: float = 0.0
    """按 U 折算的换热面积；U 未给时为 0。"""

    @property
    def feasible(self) -> bool:
        return (
            math.isfinite(self.ka_mw_k)
            and self.ka_mw_k > 0.0
            and self.dt_min_k > 0.0
        )

    def as_row(self) -> dict:
        return {
            "section": self.name,
            "q_mw": self.q_mw,
            "ka_mw_k": self.ka_mw_k,
            "lmtd_k": self.lmtd_k,
            "ka_selected_mw_k": self.ka_selected_mw_k,
            "area_m2": self.area_m2,
            "dt_hot_end_k": self.dt_hot_end_k,
            "dt_cold_end_k": self.dt_cold_end_k,
            "dt_min_k": self.dt_min_k,
        }


def section(
    name: str,
    q_mw: float,
    t_hot_in_c: float,
    t_hot_out_c: float,
    t_cold_in_c: float,
    t_cold_out_c: float,
) -> Section:
    """端点 LMTD 法（保留给简单校核与对照，主计算用 :func:`section_integrated`）。"""

    dt_hot_end = t_hot_in_c - t_cold_out_c
    dt_cold_end = t_hot_out_c - t_cold_in_c
    mean = lmtd(dt_hot_end, dt_cold_end)
    ka = q_mw / mean if math.isfinite(mean) and mean > 0.0 else float("nan")
    return Section(
        name=name,
        q_mw=q_mw,
        ka_mw_k=ka,
        lmtd_k=mean,
        dt_hot_end_k=dt_hot_end,
        dt_cold_end_k=dt_cold_end,
        dt_min_k=min(dt_hot_end, dt_cold_end),
    )


def temperature_profile(
    h_in: float,
    h_out: float,
    p_in: float,
    p_out: float,
    t_of_ph: Callable[[float, float], float],
    n: int = N_SEGMENTS,
) -> list[float]:
    """沿换热面（按热负荷等分）取温度分布，返回每个分段中点的温度。"""

    return [
        t_of_ph(
            p_in + (i + 0.5) / n * (p_out - p_in),
            h_in + (i + 0.5) / n * (h_out - h_in),
        )
        for i in range(n)
    ]


def section_integrated(
    name: str,
    q_mw: float,
    *,
    t_hot_profile: Sequence[float],
    t_cold_profile: Sequence[float],
    dt_hot_end_k: float,
    dt_cold_end_k: float,
) -> Section:
    """沿程积分求 kA：``kA = Σ (dQ / ΔT)``。

    两个温度剖面按**同一方向**排列（例如都从冷端到热端），每点是分段中点，
    因此**进出口本身不在剖面里**。kA 用中点矩形积分是对的，但最小温差必须
    额外并上真端点 ``dt_hot_end_k`` / ``dt_cold_end_k``——否则"5 K 夹点"会在
    两个端点上失守（例如再热器冷端真实值可以比中点最小值低 0.4 K）。
    """

    n = len(t_hot_profile)
    if n == 0 or len(t_cold_profile) != n:
        raise ValueError("temperature profiles must be non-empty and equal length")

    dq = q_mw / n
    ka = 0.0
    dt_min = min(dt_hot_end_k, dt_cold_end_k)
    for t_hot, t_cold in zip(t_hot_profile, t_cold_profile):
        dt = t_hot - t_cold
        dt_min = min(dt_min, dt)
        if dt <= 0.0:
            return Section(
                name=name,
                q_mw=q_mw,
                ka_mw_k=float("inf"),
                lmtd_k=float("nan"),
                dt_hot_end_k=dt_hot_end_k,
                dt_cold_end_k=dt_cold_end_k,
                dt_min_k=dt_min,
            )
        ka += dq / dt

    return Section(
        name=name,
        q_mw=q_mw,
        ka_mw_k=ka,
        lmtd_k=q_mw / ka if ka > 0.0 else float("nan"),
        dt_hot_end_k=dt_hot_end_k,
        dt_cold_end_k=dt_cold_end_k,
        dt_min_k=dt_min,
    )


def with_selection(section_: Section, ua_margin: float, u_w_m2k: float) -> Section:
    """按面积裕量与传热系数折算"选型 kA"和面积。"""

    ka_sel = section_.ka_mw_k * ua_margin
    area = (
        ka_sel * 1.0e6 / u_w_m2k if u_w_m2k > 0.0 and math.isfinite(ka_sel) else 0.0
    )
    return Section(
        name=section_.name,
        q_mw=section_.q_mw,
        ka_mw_k=section_.ka_mw_k,
        lmtd_k=section_.lmtd_k,
        dt_hot_end_k=section_.dt_hot_end_k,
        dt_cold_end_k=section_.dt_cold_end_k,
        dt_min_k=section_.dt_min_k,
        ka_selected_mw_k=ka_sel,
        area_m2=area,
    )


def pump_power_mw(
    m_kgs: float,
    dp_bar: float,
    eta: float,
    density: float,
    eta_motor: float = 1.0,
) -> float:
    """泵/风机的电功率 MW：m·Δp/(ρ·η_hyd·η_motor)。"""

    if m_kgs <= 0.0 or dp_bar <= 0.0:
        return 0.0
    return m_kgs * dp_bar * 1.0e5 / (density * eta * eta_motor) / 1.0e6
