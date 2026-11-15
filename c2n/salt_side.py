"""盐侧反解：给定汽水侧四段热负荷与 IHX 热负荷，求盐回路与储罐。

设计的自由度是**冷罐温度**（等价于盐流量）和**支路份额**。它们由"四台换热器
沿程最小温差 = 设计端差"这一组约束反解出来：

* 冷罐温度越低 -> 盐温差越大 -> 流量越小、储罐越省，但冷端先被卡住；
* 主支路份额越大 -> 主支路越松、再热支路越紧。

注意约束用的是**沿程最小温差**而不是两端温差：蒸汽在饱和线附近比热剧变，
内部夹点可能比两端都紧（例如省煤器内部温差可以比热端低 6 K 以上）。
沿程温差由"汽侧温度曲线（只算一次）× 盐侧温度曲线"逐点相减得到，成本很低。

单调性：
* 固定 f 时，沿程最小温差随 t_cold 单调增加 -> 二分法；
* 固定约束时，主支路与再热支路的"松紧差"随 f 单调 -> 外层二分。

几何关系
--------
    放热侧盐流量  m_dsg = Q_steam / (h(565) - h(t_cold))     （蒸汽发生器回路）
    充热侧盐流量  m_chg = Q_IHX   / (h(565) - h(t_cold))     （IHX 回路）
    Q_net = Q_IHX - Q_steam  -> 储罐放热（负）或充热（正）
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import PlantConfig
from .i18n import tr
from .properties import salt_density, salt_h, salt_t, t_water
from .steam_side import SteamProfiles, SteamSide
from .thermal import (
    N_SEGMENTS,
    Section,
    section_integrated,
    temperature_profile,
    with_selection,
)

#: 反解支路份额时搜索的区间
_SPLIT_RANGE = (0.70, 0.92)


def freezing_point_c(cfg: PlantConfig) -> float:
    """冷罐温度的设计下界基准：该盐凝固点 + ``freezing_margin_k``。

    旧代码把 238 °C 写成模块常量 ``SALT_FREEZING_C``，于是"换一种盐"要改两处
    （常量 + 校验）。现在它是 ``cfg.salt.spec.t_freeze_c``。
    """

    return cfg.salt.spec.t_freeze_c


@dataclass(frozen=True)
class SaltLoop:
    """盐回路设计结果。"""

    t_hot_c: float
    t_cold_c: float

    m_discharge_kgs: float
    m_charge_kgs: float
    m_main_kgs: float
    m_reheat_kgs: float

    t_sh_out_c: float
    t_ev_out_c: float
    t_econ_out_c: float
    t_rh_out_c: float
    t_boil_onset_c: float

    cp_mean_kj_kgk: float
    """h(565)-h(t_cold) 除以温差得到的名义平均比热（仅供估算，不是物性）。"""

    delta_t_k: float

    sections: dict[str, Section]
    pinches: dict[str, float]
    """两端温差（终端）。"""

    path_minima: dict[str, float]
    """沿程最小温差（真实夹点）。"""

    min_pinch_k: float
    target_pinch_k: float
    binding: str
    """最紧的位置（提示业主该处是设计瓶颈）。"""

    split_fraction: float


@dataclass(frozen=True)
class StorageSizing:
    """双罐储热定容。"""

    hours: float
    capacity_mwh: float
    net_power_mw: float
    mode: str
    mass_t: float
    cp_kj_kgk: float
    delta_t_k: float
    v_hot_m3: float
    v_cold_m3: float
    total_m3: float

    @property
    def volume_per_hour_m3(self) -> float:
        return self.total_m3 / self.hours if self.hours > 0 else float("nan")


# ---------------------------------------------------------------------------
# 盐侧温度
# ---------------------------------------------------------------------------
def _state(
    cfg: PlantConfig, steam: SteamSide, t_cold_c: float, split_fraction: float
) -> tuple[dict[str, float], float, float]:
    """给定 (t_cold, f)，返回（四段盐温、放热流量、名义平均 cp）。"""

    s = cfg.salt
    h_hot = salt_h(s.t_hot_c)
    h_cold = salt_h(t_cold_c)
    dh = h_hot - h_cold
    if dh <= 0.0:
        raise ValueError(tr("err.salt_dh"))

    m_dsg = steam.q_steam_mw * 1000.0 / dh
    m_main = split_fraction * m_dsg
    m_rh = m_dsg - m_main

    t_sh = salt_t(h_hot - steam.q_superheater_mw * 1000.0 / m_main)
    t_ev = salt_t(salt_h(t_sh) - steam.q_evaporator_mw * 1000.0 / m_main)
    t_econ = salt_t(salt_h(t_ev) - steam.q_economizer_mw * 1000.0 / m_main)
    t_rh = salt_t(h_hot - steam.q_reheater_mw * 1000.0 / m_rh)
    t_boil = salt_t(salt_h(t_ev) + steam.q_preheat_mw * 1000.0 / m_main)

    temps = {
        "t_sh_out_c": t_sh,
        "t_ev_out_c": t_ev,
        "t_econ_out_c": t_econ,
        "t_rh_out_c": t_rh,
        "t_boil_onset_c": t_boil,
    }
    return temps, m_dsg, dh / (s.t_hot_c - t_cold_c)


def _salt_profile(t_in_c: float, t_out_c: float, n: int) -> list[float]:
    h_in, h_out = salt_h(t_in_c), salt_h(t_out_c)
    return [salt_t(h_in + (i + 0.5) / n * (h_out - h_in)) for i in range(n)]


def _salt_profiles(
    cfg: PlantConfig, temps: dict[str, float], n: int = N_SEGMENTS
) -> dict[str, list[float]]:
    """四台换热器盐侧沿程温度（f = 0 为汽水进口端 = 冷端）。"""

    t_hot = cfg.salt.t_hot_c
    return {
        "过热器": _salt_profile(temps["t_sh_out_c"], t_hot, n),
        "蒸发器": _salt_profile(temps["t_ev_out_c"], temps["t_sh_out_c"], n),
        "省煤器": _salt_profile(temps["t_econ_out_c"], temps["t_ev_out_c"], n),
        "再热器": _salt_profile(temps["t_rh_out_c"], t_hot, n),
    }


def _path_minima(
    salt_profiles: dict[str, list[float]], profiles: SteamProfiles
) -> dict[str, float]:
    """四台换热器**分段中点**上的最小温差（盐侧 - 汽侧）。

    注意：中点不含进出口本身，真实端差要另外并进来，见 :func:`_exchanger_minima`。
    """

    steam = profiles.as_dict()
    return {
        name: min(
            ts - tw for ts, tw in zip(salt_profiles[name], steam[name])
        )
        for name in salt_profiles
    }


def _terminals(
    cfg: PlantConfig, steam: SteamSide, temps: dict[str, float]
) -> dict[str, tuple[float, float]]:
    """每台换热器的（热端, 冷端）真实温差 K。"""

    t_hot = cfg.salt.t_hot_c
    return {
        "过热器": (
            t_hot - steam.t_main_c,
            temps["t_sh_out_c"] - steam.t_evap_out_c,
        ),
        "蒸发器": (
            temps["t_sh_out_c"] - steam.t_evap_out_c,
            temps["t_ev_out_c"] - steam.t_econ_out_c,
        ),
        "省煤器": (
            temps["t_ev_out_c"] - steam.t_econ_out_c,
            temps["t_econ_out_c"] - steam.t_fw_c,
        ),
        "再热器": (
            t_hot - steam.t_hr_c,
            temps["t_rh_out_c"] - steam.t_cr_c,
        ),
    }


def _exchanger_minima(
    cfg: PlantConfig,
    steam: SteamSide,
    profiles: SteamProfiles,
    t_cold_c: float,
    split_fraction: float,
) -> dict[str, float]:
    """四台换热器的**真实**最小温差 = min(沿程中点, 热端, 冷端)。"""

    temps, _, _ = _state(cfg, steam, t_cold_c, split_fraction)
    path = _path_minima(_salt_profiles(cfg, temps), profiles)
    ends = _terminals(cfg, steam, temps)
    return {
        name: min(path[name], ends[name][0], ends[name][1]) for name in path
    }


def _terminal_pinches(
    cfg: PlantConfig, steam: SteamSide, temps: dict[str, float]
) -> dict[str, float]:
    """两端温差（终端），用于报告展示。"""

    ends = _terminals(cfg, steam, temps)
    return {
        "过热器热端": ends["过热器"][0],
        "过热器冷端": ends["过热器"][1],
        "蒸发器热端": ends["蒸发器"][0],
        "蒸发器冷端": ends["蒸发器"][1],
        "省煤器热端": ends["省煤器"][0],
        "省煤器冷端": ends["省煤器"][1],
        "再热器热端": ends["再热器"][0],
        "再热器冷端": ends["再热器"][1],
    }


def _constraints(
    cfg: PlantConfig,
    steam: SteamSide,
    profiles: SteamProfiles,
    t_cold_c: float,
    split_fraction: float,
) -> tuple[float, float, float, dict[str, float]]:
    """返回（四台整体最小、主支路最小、再热支路最小、四台各自最小）。"""

    minima = _exchanger_minima(cfg, steam, profiles, t_cold_c, split_fraction)
    main = min(minima["过热器"], minima["蒸发器"], minima["省煤器"])
    rh = minima["再热器"]
    return min(main, rh), main, rh, minima


# ---------------------------------------------------------------------------
# 反解
# ---------------------------------------------------------------------------
def _bisect_cold(
    cfg: PlantConfig, steam: SteamSide, profiles: SteamProfiles, split_fraction: float
) -> float:
    """给定 f，反解使**沿程**最小温差等于设计值的 t_cold。"""

    s = cfg.salt
    freeze = freezing_point_c(cfg)
    lo = max(steam.t_fw_c + 2.0, freeze + s.freezing_margin_k)
    hi = s.t_hot_c - 3.0
    if lo >= hi:
        raise ValueError(tr("err.salt_bounds", lo=lo, hi=s.t_hot_c))

    target = s.min_pinch_k
    f_lo = _constraints(cfg, steam, profiles, lo, split_fraction)[0] - target
    f_hi = _constraints(cfg, steam, profiles, hi, split_fraction)[0] - target

    if f_hi < 0.0:
        achievable = _constraints(cfg, steam, profiles, hi, split_fraction)[0]
        raise ValueError(
            tr(
                "err.salt_pinch_unreachable",
                target=target,
                hi=hi,
                achievable=achievable,
            )
        )

    if f_lo >= 0.0:
        return lo

    a, b = lo, hi
    for _ in range(60):
        mid = 0.5 * (a + b)
        if _constraints(cfg, steam, profiles, mid, split_fraction)[0] - target < 0.0:
            a = mid
        else:
            b = mid
        if b - a < 1.0e-3:
            break
    return 0.5 * (a + b)


def _solve_split(
    cfg: PlantConfig, steam: SteamSide, profiles: SteamProfiles
) -> tuple[float, float]:
    """联合反解 (f, t_cold)：使主支路与再热支路的沿程端差同时达到设计值。"""

    s = cfg.salt

    def gap(split: float) -> float | None:
        try:
            t_cold = _bisect_cold(cfg, steam, profiles, split)
        except ValueError:
            return None
        _, main, rh, _ = _constraints(cfg, steam, profiles, t_cold, split)
        return main - rh

    lo, hi = _SPLIT_RANGE
    g_lo, g_hi = gap(lo), gap(hi)
    if g_lo is None or g_hi is None or g_lo * g_hi > 0.0:
        split = s.main_branch_fraction
        return split, _bisect_cold(cfg, steam, profiles, split)

    # g 随 f 单调递增（主支路份额越大，主支路越松、再热支路越紧）
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        g_mid = gap(mid)
        if g_mid is None:
            break
        if g_mid > 0.0:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1.0e-4:
            break
    split = 0.5 * (lo + hi)
    return split, _bisect_cold(cfg, steam, profiles, split)


def solve_salt_loop(
    cfg: PlantConfig,
    steam: SteamSide,
    profiles: SteamProfiles,
    q_ihx_mw: float,
) -> SaltLoop:
    """反解（支路份额）与冷罐温度，得到盐回路与四台换热器。"""

    if cfg.salt.optimise_split:
        split, t_cold = _solve_split(cfg, steam, profiles)
    else:
        split = cfg.salt.main_branch_fraction
        t_cold = _bisect_cold(cfg, steam, profiles, split)

    temps, m_dsg, cp = _state(cfg, steam, t_cold, split)
    path = _path_minima(_salt_profiles(cfg, temps), profiles)
    ends = _terminals(cfg, steam, temps)
    minima = {name: min(path[name], ends[name][0], ends[name][1]) for name in path}
    terminals = _terminal_pinches(cfg, steam, temps)

    h_hot = salt_h(cfg.salt.t_hot_c)
    h_cold = salt_h(t_cold)
    m_chg = q_ihx_mw * 1000.0 / (h_hot - h_cold)
    m_main = split * m_dsg
    m_rh = m_dsg - m_main

    sections = _sections(cfg, steam, profiles, temps)
    min_pinch = min(minima.values())
    binding = min(minima, key=minima.get)

    return SaltLoop(
        t_hot_c=cfg.salt.t_hot_c,
        t_cold_c=t_cold,
        m_discharge_kgs=m_dsg,
        m_charge_kgs=m_chg,
        m_main_kgs=m_main,
        m_reheat_kgs=m_rh,
        t_sh_out_c=temps["t_sh_out_c"],
        t_ev_out_c=temps["t_ev_out_c"],
        t_econ_out_c=temps["t_econ_out_c"],
        t_rh_out_c=temps["t_rh_out_c"],
        t_boil_onset_c=temps["t_boil_onset_c"],
        cp_mean_kj_kgk=cp,
        delta_t_k=cfg.salt.t_hot_c - t_cold,
        sections=sections,
        pinches=terminals,
        path_minima=minima,
        min_pinch_k=min_pinch,
        target_pinch_k=cfg.salt.min_pinch_k,
        binding=binding,
        split_fraction=split,
    )


def _sections(
    cfg: PlantConfig,
    steam: SteamSide,
    profiles: SteamProfiles,
    temps: dict[str, float],
) -> dict[str, Section]:
    """四台换热器的 kA：沿程积分，再按 U 与裕量折算选型 kA 与面积。"""

    s = cfg.salt
    steam_profiles = profiles.as_dict()
    salt_profiles = _salt_profiles(cfg, temps)
    ends = _terminals(cfg, steam, temps)

    sections: dict[str, Section] = {}
    for name, q in (
        ("过热器", steam.q_superheater_mw),
        ("蒸发器", steam.q_evaporator_mw),
        ("省煤器", steam.q_economizer_mw),
        ("再热器", steam.q_reheater_mw),
    ):
        salt_prof = salt_profiles[name]
        sections[name] = with_selection(
            section_integrated(
                name,
                q,
                t_hot_profile=salt_prof,
                t_cold_profile=steam_profiles[name],
                dt_hot_end_k=ends[name][0],
                dt_cold_end_k=ends[name][1],
            ),
            s.ua_margin,
            s.u_for(name),
        )
    return sections


# ---------------------------------------------------------------------------
# 储罐
# ---------------------------------------------------------------------------
def size_storage(
    cfg: PlantConfig, loop: SaltLoop, q_net_mw: float
) -> StorageSizing:
    """按"100 % 工况下维持/充满 N 小时"定容量。"""

    hours = cfg.storage.hours
    capacity = abs(q_net_mw) * hours
    delta_t = loop.delta_t_k
    mass_t = (
        capacity * 3600.0 / (loop.cp_mean_kj_kgk * delta_t) if capacity > 0 else 0.0
    )

    rho_hot = salt_density(loop.t_hot_c)
    rho_cold = salt_density(loop.t_cold_c)
    v_hot = mass_t * 1000.0 / rho_hot if mass_t else 0.0
    v_cold = mass_t * 1000.0 / rho_cold if mass_t else 0.0

    mode = "放热" if q_net_mw < 0 else ("充热" if q_net_mw > 0 else "平衡")
    return StorageSizing(
        hours=hours,
        capacity_mwh=capacity,
        net_power_mw=q_net_mw,
        mode=mode,
        mass_t=mass_t,
        cp_kj_kgk=loop.cp_mean_kj_kgk,
        delta_t_k=delta_t,
        v_hot_m3=v_hot,
        v_cold_m3=v_cold,
        total_m3=v_hot + v_cold,
    )
