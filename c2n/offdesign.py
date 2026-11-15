"""非额定工况估算（方案 B）：不建模汽轮机通流，只引入三条可标定的假设。

1. **滑压规律**  ``p/p0 = (m/m0)^k``，低于滑压下限转定压节流（升压不再降）；
2. **级组效率修正**  ``eta_steam(load) = eta_steam,rated * f(load)``，
   ``f`` 由 ``[partload].eta_ratio`` 给出（8 个负荷点）；
3. **给水温度**  业主给表就用表；留空则按"抽汽级数不变、抽汽压力随滑压比缩放"
   估算：``T_fw(load) = T_sat(p_sat(T_fw,rated) * r)``。

除这三条外全是**精确热平衡**：给定 (m, p, T_fw) 用与额定点完全相同的
:func:`c2n.steam_side.duties_at` 算四台换热器的热负荷（它会按机型选分段策略）。

辅机侧：
* 氦风机**恒定**（反应堆稳定运行，氦流量与压比不变）；
* 盐泵随盐流量（放热盐流量 ∝ 汽侧吸热，充热侧恒定）；
* 汽轮机岛辅机按"一部分固定（循环水/辅机冷却）+ 一部分随负荷"分解。

求解：每个负荷点给定目标毛电 ``L * P_rated``，反解主蒸汽流量 ``m``
（``p``、``T_fw``、``Q`` 都是 ``m`` 的函数），一维阻尼迭代，收敛到 1e-5。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf

from .config import PlantConfig
from .properties import p_sat, salt_h, salt_t, t_sat
from .steam_side import SteamSide, duties_at


@dataclass(frozen=True)
class LoadPoint:
    """一个非额定工况点的估算结果。"""

    load: float
    m_main_kgs: float
    p_main_bar: float
    p_boiler_bar: float
    t_fw_c: float
    t_fw_source: str
    q_steam_mw: float
    gross_mw: float
    aux_turbine_mw: float
    aux_circulator_mw: float
    aux_pump_mw: float
    net_mw: float
    q_net_mw: float
    storage_hours: float
    eta_cycle_pct: float
    sliding: bool

    t_cold_c: float
    """冷罐（盐回路冷侧）温度 °C。由 IHX 与反应堆侧盐流量决定，恒定。"""

    t_salt_min_c: float
    """盐回路中**最低**的温度 °C（通常是省煤器出口，随负荷下降）。"""

    t_sh_out_c: float
    t_ev_out_c: float
    t_econ_out_c: float
    t_rh_out_c: float

    salt_warnings: tuple[str, ...] = ()

    @property
    def charging(self) -> bool:
        return self.q_net_mw > 0.0

    def as_row(self) -> dict:
        return {
            "load_pct": 100.0 * self.load,
            "m_main_kgs": self.m_main_kgs,
            "p_main_bar": self.p_main_bar,
            "p_boiler_bar": self.p_boiler_bar,
            "t_fw_c": self.t_fw_c,
            "t_fw_source": self.t_fw_source,
            "q_steam_mw": self.q_steam_mw,
            "gross_mw": self.gross_mw,
            "aux_turbine_mw": self.aux_turbine_mw,
            "aux_circulator_mw": self.aux_circulator_mw,
            "aux_pump_mw": self.aux_pump_mw,
            "net_mw": self.net_mw,
            "q_net_mw": self.q_net_mw,
            "storage_hours": (
                None if abs(self.q_net_mw) <= 5.0 else self.storage_hours
            ),
            "eta_cycle_pct": self.eta_cycle_pct,
            "sliding": self.sliding,
            "t_cold_c": self.t_cold_c,
            "t_salt_min_c": self.t_salt_min_c,
            "t_econ_out_c": self.t_econ_out_c,
        }


def _pressure_ratio(cfg: PlantConfig, flow_ratio: float) -> tuple[float, bool]:
    """滑压比 r 与是否仍处于滑压段。"""

    pl = cfg.partload
    floor = pl.sliding_floor
    if flow_ratio >= floor:
        return flow_ratio ** pl.sliding_exponent, True
    return floor ** pl.sliding_exponent, False


def _feedwater(
    cfg: PlantConfig,
    ratio: float,
    table_value: float | None,
    table_rated: float,
) -> tuple[float, str]:
    """给水温度：优先用业主的表（按额定值平移），否则按抽汽压力缩放估算。

    表描述的是"随负荷变化的**形状**"，绝对水平必须以业主的额定给水温度为准：
    否则换一台机组时，600 MW 级默认表（首点 273.6 °C）会覆盖掉业主的 239 °C。
    """

    if table_value is not None:
        offset = cfg.plant.feedwater_t_c - table_rated
        label = "与额定值一致" if abs(offset) < 0.05 else f"按额定平移 {offset:+.1f} K"
        return table_value + offset, label
    p_ext_rated = p_sat(cfg.plant.feedwater_t_c)
    return t_sat(p_ext_rated * ratio), "估算"


def run_part_load(
    cfg: PlantConfig,
    *,
    steam_rated: SteamSide,
    gross_rated_mw: float,
    q_ihx_mw: float,
    q_core_mw: float,
    aux_circulator_mw: float,
    aux_salt_pump_mw: float,
    aux_turbine_mw: float,
    m_salt_charge_kgs: float,
    m_salt_discharge_kgs: float,
    t_cold_c: float,
    split_fraction: float,
    capacity_mwh: float,
) -> list[LoadPoint]:
    """逐负荷点估算全厂效率与储罐状态。"""

    pl = cfg.partload
    p = cfg.plant
    m_rated = steam_rated.m_main_kgs
    eta_steam_rated = gross_rated_mw / steam_rated.q_steam_mw
    m_rh_ratio = steam_rated.m_rh_kgs / m_rated
    salt_total_rated = m_salt_charge_kgs + m_salt_discharge_kgs
    table_rated = pl.feedwater_t_c[0] if pl.feedwater_t_c else 0.0

    points: list[LoadPoint] = []
    for i, (load, ratio_eta) in enumerate(zip(pl.load_points, pl.eta_ratio)):
        target = load * gross_rated_mw

        # 给水温度表（给了就用，长度与 load_points 一致；留空则估算）
        table_value = pl.feedwater_t_c[i] if pl.feedwater_t_c else None

        m = m_rated * load
        solved = None
        for _ in range(80):
            ratio, sliding = _pressure_ratio(cfg, m / m_rated)
            t_fw, source = _feedwater(cfg, ratio, table_value, table_rated)

            duties = duties_at(
                cfg,
                ratio=ratio,
                t_fw_c=t_fw,
                m_main_kgs=m,
                m_rh_kgs=m * m_rh_ratio,
            )
            gross = duties.q_steam_mw * eta_steam_rated * ratio_eta
            if abs(gross / target - 1.0) < 1e-5:
                solved = (ratio, sliding, t_fw, source, duties, gross)
                break
            m *= (target / gross) ** 0.8

        if solved is None:
            continue

        ratio, sliding, t_fw, source, duties, gross = solved

        aux_turbine = aux_turbine_mw * (
            pl.turbine_aux_fixed_fraction
            + (1.0 - pl.turbine_aux_fixed_fraction) * load
        )
        # 盐泵：充热侧流量恒定，放热侧流量 ∝ 汽侧吸热（温差不变）
        q_ratio = duties.q_steam_mw / steam_rated.q_steam_mw
        flow_mix = (
            m_salt_charge_kgs + m_salt_discharge_kgs * q_ratio
        ) / salt_total_rated
        aux_pump = aux_salt_pump_mw * flow_mix
        net = gross - aux_turbine - aux_circulator_mw - aux_pump

        q_net = q_ihx_mw - duties.q_steam_mw
        hours = capacity_mwh / abs(q_net) if abs(q_net) > 1e-9 else inf
        # 与额定点同一口径：汽机侧净循环效率 = 净电 / 四台换热器吸热
        eta = (
            100.0 * net / duties.q_steam_mw
            if duties.q_steam_mw > 0.0
            else float("nan")
        )

        # 盐侧温度：冷罐由 IHX 钉住（反应堆恒定），四段出口随负荷变
        h_hot = salt_h(cfg.salt.t_hot_c)
        h_cold = salt_h(t_cold_c)
        m_dsg = duties.q_steam_mw * 1000.0 / (h_hot - h_cold)
        m_main = split_fraction * m_dsg
        m_rh = m_dsg - m_main
        t_sh = salt_t(h_hot - duties.q_superheater_mw * 1000.0 / m_main)
        t_ev = salt_t(salt_h(t_sh) - duties.q_evaporator_mw * 1000.0 / m_main)
        t_econ = salt_t(salt_h(t_ev) - duties.q_economizer_mw * 1000.0 / m_main)
        t_rh = salt_t(h_hot - duties.q_reheater_mw * 1000.0 / m_rh)
        t_min = min(t_cold_c, t_sh, t_ev, t_econ, t_rh)

        notes: list[str] = []
        if t_min < cfg.salt.t_warn_freeze_c:
            notes.append(
                f"{100 * load:.0f} % 负荷：盐回路最低温度 {t_min:.1f} °C "
                f"(&lt; {cfg.salt.t_warn_freeze_c:.0f} °C) —— **凝固风险**"
            )
        elif t_min < cfg.salt.t_warn_margin_c:
            notes.append(
                f"{100 * load:.0f} % 负荷：盐回路最低温度 {t_min:.1f} °C "
                f"(&lt; {cfg.salt.t_warn_margin_c:.0f} °C) —— 距凝固温度裕量不足"
            )

        points.append(
            LoadPoint(
                load=load,
                m_main_kgs=m,
                p_main_bar=p.main_steam_p_bar * ratio,
                p_boiler_bar=p.p_boiler_bar * ratio,
                t_fw_c=t_fw,
                t_fw_source=source,
                q_steam_mw=duties.q_steam_mw,
                gross_mw=gross,
                aux_turbine_mw=aux_turbine,
                aux_circulator_mw=aux_circulator_mw,
                aux_pump_mw=aux_pump,
                net_mw=net,
                q_net_mw=q_net,
                storage_hours=hours,
                eta_cycle_pct=eta,
                sliding=sliding,
                t_cold_c=t_cold_c,
                t_salt_min_c=t_min,
                t_sh_out_c=t_sh,
                t_ev_out_c=t_ev,
                t_econ_out_c=t_econ,
                t_rh_out_c=t_rh,
                salt_warnings=tuple(notes),
            )
        )
    return points
