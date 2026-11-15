"""汽水侧：一个接口，两套分段策略。

为什么是"策略"而不是"两个函数"
------------------------------
亚临界与超临界在汽水侧的差别只有**分段边界怎么定**这一件事：

.. code-block:: text

    亚临界（汽包炉）                    超临界（直流炉）
    ─────────────────────              ─────────────────────────────
    省煤器：给水 -> T_sat - 过冷度       省煤器：给水 -> 省煤器出口温度
    蒸发器：省煤器出口 -> 饱和汽         蒸发段：省煤器出口 -> 水冷壁出口
    过热器：饱和汽 -> 主蒸汽             过热器：水冷壁出口 -> 主蒸汽
    再热器：冷再热 -> 热再热             再热器：冷再热 -> 热再热

其余三件事——四段热负荷的焓差公式、沿程温度积分、端差约束——**完全一样**。
所以这里是一个 Protocol（隐式的：同样的入参、同样的 :class:`SteamDuties` 出参）
加两个实现，由 :func:`evaluate_steam_side` 按 ``cfg.plant.turbine_class`` 分派。

对外只暴露两样东西：
* :func:`evaluate_steam_side` —— 100 % 工况的 :class:`SteamSide`；
* :func:`duties_at` —— 任意工况的四段热负荷（变工况与额定点共用一套口径）。

下游（盐侧反解、定尺寸、状态点、报告）只认 :class:`SteamSide`，不再关心机型。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import PlantConfig, ensure_resolved
from .i18n import tr
from .properties import (
    h_sat_liq,
    h_sat_vap,
    h_water,
    pseudo_critical_t,
    t_sat,
    t_water,
)
from .thermal import N_SEGMENTS, temperature_profile

__all__ = [
    "SteamDuties",
    "SteamProfiles",
    "SteamSide",
    "build_steam_profiles",
    "duties_at",
    "evaluate_steam_side",
    "section_label",
]

#: 四台换热器的内部取值（盐侧反解、U 值、状态点都以它为键）
SECTION_KEYS = ("过热器", "蒸发器", "省煤器", "再热器")


def section_label(name: str, *, supercritical: bool) -> str:
    """对外显示名（随语言）。``蒸发器`` 在超临界下是"蒸发段（水冷壁）"。"""

    if name == "蒸发器":
        key = "section.evaporator_once_through" if supercritical else "section.evaporator"
        return tr(key)
    mapping = {
        "过热器": "section.superheater",
        "省煤器": "section.economizer",
        "再热器": "section.reheater",
        "IHX": "section.ihx",
    }
    key = mapping.get(name)
    return tr(key) if key else name


# ---------------------------------------------------------------------------
# 四段热负荷
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SteamDuties:
    """四台换热器的汽水侧热负荷（任意工况）。"""

    q_economizer_mw: float
    q_evaporator_mw: float
    q_superheater_mw: float
    q_reheater_mw: float

    @property
    def q_boiler_mw(self) -> float:
        return self.q_economizer_mw + self.q_evaporator_mw + self.q_superheater_mw

    @property
    def q_steam_mw(self) -> float:
        return self.q_boiler_mw + self.q_reheater_mw


# ---------------------------------------------------------------------------
# 策略 A：亚临界（饱和线分段）
# ---------------------------------------------------------------------------
def _duties_subcritical(
    *,
    p_drum_bar: float,
    p_main_bar: float,
    p_cr_bar: float,
    p_hr_bar: float,
    p_fw_bar: float,
    t_main_c: float,
    t_cr_c: float,
    t_hr_c: float,
    t_fw_c: float,
    subcooling_k: float,
    m_main_kgs: float,
    m_rh_kgs: float,
) -> SteamDuties:
    """由一组**显式**的汽水状态算四台换热器的热负荷（亚临界）。"""

    t_sat_drum = t_sat(p_drum_bar)
    t_econ_out = t_sat_drum - subcooling_k

    h_fw = h_water(p_fw_bar, t_fw_c)
    h_econ_out = h_water(p_drum_bar, t_econ_out)
    h_f = h_sat_liq(p_drum_bar)
    h_g = h_sat_vap(p_drum_bar)
    h_main = h_water(p_main_bar, t_main_c)
    h_cr = h_water(p_cr_bar, t_cr_c)
    h_hr = h_water(p_hr_bar, t_hr_c)

    if h_main <= h_g:
        raise ValueError(tr("err.h_main_below_sat"))
    if h_econ_out >= h_f:
        raise ValueError(tr("err.h_econ_above_sat"))

    return SteamDuties(
        q_economizer_mw=m_main_kgs * (h_econ_out - h_fw) / 1000.0,
        q_evaporator_mw=m_main_kgs * (h_g - h_econ_out) / 1000.0,
        q_superheater_mw=m_main_kgs * (h_main - h_g) / 1000.0,
        q_reheater_mw=m_rh_kgs * (h_hr - h_cr) / 1000.0,
    )


# ---------------------------------------------------------------------------
# 策略 B：超临界（水冷壁出口 + 拟临界分段）
# ---------------------------------------------------------------------------
def _duties_once_through(
    *,
    p_boiler_bar: float,
    p_main_bar: float,
    p_cr_bar: float,
    p_hr_bar: float,
    p_fw_bar: float,
    t_wall_out_c: float,
    t_econ_out_c: float,
    t_main_c: float,
    t_cr_c: float,
    t_hr_c: float,
    t_fw_c: float,
    m_main_kgs: float,
    m_rh_kgs: float,
) -> SteamDuties:
    """由一组**显式**的汽水状态算四段热负荷（超临界，无相变）。"""

    h_fw = h_water(p_fw_bar, t_fw_c)
    h_econ_out = h_water(p_boiler_bar, t_econ_out_c)
    h_wall_out = h_water(p_boiler_bar, t_wall_out_c)
    h_main = h_water(p_main_bar, t_main_c)
    h_cr = h_water(p_cr_bar, t_cr_c)
    h_hr = h_water(p_hr_bar, t_hr_c)

    if h_main <= h_wall_out:
        raise ValueError(tr("err.h_main_below_wall"))
    if h_wall_out <= h_econ_out:
        raise ValueError(tr("err.wall_below_econ"))
    if h_econ_out <= h_fw:
        raise ValueError(tr("err.econ_below_fw"))

    return SteamDuties(
        q_economizer_mw=m_main_kgs * (h_econ_out - h_fw) / 1000.0,
        q_evaporator_mw=m_main_kgs * (h_wall_out - h_econ_out) / 1000.0,
        q_superheater_mw=m_main_kgs * (h_main - h_wall_out) / 1000.0,
        q_reheater_mw=m_rh_kgs * (h_hr - h_cr) / 1000.0,
    )


# ---------------------------------------------------------------------------
# 分派：任意工况
# ---------------------------------------------------------------------------
def duties_at(
    cfg: PlantConfig,
    *,
    ratio: float,
    t_fw_c: float,
    m_main_kgs: float,
    m_rh_kgs: float,
) -> SteamDuties:
    """给定负荷比与给水温度，算四段热负荷。

    变工况的三条假设都在调用方（``offdesign``）：滑压比、给水温度、级组效率。
    这里只负责"同一套热平衡公式，按机型选分段基准"：

    * 亚临界：省煤器出口 = 该压力下的饱和温度 - 过冷度；
    * 超临界：省煤器出口随给水温度平移（保持段内温升不变），水冷壁出口按恒定。
    """

    cfg = ensure_resolved(cfg)
    p = cfg.plant
    if p.is_supercritical:
        if p.boiler_p_bar is None or p.wall_outlet_t_c is None:
            raise ValueError(tr("err.missing_sc_fields"))
        if p.economizer_outlet_t_c is None:
            raise ValueError(tr("err.missing_sc_econ"))
        econ_rise_k = p.economizer_outlet_t_c - p.feedwater_t_c
        return _duties_once_through(
            p_boiler_bar=p.boiler_p_bar * ratio,
            p_main_bar=p.main_steam_p_bar * ratio,
            p_cr_bar=p.cold_reheat_p_bar * ratio,
            p_hr_bar=p.hot_reheat_p_bar * ratio,
            p_fw_bar=p.feedwater_p_bar * ratio,
            t_wall_out_c=p.wall_outlet_t_c,
            t_econ_out_c=t_fw_c + econ_rise_k,
            t_main_c=p.main_steam_t_c,
            t_cr_c=p.cold_reheat_t_c,
            t_hr_c=p.hot_reheat_t_c,
            t_fw_c=t_fw_c,
            m_main_kgs=m_main_kgs,
            m_rh_kgs=m_rh_kgs,
        )

    if p.drum_p_bar is None or p.economizer_subcooling_k is None:
        raise ValueError(tr("err.missing_sub_fields"))
    return _duties_subcritical(
        p_drum_bar=p.drum_p_bar * ratio,
        p_main_bar=p.main_steam_p_bar * ratio,
        p_cr_bar=p.cold_reheat_p_bar * ratio,
        p_hr_bar=p.hot_reheat_p_bar * ratio,
        p_fw_bar=p.feedwater_p_bar * ratio,
        t_main_c=p.main_steam_t_c,
        t_cr_c=p.cold_reheat_t_c,
        t_hr_c=p.hot_reheat_t_c,
        t_fw_c=t_fw_c,
        subcooling_k=p.economizer_subcooling_k,
        m_main_kgs=m_main_kgs,
        m_rh_kgs=m_rh_kgs,
    )


# ---------------------------------------------------------------------------
# 额定工况
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SteamProfiles:
    """四台换热器**汽水侧**的沿程温度（按汽水流向，冷端 -> 热端）。

    这些曲线只取决于业主给的汽水参数，与盐侧设计无关，整次求解只需算一次。
    键名固定为"过热器 / 蒸发器 / 省煤器 / 再热器"，与盐侧、U 值、状态点一致；
    超临界下"蒸发器"的显示名换成"蒸发段（水冷壁）"，但**内部键名不变**——
    这样盐侧反解与报告都不需要知道机型。
    """

    superheater: tuple[float, ...]
    evaporator: tuple[float, ...]
    economizer: tuple[float, ...]
    reheater: tuple[float, ...]

    def as_dict(self) -> dict[str, tuple[float, ...]]:
        return {
            "过热器": self.superheater,
            "蒸发器": self.evaporator,
            "省煤器": self.economizer,
            "再热器": self.reheater,
        }


@dataclass(frozen=True)
class SteamSide:
    """四台换热器的汽水侧边界与热负荷（100 % 工况）。

    亚临界专属字段（``t_sat_c`` / ``h_sat_liq`` / ``h_sat_vap``）与超临界专属
    字段（``t_wall_out_c`` / ``t_pc_c`` / ``h_wall_out`` / ``h_pc``）同时存在，
    但**同一时刻只有一套非空**。下游一律用通用属性（``t_evap_out_c`` 等），
    不要直接读专属字段。
    """

    turbine_class: str

    p_main_bar: float
    t_main_c: float
    m_main_kgs: float

    p_cr_bar: float
    t_cr_c: float
    m_rh_kgs: float
    p_hr_bar: float
    t_hr_c: float

    p_boiler_bar: float
    p_fw_bar: float
    t_fw_c: float
    t_econ_out_c: float

    h_fw: float
    h_econ_out: float
    h_evap_out: float
    h_main: float
    h_cr: float
    h_hr: float

    q_economizer_mw: float
    q_evaporator_mw: float
    q_superheater_mw: float
    q_reheater_mw: float

    # 亚临界
    t_sat_c: float | None = None
    h_sat_liq: float | None = None
    h_sat_vap: float | None = None

    # 超临界
    t_wall_out_c: float | None = None
    t_pc_c: float | None = None
    h_wall_out: float | None = None
    h_pc: float | None = None

    @property
    def supercritical(self) -> bool:
        return self.turbine_class != "subcritical"

    @property
    def q_boiler_mw(self) -> float:
        return self.q_economizer_mw + self.q_evaporator_mw + self.q_superheater_mw

    @property
    def q_steam_mw(self) -> float:
        """四台换热器的总热负荷 = 新锅炉必须提供的热量。"""

        return self.q_boiler_mw + self.q_reheater_mw

    @property
    def t_evap_out_c(self) -> float:
        """蒸发段汽侧出口温度：亚临界是饱和温度，超临界是水冷壁出口。"""

        value = self.t_sat_c if self.t_sat_c is not None else self.t_wall_out_c
        if value is None:
            raise ValueError("SteamSide 既无 t_sat_c 也无 t_wall_out_c")
        return value

    @property
    def q_preheat_mw(self) -> float:
        """蒸发段里"从省煤器出口到饱和水 / 拟临界"的那部分显热。"""

        if self.h_sat_liq is not None:
            return self.m_main_kgs * (self.h_sat_liq - self.h_econ_out) / 1000.0
        h_pc = self.h_pc
        if h_pc is None and self.t_pc_c is not None:
            h_pc = h_water(self.p_boiler_bar, self.t_pc_c)
        if h_pc is None:
            raise ValueError("超临界 SteamSide 缺少 t_pc_c / h_pc")
        return self.m_main_kgs * (h_pc - self.h_econ_out) / 1000.0

    def label(self, name: str) -> str:
        return section_label(name, supercritical=self.supercritical)

    def as_rows(self) -> list[dict]:
        """四段换热器的汽水侧表格（报告与网站共用）。"""

        evap = self.label("蒸发器")
        return [
            {
                "section": "省煤器",
                "q_mw": self.q_economizer_mw,
                "steam_in_c": self.t_fw_c,
                "steam_out_c": self.t_econ_out_c,
                "p_bar": self.p_boiler_bar,
                "m_kgs": self.m_main_kgs,
            },
            {
                "section": evap,
                "q_mw": self.q_evaporator_mw,
                "steam_in_c": self.t_econ_out_c,
                "steam_out_c": self.t_evap_out_c,
                "p_bar": self.p_boiler_bar,
                "m_kgs": self.m_main_kgs,
            },
            {
                "section": "过热器",
                "q_mw": self.q_superheater_mw,
                "steam_in_c": self.t_evap_out_c,
                "steam_out_c": self.t_main_c,
                "p_bar": self.p_main_bar,
                "m_kgs": self.m_main_kgs,
            },
            {
                "section": "再热器",
                "q_mw": self.q_reheater_mw,
                "steam_in_c": self.t_cr_c,
                "steam_out_c": self.t_hr_c,
                "p_bar": self.p_cr_bar,
                "m_kgs": self.m_rh_kgs,
            },
        ]


def evaluate_steam_side(cfg: PlantConfig) -> SteamSide:
    """把业主给的汽水参数换算成四台换热器的热负荷（按机型分派）。"""

    cfg = ensure_resolved(cfg)
    p = cfg.plant

    if p.is_supercritical:
        if p.boiler_p_bar is None or p.wall_outlet_t_c is None:
            raise ValueError(tr("err.missing_sc_fields"))
        if p.economizer_outlet_t_c is None:
            raise ValueError(tr("err.missing_sc_econ"))

        t_pc = pseudo_critical_t(p.boiler_p_bar)
        duties = _duties_once_through(
            p_boiler_bar=p.boiler_p_bar,
            p_main_bar=p.main_steam_p_bar,
            p_cr_bar=p.cold_reheat_p_bar,
            p_hr_bar=p.hot_reheat_p_bar,
            p_fw_bar=p.feedwater_p_bar,
            t_wall_out_c=p.wall_outlet_t_c,
            t_econ_out_c=p.economizer_outlet_t_c,
            t_main_c=p.main_steam_t_c,
            t_cr_c=p.cold_reheat_t_c,
            t_hr_c=p.hot_reheat_t_c,
            t_fw_c=p.feedwater_t_c,
            m_main_kgs=p.main_steam_m_kgs,
            m_rh_kgs=p.reheat_m_kgs,
        )
        return SteamSide(
            turbine_class=p.turbine_class,
            p_main_bar=p.main_steam_p_bar,
            t_main_c=p.main_steam_t_c,
            m_main_kgs=p.main_steam_m_kgs,
            p_cr_bar=p.cold_reheat_p_bar,
            t_cr_c=p.cold_reheat_t_c,
            m_rh_kgs=p.reheat_m_kgs,
            p_hr_bar=p.hot_reheat_p_bar,
            t_hr_c=p.hot_reheat_t_c,
            p_boiler_bar=p.boiler_p_bar,
            p_fw_bar=p.feedwater_p_bar,
            t_fw_c=p.feedwater_t_c,
            t_econ_out_c=p.economizer_outlet_t_c,
            h_fw=h_water(p.feedwater_p_bar, p.feedwater_t_c),
            h_econ_out=h_water(p.boiler_p_bar, p.economizer_outlet_t_c),
            h_evap_out=h_water(p.boiler_p_bar, p.wall_outlet_t_c),
            h_main=h_water(p.main_steam_p_bar, p.main_steam_t_c),
            h_cr=h_water(p.cold_reheat_p_bar, p.cold_reheat_t_c),
            h_hr=h_water(p.hot_reheat_p_bar, p.hot_reheat_t_c),
            q_economizer_mw=duties.q_economizer_mw,
            q_evaporator_mw=duties.q_evaporator_mw,
            q_superheater_mw=duties.q_superheater_mw,
            q_reheater_mw=duties.q_reheater_mw,
            t_wall_out_c=p.wall_outlet_t_c,
            t_pc_c=t_pc,
            h_wall_out=h_water(p.boiler_p_bar, p.wall_outlet_t_c),
            h_pc=h_water(p.boiler_p_bar, t_pc),
        )

    if p.drum_p_bar is None or p.economizer_subcooling_k is None:
        raise ValueError(tr("err.missing_sub_fields"))

    t_sat_drum = t_sat(p.drum_p_bar)
    t_econ_out = t_sat_drum - p.economizer_subcooling_k
    duties = _duties_subcritical(
        p_drum_bar=p.drum_p_bar,
        p_main_bar=p.main_steam_p_bar,
        p_cr_bar=p.cold_reheat_p_bar,
        p_hr_bar=p.hot_reheat_p_bar,
        p_fw_bar=p.feedwater_p_bar,
        t_main_c=p.main_steam_t_c,
        t_cr_c=p.cold_reheat_t_c,
        t_hr_c=p.hot_reheat_t_c,
        t_fw_c=p.feedwater_t_c,
        subcooling_k=p.economizer_subcooling_k,
        m_main_kgs=p.main_steam_m_kgs,
        m_rh_kgs=p.reheat_m_kgs,
    )
    h_f = h_sat_liq(p.drum_p_bar)
    h_g = h_sat_vap(p.drum_p_bar)
    return SteamSide(
        turbine_class=p.turbine_class,
        p_main_bar=p.main_steam_p_bar,
        t_main_c=p.main_steam_t_c,
        m_main_kgs=p.main_steam_m_kgs,
        p_cr_bar=p.cold_reheat_p_bar,
        t_cr_c=p.cold_reheat_t_c,
        m_rh_kgs=p.reheat_m_kgs,
        p_hr_bar=p.hot_reheat_p_bar,
        t_hr_c=p.hot_reheat_t_c,
        p_boiler_bar=p.drum_p_bar,
        p_fw_bar=p.feedwater_p_bar,
        t_fw_c=p.feedwater_t_c,
        t_econ_out_c=t_econ_out,
        h_fw=h_water(p.feedwater_p_bar, p.feedwater_t_c),
        h_econ_out=h_water(p.drum_p_bar, t_econ_out),
        h_evap_out=h_g,
        h_main=h_water(p.main_steam_p_bar, p.main_steam_t_c),
        h_cr=h_water(p.cold_reheat_p_bar, p.cold_reheat_t_c),
        h_hr=h_water(p.hot_reheat_p_bar, p.hot_reheat_t_c),
        q_economizer_mw=duties.q_economizer_mw,
        q_evaporator_mw=duties.q_evaporator_mw,
        q_superheater_mw=duties.q_superheater_mw,
        q_reheater_mw=duties.q_reheater_mw,
        t_sat_c=t_sat_drum,
        h_sat_liq=h_f,
        h_sat_vap=h_g,
    )


def build_steam_profiles(steam: SteamSide, n: int = N_SEGMENTS) -> SteamProfiles:
    """沿汽水流向取四台换热器汽水侧的温度分布（只算一次）。"""

    return SteamProfiles(
        superheater=tuple(
            temperature_profile(
                steam.h_evap_out,
                steam.h_main,
                steam.p_boiler_bar,
                steam.p_main_bar,
                t_water,
                n,
            )
        ),
        evaporator=tuple(
            temperature_profile(
                steam.h_econ_out,
                steam.h_evap_out,
                steam.p_boiler_bar,
                steam.p_boiler_bar,
                t_water,
                n,
            )
        ),
        economizer=tuple(
            temperature_profile(
                steam.h_fw,
                steam.h_econ_out,
                steam.p_fw_bar,
                steam.p_boiler_bar,
                t_water,
                n,
            )
        ),
        reheater=tuple(
            temperature_profile(
                steam.h_cr,
                steam.h_hr,
                steam.p_cr_bar,
                steam.p_hr_bar,
                t_water,
                n,
            )
        ),
    )
