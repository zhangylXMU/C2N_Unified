"""一回路氦气与 IHX（解析计算，不求解网络）。

回路：氦风机 -> 堆芯 -> IHX 氦侧 -> 氦风机。

* 压力：``p_core_out = p_cold / core_pr``，``p_ihx_out = p_core_out * ihx_pr``，
  所以风机压比 ``pi = p_cold / p_ihx_out``；
* 风机出口温度由等熵效率决定（氦气近似理想气体，与流量无关）；
* 堆芯功率给定，氦流量由 ``Q = m (h_hot - h_core_in)`` 反解；
* IHX 取走 ``Q_core + P_circulator``，这正是盐侧得到的热量。

注意 TESPy 中 ``pr`` 为进出口压比（< 1 表示压降），与本模块一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import PlantConfig
from .i18n import tr
from .properties import (
    h_helium,
    h_helium_ps,
    salt_h,
    salt_t,
    s_helium,
    t_helium,
)
from .thermal import (
    Section,
    section_integrated,
    temperature_profile,
)


@dataclass(frozen=True)
class ReactorDesign:
    """一回路计算结果（与盐侧温度无关的部分）。"""

    n_modules: int
    module_power_mwth: float
    q_core_mw: float

    p_cold_bar: float
    p_core_out_bar: float
    p_ihx_out_bar: float
    circulator_pressure_ratio: float

    t_hot_c: float
    t_ihx_out_c: float
    t_core_in_c: float

    h_core_in: float
    h_hot: float
    h_ihx_out: float

    m_helium_kgs: float
    m_per_module_kgs: float
    p_circulator_mw: float
    """氦风机轴功（TESPy 口径）。"""

    eta_circulator_motor: float = 1.0

    @property
    def q_ihx_mw(self) -> float:
        return self.q_core_mw + self.p_circulator_mw

    @property
    def p_circulator_electric_mw(self) -> float:
        """氦风机电耗 = 轴功 / 电机效率。"""

        return self.p_circulator_mw / self.eta_circulator_motor


def reactor_design(cfg: PlantConfig, n_modules: int | None = None) -> ReactorDesign:
    """给定模块数与单模块功率，算氦流量、风机功与 IHX 热负荷。"""

    r = cfg.reactor
    n = r.n_modules if n_modules is None else int(n_modules)
    q_core = r.module_power_mwth * n

    p_core_out = r.p_cold_bar * r.core_pr
    p_ihx_out = p_core_out * r.ihx_pr
    pi = r.p_cold_bar / p_ihx_out

    h_ihx_out = h_helium(p_ihx_out, r.t_ihx_out_c)
    s_ihx_out = s_helium(p_ihx_out, r.t_ihx_out_c)
    h_s = h_helium_ps(r.p_cold_bar, s_ihx_out)
    h_core_in = h_ihx_out + (h_s - h_ihx_out) / r.eta_circulator
    t_core_in = t_helium(r.p_cold_bar, h_core_in)

    h_hot = h_helium(p_core_out, r.t_hot_c)
    dh = h_hot - h_core_in
    if dh <= 0.0:
        raise ValueError(tr("err.helium_dh"))

    m_per_module = r.module_power_mwth * 1000.0 / dh
    circ_per_module = m_per_module * (h_core_in - h_ihx_out) / 1000.0

    return ReactorDesign(
        n_modules=n,
        module_power_mwth=r.module_power_mwth,
        q_core_mw=q_core,
        p_cold_bar=r.p_cold_bar,
        p_core_out_bar=p_core_out,
        p_ihx_out_bar=p_ihx_out,
        circulator_pressure_ratio=pi,
        t_hot_c=r.t_hot_c,
        t_ihx_out_c=r.t_ihx_out_c,
        t_core_in_c=t_core_in,
        h_core_in=h_core_in,
        h_hot=h_hot,
        h_ihx_out=h_ihx_out,
        m_helium_kgs=m_per_module * n,
        m_per_module_kgs=m_per_module,
        p_circulator_mw=circ_per_module * n,
        eta_circulator_motor=r.eta_circulator_motor,
    )


def ihx_section(
    q_ihx_mw: float,
    t_he_in_c: float,
    t_he_out_c: float,
    p_he_in_bar: float,
    p_he_out_bar: float,
    t_salt_cold_c: float,
    t_salt_hot_c: float,
) -> Section:
    """IHX：氦侧 750 -> 340 °C，盐侧 冷罐 -> 565 °C，逆流，沿程积分。"""

    he_profile = temperature_profile(
        h_helium(p_he_in_bar, t_he_in_c),
        h_helium(p_he_out_bar, t_he_out_c),
        p_he_in_bar,
        p_he_out_bar,
        lambda p, h: t_helium(p, h),
    )
    h_salt_hot = salt_h(t_salt_hot_c)
    h_salt_cold = salt_h(t_salt_cold_c)
    salt_profile = [
        salt_t(h_salt_hot + (i + 0.5) / 60 * (h_salt_cold - h_salt_hot))
        for i in range(60)
    ]
    return section_integrated(
        "IHX",
        q_ihx_mw,
        t_hot_profile=he_profile,
        t_cold_profile=salt_profile,
        dt_hot_end_k=t_he_in_c - t_salt_hot_c,
        dt_cold_end_k=t_he_out_c - t_salt_cold_c,
    )


def modules_for_self_sufficiency(cfg: PlantConfig, q_steam_mw: float) -> int:
    """使"堆 + 风机"恰好覆盖汽轮机热负荷所需的最少模块数（不含储热）。"""

    for n in range(1, 65):
        try:
            design = reactor_design(cfg, n)
        except ValueError:
            continue
        if design.q_ihx_mw >= q_steam_mw:
            return n
    raise ValueError(tr("err.no_module_count", q=q_steam_mw))
