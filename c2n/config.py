"""输入参数、机组类型判定与校验。

一个输入族，两种机组
--------------------
亚临界与超临界**不是两个模型**，而是同一个内核的两种蒸汽侧分段约定：

======================  ==========================  ==============================
                       亚临界（汽包炉）            超临界 / 超超临界（直流炉）
======================  ==========================  ==============================
分段边界                饱和温度 ``T_sat(p_drum)``  水冷壁出口温度（业主给定）
蒸发段的含义            省煤器出口 -> 饱和汽        省煤器出口 -> 水冷壁出口（无相变）
专属输入                ``drum_p_bar``              ``boiler_p_bar`` / ``wall_outlet_t_c``
                        ``economizer_subcooling_k`` ``economizer_outlet_t_c``
盐（分解温度）          太阳盐 600 °C               新型硝酸盐 650 °C（假设）
======================  ==========================  ==============================

因此 ``turbine_class`` 是**唯一的判别字段**：填 ``auto``（默认）时由压力推断，
填具体值时以它为准。判定之后，另一套专属字段会被清空，下游代码不需要再猜。

两类问题，两种处理
------------------
* **硬错误**（``ConfigError``）：物理上算不出来，或机组类型自相矛盾。例如流量
  为负、给水温度高于主蒸汽温度、声明"超临界"但主蒸汽只有 16.7 MPa。
* **软提示**（返回字符串列表）：能照算，但结果可能不可信或需要业主核实。例如
  毛电与主蒸汽流量隐含的循环效率超出常见区间、热盐温度逼近分解温度、储热量级
  异常。**软提示一律不阻断计算**——这是本工具"照算 + 警示"的原则：业主的输入
  往往就是不全的，先给出一份带标注的结果，比弹一个错误框有用。

单位：温度 °C，压力 bar(a)，功率/热量 MW，流量 kg/s，焓 kJ/kg。

关于厂用电的口径
----------------
一台煤电机组改造前后，汽轮机岛的自用电基本不变，变化的是锅炉侧：

* 煤粉炉辅机（送风机、引风机、磨煤机、一次风机）**随锅炉一起消失**——``boiler_aux_mw``
* 氦风机与盐泵**新增**——由模型自己算
* 汽轮机岛辅机（给水泵、凝结水泵、循环水泵）**保留**——``turbine_island_aux_mw``

因此"改造前后净电差" = ``boiler_aux_mw − 氦风机 − 盐泵``，与汽轮机岛辅机无关。
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass, replace
from pathlib import Path
from typing import Any

from .fluids import AUTO, SaltFluid, default_salt_for, get_salt
from .i18n import EN, get_language, tr

__all__ = [
    "AUTO",
    "ConfigError",
    "CoalPlantConfig",
    "PlantConfig",
    "ReactorConfig",
    "SaltConfig",
    "SizingConfig",
    "StorageConfig",
    "PartLoadConfig",
    "WATER_CRITICAL_P_BAR",
    "DEFAULT_CONFIG",
    "dump_config",
    "efficiency_band",
    "load_config",
    "resolve",
    "validate",
]


class ConfigError(ValueError):
    """输入自相矛盾，或超出模型能算的范围。"""


#: 水的临界压力 bar。高于它就没有饱和线，蒸汽侧按直流炉处理。
WATER_CRITICAL_P_BAR = 220.64

#: 允许的机组类型。``ultra_supercritical`` 与 ``supercritical`` 在本内核中
#: **走同一条超临界策略**，区别只在报告措辞（前者主蒸汽温度 ≥ 580 °C）。
TURBINE_CLASSES = ("subcritical", "supercritical", "ultra_supercritical")

#: 未给专属字段时的工程缺省值（会附软提示，要求业主用热力特性书核对）
DEFAULT_SUBCOOLING_K = 5.0
DEFAULT_WALL_OUTLET_C = 450.0
DEFAULT_ECON_OUTLET_C = 340.0


# ---------------------------------------------------------------------------
# 1. 业主机组（汽轮机侧边界）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CoalPlantConfig:
    """既有汽轮机侧的 100 % 工况边界条件（亚临界与超临界共用一套字段）。

    这四台换热器是锅炉与汽轮机的耦合面，也是本次改造唯一要动的界面。
    业主只需给出这四段的汽水状态与流量；两种机组的差别只在"分段边界由什么
    决定"（见模块文档）。
    """

    name: str = "600 MW 亚临界再热机组"

    turbine_class: str = AUTO
    """``auto`` / ``subcritical`` / ``supercritical`` / ``ultra_supercritical``。

    ``auto``（默认）时按下面顺序判定，第一个命中者为准：

    1. 显式给了 ``boiler_p_bar`` -> 超临界；
    2. ``main_steam_p_bar ≥ 220.64 bar`` -> 超临界；
    3. 否则 -> 亚临界。

    判定结果会写回 ``turbine_class``，并把另一套专属字段清空。
    """

    gross_power_mw: float = 589.84
    """汽轮机 100 % 工况毛功率（MW）。

    口径由 ``gross_basis`` 决定：``"generator"``（默认）表示**发电机端输出**
    （业主铭牌上的数），此时不再乘发电机/机械效率；``"shaft"`` 表示**轴功率**，
    模型会用 ``generator_eta * mechanical_eta`` 折算到发电机端。
    """

    gross_basis: str = "generator"
    """``"generator"``（默认，铭牌）或 ``"shaft"``（轴功率）。"""

    main_steam_p_bar: float = 166.7
    main_steam_t_c: float = 538.0
    main_steam_m_kgs: float = 479.913

    # ---- 亚临界专属 -------------------------------------------------------
    drum_p_bar: float | None = 185.0
    """汽包（蒸汽发生器）压力 bar，必须低于 220.64。仅亚临界使用。"""

    economizer_subcooling_k: float | None = DEFAULT_SUBCOOLING_K
    """省煤器出口过冷度 K，决定省煤器 / 蒸发器的分界。仅亚临界使用。"""

    # ---- 超临界专属 -------------------------------------------------------
    boiler_p_bar: float | None = None
    """水冷壁出口压力 bar（= 主蒸汽压力 + 过热器压损），必须高于 220.64。
    仅超临界使用；``auto`` 判定时它一出现就按超临界处理。"""

    wall_outlet_t_c: float | None = None
    """水冷壁出口（= 蒸发段出口）温度 °C。仅超临界使用，业主从热力特性书取。"""

    economizer_outlet_t_c: float | None = None
    """省煤器出口温度 °C（须低于该压力下的拟临界温度）。仅超临界使用。"""

    # ---- 两种机组共有 -----------------------------------------------------
    cold_reheat_p_bar: float = 36.0
    cold_reheat_t_c: float = 314.936
    reheat_m_kgs: float = 401.039
    reheater_pressure_ratio: float = 0.889
    hot_reheat_t_c: float = 538.0

    feedwater_p_bar: float = 200.0
    """给水压力（省煤器进口）。亚临界实机比汽包高 10~20 bar。"""
    feedwater_t_c: float = 273.585

    generator_eta: float = 0.988
    mechanical_eta: float = 0.995

    turbine_island_aux_mw: float = 26.5
    """汽轮机岛自用电（给水泵、凝结水泵、循环水泵等）。改造前后不变。

    缺省 26.5 MW 对应 600 MW 级机组的典型值（≈毛电的 4.5 %）。
    **必须换成业主的实际厂用电**：它只影响"绝对净电"，不影响净电变化。
    """

    boiler_aux_mw: float = 12.0
    """煤粉炉辅机自用电（送/引风机、磨煤机、一次风机）。

    这部分在 C2N 方案里**消失**，是改造的收益项。**必须换成业主的实际值**
    （锅炉侧辅机分项电表）。
    """

    @property
    def is_supercritical(self) -> bool:
        """是否走超临界策略。``auto`` 视为"还没判定"，一律按 False 处理——
        调用方应先经 :func:`ensure_resolved`，不要拿 ``auto`` 去猜。"""

        return self.turbine_class in ("supercritical", "ultra_supercritical")

    @property
    def p_boiler_bar(self) -> float:
        """锅炉出口压力：亚临界是汽包压力，超临界是水冷壁出口压力。"""

        value = self.drum_p_bar if self.drum_p_bar is not None else self.boiler_p_bar
        if value is None:
            raise ConfigError("既没有 drum_p_bar 也没有 boiler_p_bar：无法确定锅炉压力")
        return float(value)

    @property
    def hot_reheat_p_bar(self) -> float:
        return self.cold_reheat_p_bar * self.reheater_pressure_ratio

    @property
    def gross_electric_mw(self) -> float:
        """折算到发电机端的毛电功率。"""

        if self.gross_basis == "shaft":
            return self.gross_power_mw * self.generator_eta * self.mechanical_eta
        return self.gross_power_mw

    @property
    def grade_label(self) -> str:
        """对外称呼（随当前语言）。"""

        if get_language() == EN:
            return {
                "subcritical": "subcritical",
                "supercritical": "supercritical",
                "ultra_supercritical": "ultra-supercritical",
            }.get(self.turbine_class, self.turbine_class)
        return {
            "subcritical": "亚临界",
            "supercritical": "超临界",
            "ultra_supercritical": "超超临界",
        }.get(self.turbine_class, self.turbine_class)


# ---------------------------------------------------------------------------
# 2. 高温气冷堆
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReactorConfig:
    """模块式高温气冷堆一回路（氦气）。"""

    module_power_mwth: float = 250.0
    n_modules: int = 4

    t_hot_c: float = 750.0
    t_ihx_out_c: float = 340.0
    p_cold_bar: float = 70.0

    core_pr: float = 0.98
    ihx_pr: float = 0.98
    eta_circulator: float = 0.85

    eta_circulator_motor: float = 0.97
    """氦风机电机效率：轴功 / 电机效率 = 电耗（TESPy 算的是轴功）。"""

    @property
    def q_core_mw(self) -> float:
        return self.module_power_mwth * self.n_modules


# ---------------------------------------------------------------------------
# 3. 熔盐回路
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SaltConfig:
    t_hot_c: float = 565.0
    """热罐温度 °C。亚临界太阳盐取 565（分解 600，留 35 K）；超临界取 620。"""

    fluid: str = AUTO
    """盐种标识，见 ``c2n.fluids.SALT_FLUIDS``。

    ``auto`` 时按机组类型推断：亚临界 -> ``solar_salt``，超临界 ->
    ``advanced_nitrate``（因为太阳盐做不出 600 °C 主蒸汽）。
    """

    main_branch_fraction: float = 0.825
    optimise_split: bool = True
    """是否把主支路份额也当作设计变量反解（使两分支端差同时达到设计值）。

    ``False`` 时按 ``main_branch_fraction`` 固定。
    """

    min_pinch_k: float = 5.0

    p_bar: float = 3.0
    pump_dp_bar: float = 2.0
    eta_pump: float = 0.80
    eta_pump_motor: float = 0.97

    ua_margin: float = 1.0
    """换热面积裕量系数（1.10 = 加 10 %）。乘在"选型 kA"上，不改变所需 kA。"""

    u_superheater_w_m2k: float = 1000.0
    u_evaporator_w_m2k: float = 2000.0
    u_economizer_w_m2k: float = 1300.0
    u_reheater_w_m2k: float = 1000.0
    """四台盐-汽换热器的总传热系数（W/m²K），用于把 kA 折算成 m²。

    典型范围：过热器/再热器 800~1200（蒸汽单相，h 低）；蒸发器 1500~2500
    （沸腾/拟临界区 h 高）；省煤器 1000~1500。仅用于量级估算。
    """

    u_he_salt_w_m2k: float = 800.0
    """氦-盐 IHX 总传热系数（W/m²K），典型 600~1200。"""

    freezing_margin_k: float = 20.0
    """设计阶段：冷盐温度相对**该盐凝固点**的最小裕量。"""

    t_warn_freeze_c: float = 220.0
    """变工况告警：盐回路最低温度低于该值即报"凝固风险"。"""

    t_warn_margin_c: float = 290.0
    """变工况告警：盐回路最低温度低于该值即报"距凝固温度裕量不足"。"""

    @property
    def spec(self) -> SaltFluid:
        """解析后的盐种。``auto`` 只有在未经 :func:`resolve` 时才会出现。"""

        if self.fluid == AUTO:
            raise ConfigError(
                "[salt].fluid 仍是 'auto'：请先调用 c2n.config.resolve()，"
                "或显式指定 'solar_salt' / 'advanced_nitrate'"
            )
        return get_salt(self.fluid)

    @property
    def t_salt_max_c(self) -> float:
        return self.spec.t_decompose_c

    @property
    def t_freeze_c(self) -> float:
        return self.spec.t_freeze_c

    def u_for(self, section: str) -> float:
        """按设备名取总传热系数。"""

        return {
            "过热器": self.u_superheater_w_m2k,
            "蒸发器": self.u_evaporator_w_m2k,
            "省煤器": self.u_economizer_w_m2k,
            "再热器": self.u_reheater_w_m2k,
        }[section]


# ---------------------------------------------------------------------------
# 4. 储热
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StorageConfig:
    hours: float = 4.0
    """在 100 % 汽轮机工况下，储罐可维持（放热）或充满（吸热）的小时数。"""


# ---------------------------------------------------------------------------
# 5. 设备几何（只用于把 kA 折算成"多大"）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SizingConfig:
    """把面积折成外形、把盐量折成罐径的几何假设。

    这些数不影响热力结果，只影响"换热器多大、罐多大"的估算，按行业常规取值
    即可，误差 ±30 %。
    """

    tube_od_mm: float = 25.0
    """换热管外径（mm）。盐-汽与氦-盐换热器常用 Ø19~25。"""

    tube_wall_mm: float = 2.0
    tube_length_m: float = 12.0
    """单根管长（m）。立式壳管式常用 6~15 m。"""

    pitch_ratio: float = 1.25
    """管间距 / 管外径。正三角形排列常取 1.25~1.5。"""

    shell_clearance_m: float = 0.35
    """管束与壳体内壁的间隙（m），含折流板与检修空间。"""

    max_shell_id_m: float = 4.0
    """单台壳体最大内径（m）。超过则并联多台（运输限制）。"""

    tank_height_to_diameter: float = 0.4
    """储罐高径比 H/D。工业双罐熔盐储热都是"扁胖型"（0.35~0.5）。"""

    ullage: float = 0.12
    """储罐气相空间占罐容积的比例（液位波动 + 氮封）。"""

    salt_inventory_margin: float = 0.10
    """回路内滞留盐量占储罐活动盐量的比例（管道、IHX、换热器持液）。"""


# ---------------------------------------------------------------------------
# 6. 非额定工况（不建模汽轮机通流）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PartLoadConfig:
    """非额定工况估算的三条假设 + 一条辅机分解。

    只引入三个可标定的工程关系，其余（四台热负荷、辅机、储罐）都是精确计算：

    * 滑压规律 ``p/p0 = (m/m0)^k``，低于下限转定压节流；
    * 级组效率修正 ``eta/eta0 = f(load)``（下表）；
    * 给水温度：给表就用表，留空则按抽汽压力缩放估算。
    """

    load_points: tuple[float, ...] = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3)
    """要算的负荷点（相对汽轮机额定出力的比例），必须从 1.0 递减。"""

    eta_ratio: tuple[float, ...] = (
        1.000, 0.992, 0.981, 0.967, 0.950, 0.930, 0.903, 0.870,
    )
    """汽轮机**蒸汽循环效率**相对额定值的修正，与 ``load_points`` 一一对应。

    只修正"级组效率 + 末级排汽损失 + 抽汽压力变化"这一块，不含锅炉/辅机。
    缺省值取自亚临界再热机组滑压运行的常规范围；超临界机组应换成
    ``(1.000, 0.993, 0.983, 0.970, 0.954, 0.935, 0.909, 0.876)``。
    """

    sliding_exponent: float = 0.90
    """滑压指数 k：``p/p0 = (m/m0)^k``。再热机组常取 0.85~1.0。"""

    sliding_floor: float = 0.70
    """滑压下限（相对额定流量的流量比）。低于它转定压节流，压力不再降。"""

    turbine_aux_fixed_fraction: float = 0.40
    """汽轮机岛辅机里"不随负荷变"的那部分占多大比例。"""

    feedwater_t_c: tuple[float, ...] = (
        273.6, 271.0, 267.5, 263.0, 257.5, 251.0, 243.0, 233.5,
    )
    """给水温度随负荷的表（°C），与 ``load_points`` 一一对应。

    缺省值是 600 MW 级亚临界机组的常规数据；换机组请改表。
    留空 ``[]`` 表示不填：按"抽汽级数不变、抽汽压力随滑压比缩放"估算。
    """


# ---------------------------------------------------------------------------
# 7. 总配置
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PlantConfig:
    plant: CoalPlantConfig = field(default_factory=CoalPlantConfig)
    reactor: ReactorConfig = field(default_factory=ReactorConfig)
    salt: SaltConfig = field(default_factory=SaltConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    partload: PartLoadConfig = field(default_factory=PartLoadConfig)

    notes: tuple[str, ...] = field(default=(), compare=False)
    """解析阶段的**假设性说明**（不是输入，不写进 TOML）。

    例如"未给汽包压力，按主蒸汽压力 / 0.90 反推"。业主必须看到这些，否则会
    把模型的缺省值当成他给的数据。一致性与可行性提示在 :func:`validate` 里
    单独计算，避免重复。
    """



DEFAULT_CONFIG = PlantConfig()

_SECTIONS = {
    "plant": CoalPlantConfig,
    "reactor": ReactorConfig,
    "salt": SaltConfig,
    "storage": StorageConfig,
    "sizing": SizingConfig,
    "partload": PartLoadConfig,
}


# ---------------------------------------------------------------------------
# 8. 机组类型判定与缺省值补齐
# ---------------------------------------------------------------------------
def _infer_class(p: CoalPlantConfig) -> tuple[str, list[str]]:
    """按"显式 > 锅炉压力 > 主蒸汽压力 > 汽包"的顺序判定机组类型。"""

    notes: list[str] = []
    raw = (p.turbine_class or AUTO).strip().lower()

    if raw not in ("auto",) + TURBINE_CLASSES:
        raise ConfigError(
            f"plant.turbine_class = {p.turbine_class!r} 无效；"
            f"应为 'auto' 或 {list(TURBINE_CLASSES)}"
        )

    if raw != AUTO:
        return raw, notes

    if p.boiler_p_bar is not None:
        return "supercritical", notes
    if p.main_steam_p_bar >= WATER_CRITICAL_P_BAR:
        return "supercritical", notes
    if p.drum_p_bar is not None:
        return "subcritical", notes

    notes.append(tr("note.infer_overall"))
    return "subcritical", notes


def resolve(cfg: PlantConfig) -> tuple[PlantConfig, list[str]]:
    """把"业主写的"配置变成"求解器要的"配置。

    做三件事，并返回过程中产生的软提示：

    1. 判定 ``turbine_class``（若为 ``auto``）；
    2. 补齐全套专属缺省值（缺哪个补哪个，每补一个都留一条提示）；
    3. 清空另一套专属字段，使下游"看到非空就是本类型"。

    这个函数是**幂等**的：对已解析的配置再调用一次不会产生新提示。
    """

    p = cfg.plant
    cls, notes = _infer_class(p)

    if cls == "subcritical":
        drum = p.drum_p_bar
        if drum is None:
            drum = p.main_steam_p_bar / 0.90
            notes.append(tr("note.sub_drum", drum=drum))
        subcool = p.economizer_subcooling_k
        if subcool is None:
            subcool = DEFAULT_SUBCOOLING_K
            notes.append(tr("note.subcooling", sub=subcool))
        plant = replace(
            p,
            turbine_class="subcritical",
            drum_p_bar=float(drum),
            economizer_subcooling_k=float(subcool),
            boiler_p_bar=None,
            wall_outlet_t_c=None,
            economizer_outlet_t_c=None,
        )
    else:
        boiler = p.boiler_p_bar
        if boiler is None:
            boiler = p.main_steam_p_bar * 1.05
            notes.append(tr("note.sc_boiler", p=boiler))
        wall = p.wall_outlet_t_c
        if wall is None:
            wall = min(DEFAULT_WALL_OUTLET_C, p.main_steam_t_c - 120.0)
            notes.append(tr("note.sc_wall", t=wall))
        econ = p.economizer_outlet_t_c
        if econ is None:
            econ = p.feedwater_t_c + 42.0
            notes.append(tr("note.sc_econ", t=econ))
        plant = replace(
            p,
            turbine_class=cls,
            drum_p_bar=None,
            economizer_subcooling_k=None,
            boiler_p_bar=float(boiler),
            wall_outlet_t_c=float(wall),
            economizer_outlet_t_c=float(econ),
        )

    salt = cfg.salt
    if salt.fluid == AUTO:
        spec = default_salt_for(plant.turbine_class)
        salt = replace(salt, fluid=spec.key)

    # 超临界用户常常只改"机组等级"而把热罐温度留在亚临界缺省值（565 °C）。
    # 565 < 600 时过热器没有传热温差，物理上无解——这不是"输入矛盾"，而是
    # "这一项还没填"。按主蒸汽 + 20 K 补一个可算的值，并留下明确提示。
    if plant.is_supercritical and salt.t_hot_c <= plant.main_steam_t_c:
        suggested = plant.main_steam_t_c + 20.0
        notes.append(
            tr(
                "note.salt_raised",
                old=salt.t_hot_c,
                main=plant.main_steam_t_c,
                new=suggested,
                salt=salt.spec.display_short,
            )
        )
        salt = replace(salt, t_hot_c=suggested)

    return replace(cfg, plant=plant, salt=salt), notes


# ---------------------------------------------------------------------------
# 9. TOML 读写
# ---------------------------------------------------------------------------
def _coerce(value: Any, current: Any) -> Any:
    if isinstance(current, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"expected a boolean, got {value!r}")
        return value
    if current is None:
        # 可选字段（亚临界/超临界专属）：按数值处理，避免 TOML 整数混进 float
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return float(value)
        return value
    if isinstance(current, float):
        return float(value)
    if isinstance(current, int):
        return int(value)
    if isinstance(current, str):
        return str(value)
    if isinstance(current, tuple):
        if not isinstance(value, (list, tuple)):
            raise ConfigError(f"expected an array, got {value!r}")
        return tuple(float(item) for item in value)
    return value


def apply_overrides(base: Any, data: dict[str, Any], *, where: str = "") -> Any:
    if not is_dataclass(base):
        raise ConfigError(f"{where or 'config'} is not a config section")
    known = {f.name for f in fields(base)}
    for key in data:
        if key not in known:
            hint = ""
            if key == "drum_p_bar":
                hint = "（亚临界专属；超临界请用 boiler_p_bar）"
            if key == "boiler_p_bar":
                hint = "（超临界专属；亚临界请用 drum_p_bar）"
            raise ConfigError(
                f"unknown key {where + '.' if where else ''}{key}; "
                f"expected one of {sorted(known)}{hint}"
            )
    kwargs: dict[str, Any] = {}
    for name in known:
        if name not in data:
            continue
        current = getattr(base, name)
        value = data[name]
        kwargs[name] = (
            apply_overrides(current, value, where=f"{where}.{name}".lstrip("."))
            if is_dataclass(current)
            else _coerce(value, current)
        )
    return replace(base, **kwargs)


def load_config(path: str | Path | None = None) -> PlantConfig:
    """读入 TOML（``None`` 用内置默认值），判定机组类型并校验。"""

    cfg = DEFAULT_CONFIG
    if path is not None:
        text = Path(path).read_text(encoding="utf-8-sig")
        data = tomllib.loads(text)
        unknown = set(data) - set(_SECTIONS)
        if unknown:
            raise ConfigError(
                f"unknown section(s) {sorted(unknown)}; expected {sorted(_SECTIONS)}"
            )
        cfg = apply_overrides(cfg, data)
    return finalize(cfg)


def ensure_resolved(cfg: PlantConfig) -> PlantConfig:
    """需要时先 :func:`resolve`（幂等），供"接受用户原始配置"的公开函数使用。

    这样 ``validate(raw)``、``evaluate_steam_side(raw)`` 这类直接调用不会因为
    忘了先 resolve 而崩在某个 ``None`` 上——合并两个内核时踩过这个坑。
    """

    if cfg.plant.turbine_class == AUTO or cfg.salt.fluid == AUTO:
        cfg, _ = resolve(cfg)
    return cfg


def finalize(cfg: PlantConfig) -> PlantConfig:
    """:func:`resolve` + :func:`validate`（硬错误立即抛出）。

    返回的配置只携带**解析阶段**的假设说明（``cfg.notes``）；一致性/可行性
    提示由 :func:`validate` 在求解时再算一次，避免两处口径分家。
    """

    cfg, notes = resolve(cfg)
    validate(cfg)
    return replace(cfg, notes=tuple(cfg.notes) + tuple(notes))


# ---------------------------------------------------------------------------
# 10. 校验
# ---------------------------------------------------------------------------
def efficiency_band(p_main_bar: float) -> tuple[float, float]:
    """毛电 / 汽侧吸热的合理区间，按主蒸汽压力等级分。

    12~14 MPa 超高压机组约 37~41 %，16.7 MPa 亚临界约 43~47 %，25~27 MPa
    超超临界约 45~48 %。用一个 40~50 % 的区间去卡前者会误报。
    """

    if p_main_bar < 155.0:
        return 0.36, 0.45
    if p_main_bar < WATER_CRITICAL_P_BAR:
        return 0.40, 0.50
    return 0.42, 0.52


def validate(cfg: PlantConfig) -> list[str]:
    """硬性检查（raise :class:`ConfigError`）与软性提示（return）。

    中文/英文由 :mod:`c2n.i18n` 决定；数字与判据两种语言完全一致。
    """

    p, r, s, st = cfg.plant, cfg.reactor, cfg.salt, cfg.storage
    if p.turbine_class == AUTO or s.fluid == AUTO:
        cfg = ensure_resolved(cfg)
        p, r, s, st = cfg.plant, cfg.reactor, cfg.salt, cfg.storage
    if p.turbine_class == AUTO:
        raise ConfigError(tr("err.unknown_class"))

    errors: list[str] = []
    sup = p.is_supercritical

    # ---- 汽轮机侧：两种机组共有 ------------------------------------------
    if p.gross_power_mw <= 0.0:
        errors.append(tr("err.positive", name="plant.gross_power_mw"))
    if p.gross_basis not in {"generator", "shaft"}:
        errors.append(tr("err.gross_basis"))
    if p.main_steam_m_kgs <= 0.0:
        errors.append(tr("err.positive", name="plant.main_steam_m_kgs"))
    if p.reheat_m_kgs <= 0.0:
        errors.append(tr("err.positive", name="plant.reheat_m_kgs"))
    if p.feedwater_t_c >= p.main_steam_t_c:
        errors.append(tr("err.feedwater_above_main"))
    if p.turbine_island_aux_mw < 0.0:
        errors.append(tr("err.non_negative", name="plant.turbine_island_aux_mw"))
    if p.boiler_aux_mw < 0.0:
        errors.append(tr("err.non_negative", name="plant.boiler_aux_mw"))
    if not 0.0 < p.generator_eta <= 1.0 or not 0.0 < p.mechanical_eta <= 1.0:
        errors.append(tr("err.efficiencies"))
    if not 0.8 <= p.reheater_pressure_ratio < 1.0:
        errors.append(tr("err.reheater_ratio"))

    # ---- 机组类型与专属字段：结构性问题必须拦 ------------------------------
    if sup:
        if (
            p.boiler_p_bar is None
            or p.wall_outlet_t_c is None
            or p.economizer_outlet_t_c is None
        ):
            errors.append(tr("err.sc_missing"))
        elif p.boiler_p_bar <= WATER_CRITICAL_P_BAR:
            errors.append(
                tr(
                    "err.sc_pressure_below_critical",
                    grade=p.grade_label,
                    p=p.boiler_p_bar,
                    pc=WATER_CRITICAL_P_BAR,
                )
            )
        elif not (
            p.feedwater_t_c
            < p.economizer_outlet_t_c
            < p.wall_outlet_t_c
            < p.main_steam_t_c
        ):
            errors.append(
                tr(
                    "err.sc_temp_order",
                    fw=p.feedwater_t_c,
                    econ=p.economizer_outlet_t_c,
                    wall=p.wall_outlet_t_c,
                    main=p.main_steam_t_c,
                )
            )
        if p.boiler_p_bar is not None and p.boiler_p_bar <= p.main_steam_p_bar:
            errors.append(tr("err.boiler_p_le_main"))
    else:
        if p.main_steam_p_bar >= WATER_CRITICAL_P_BAR:
            errors.append(
                tr(
                    "err.sub_main_above_critical",
                    p=p.main_steam_p_bar,
                    pc=WATER_CRITICAL_P_BAR,
                )
            )
        elif p.drum_p_bar is None or p.economizer_subcooling_k is None:
            errors.append(tr("err.sub_missing"))
        elif p.drum_p_bar >= WATER_CRITICAL_P_BAR:
            errors.append(
                tr(
                    "err.drum_above_critical",
                    p=p.drum_p_bar,
                    pc=WATER_CRITICAL_P_BAR,
                )
            )
        elif p.drum_p_bar <= p.main_steam_p_bar:
            errors.append(tr("err.drum_le_main"))
        elif p.economizer_subcooling_k <= 0.0:
            errors.append(tr("err.subcooling"))

    # ---- 堆 -----------------------------------------------------------------
    if r.n_modules < 1:
        errors.append(tr("err.positive", name="reactor.n_modules"))
    if r.module_power_mwth <= 0.0:
        errors.append(tr("err.positive", name="reactor.module_power_mwth"))
    if r.t_hot_c <= r.t_ihx_out_c:
        errors.append(tr("err.reactor_t_hot"))
    if not 0.0 < r.eta_circulator < 1.0:
        errors.append(tr("err.open_unit", name="reactor.eta_circulator"))
    if not 0.0 < r.eta_circulator_motor <= 1.0:
        errors.append(tr("err.half_open_unit", name="reactor.eta_circulator_motor"))

    # ---- 盐 -----------------------------------------------------------------
    if cfg.salt.fluid == AUTO:
        errors.append(tr("err.salt_fluid_unresolved"))
    if s.t_hot_c <= r.t_ihx_out_c:
        errors.append(tr("err.salt_hot_le_ihx"))
    if s.t_hot_c <= p.main_steam_t_c:
        errors.append(tr("err.salt_hot_le_main", salt=s.t_hot_c, main=p.main_steam_t_c))
    if not 0.0 < s.main_branch_fraction < 1.0:
        errors.append(tr("err.open_unit", name="salt.main_branch_fraction"))
    if s.min_pinch_k <= 0.0:
        errors.append(tr("err.min_pinch"))
    if not 0.0 < s.eta_pump < 1.0:
        errors.append(tr("err.open_unit", name="salt.eta_pump"))
    if not 0.0 < s.eta_pump_motor <= 1.0:
        errors.append(tr("err.half_open_unit", name="salt.eta_pump_motor"))
    if s.ua_margin < 1.0:
        errors.append(tr("err.ua_margin"))
    if s.t_warn_freeze_c >= s.t_warn_margin_c:
        errors.append(tr("err.warn_order"))
    if s.u_he_salt_w_m2k <= 0.0:
        errors.append(tr("err.positive", name="salt.u_he_salt_w_m2k"))
    for key in (
        "u_superheater_w_m2k",
        "u_evaporator_w_m2k",
        "u_economizer_w_m2k",
        "u_reheater_w_m2k",
    ):
        if getattr(s, key) <= 0.0:
            errors.append(tr("err.positive", name=f"salt.{key}"))

    # ---- 储热与几何 ---------------------------------------------------------
    if st.hours <= 0.0:
        errors.append(tr("err.storage_hours"))

    z = cfg.sizing
    if (
        z.tube_od_mm <= 0.0
        or z.tube_wall_mm < 0.0
        or z.tube_wall_mm >= 0.5 * z.tube_od_mm
    ):
        errors.append(tr("err.tube_geometry"))
    if z.tube_length_m <= 0.0:
        errors.append(tr("err.positive", name="sizing.tube_length_m"))
    if z.pitch_ratio < 1.05:
        errors.append(tr("err.pitch_ratio"))
    if z.shell_clearance_m < 0.0:
        errors.append(tr("err.non_negative", name="sizing.shell_clearance_m"))
    if z.max_shell_id_m <= 2.0 * z.shell_clearance_m + 0.2:
        errors.append(tr("err.max_shell_id"))
    if z.tank_height_to_diameter <= 0.0:
        errors.append(tr("err.positive", name="sizing.tank_height_to_diameter"))
    if not 0.0 <= z.ullage < 0.5:
        errors.append(tr("err.ullage"))
    if z.salt_inventory_margin < 0.0:
        errors.append(tr("err.non_negative", name="sizing.salt_inventory_margin"))

    # ---- 变工况 -------------------------------------------------------------
    lc = cfg.partload
    n = len(lc.load_points)
    if n == 0:
        errors.append(tr("err.partload_empty"))
    if len(lc.eta_ratio) != n:
        errors.append(tr("err.partload_len", got=len(lc.eta_ratio), want=n))
    if lc.feedwater_t_c and len(lc.feedwater_t_c) != n:
        errors.append(tr("err.partload_fw_len", got=len(lc.feedwater_t_c), want=n))
    for i, load in enumerate(lc.load_points):
        if not 0.0 < load <= 1.0:
            errors.append(tr("err.partload_point", i=i))
        if i and lc.load_points[i - 1] <= load:
            errors.append(tr("err.partload_desc"))
    for i, ratio in enumerate(lc.eta_ratio):
        if not 0.2 < ratio <= 1.05:
            errors.append(tr("err.partload_ratio", i=i))
        if i and lc.eta_ratio[i - 1] < ratio:
            errors.append(tr("err.partload_monotonic"))
    if not 0.5 <= lc.sliding_exponent <= 1.5:
        errors.append(tr("err.sliding_exponent"))
    if not 0.3 <= lc.sliding_floor <= 1.0:
        errors.append(tr("err.sliding_floor"))
    if not 0.0 <= lc.turbine_aux_fixed_fraction <= 1.0:
        errors.append(tr("err.turbine_aux_fraction"))

    if errors:
        raise ConfigError(tr("err.prefix") + "\n  - " + "\n  - ".join(errors))

    # ---- 软提示：照算，但要说清楚 -------------------------------------------
    warnings: list[str] = []
    spec = s.spec

    if not spec.commercial:
        warnings.append(
            tr(
                "warn.salt_noncommercial",
                label=spec.display_label,
                note=spec.display_note,
            )
        )
    if s.t_hot_c > spec.t_decompose_c:
        warnings.append(
            tr(
                "warn.salt_above",
                t=s.t_hot_c,
                salt=spec.display_short,
                limit=spec.t_decompose_c,
            )
        )
    elif spec.t_decompose_c - s.t_hot_c < 15.0:
        warnings.append(
            tr(
                "warn.salt_margin",
                t=s.t_hot_c,
                salt=spec.display_short,
                limit=spec.t_decompose_c,
            )
        )
    if p.main_steam_t_c > s.t_hot_c - 15.0:
        warnings.append(
            tr("warn.main_close_salt", main=p.main_steam_t_c, salt=s.t_hot_c)
        )
    if sup and p.main_steam_t_c < 580.0:
        warnings.append(
            tr("warn.usc_low_temp", grade=p.grade_label, t=p.main_steam_t_c)
        )
    if not sup and p.drum_p_bar is not None:
        if p.drum_p_bar > 200.0:
            warnings.append(tr("warn.drum_high", p=p.drum_p_bar))
        if p.feedwater_p_bar < p.drum_p_bar + 5.0:
            warnings.append(
                tr("warn.fw_low_drum", fw=p.feedwater_p_bar, drum=p.drum_p_bar)
            )
    if sup and p.boiler_p_bar is not None and p.feedwater_p_bar < p.boiler_p_bar + 20.0:
        warnings.append(
            tr("warn.fw_low_boiler", fw=p.feedwater_p_bar, boiler=p.boiler_p_bar)
        )
    if p.main_steam_p_bar < 90.0:
        warnings.append(tr("warn.main_low"))
    if s.min_pinch_k < 5.0:
        warnings.append(tr("warn.pinch_low"))
    if p.boiler_aux_mw <= 0.0:
        warnings.append(tr("warn.boiler_aux_zero"))
    if p.turbine_island_aux_mw <= 0.0:
        warnings.append(tr("warn.turbine_aux_zero"))
    if p.reheat_m_kgs > p.main_steam_m_kgs:
        warnings.append(
            tr("warn.reheat_gt_main", reheat=p.reheat_m_kgs, main=p.main_steam_m_kgs)
        )
    return warnings


# ---------------------------------------------------------------------------
# 11. 导出 TOML
# ---------------------------------------------------------------------------
_FIELD_NOTES: dict[str, str] = {
    "name": "机组名称（只用于报告标题）",
    "turbine_class": '"auto" | "subcritical" | "supercritical" | "ultra_supercritical"',
    "gross_power_mw": "汽轮机 100% 工况毛功率；口径见 gross_basis",
    "gross_basis": '"generator"=发电机端（铭牌，推荐）| "shaft"=轴功率',
    "main_steam_p_bar": "主蒸汽压力（过热器出口）",
    "main_steam_t_c": "主蒸汽温度（过热器出口）",
    "main_steam_m_kgs": "主蒸汽流量（= 给水流量，假设无减温水）",
    "drum_p_bar": "【亚临界】汽包压力（必须低于 220.6 bar）",
    "economizer_subcooling_k": "【亚临界】省煤器出口过冷度（决定省煤器/蒸发器分界）",
    "boiler_p_bar": "【超临界】水冷壁出口压力 bar（必须高于 220.6 bar）",
    "wall_outlet_t_c": "【超临界】水冷壁出口温度 °C（决定蒸发段/过热段分界）",
    "economizer_outlet_t_c": "【超临界】省煤器出口温度 °C（须低于拟临界温度）",
    "cold_reheat_p_bar": "冷再热压力（再热器进口）",
    "cold_reheat_t_c": "冷再热温度（再热器进口）",
    "reheat_m_kgs": "再热蒸汽流量（≠ 主蒸汽流量，有抽汽）",
    "reheater_pressure_ratio": "冷再热 -> 热再热压比（含再热系统压损）",
    "hot_reheat_t_c": "热再热温度（再热器出口）",
    "feedwater_p_bar": "给水压力（省煤器进口）",
    "feedwater_t_c": "给水温度（省煤器进口）",
    "generator_eta": "发电机效率（仅 gross_basis=shaft 时使用）",
    "mechanical_eta": "机械效率（仅 gross_basis=shaft 时使用）",
    "turbine_island_aux_mw": "汽轮机岛自用电（给水/凝结水/循环水泵），改造前后不变",
    "boiler_aux_mw": "煤粉炉辅机自用电，改造后消失",
    "module_power_mwth": "单模块热功率 MWth",
    "n_modules": "模块数（并联，每个模块一套氦回路 + IHX）",
    "t_hot_c": "【堆】堆芯出口氦温 °C；【盐】热罐温度 °C（两处同名，见所属段）",
    "t_ihx_out_c": "IHX 氦侧出口（= 氦风机进口）°C",
    "p_cold_bar": "氦气冷段压力 bar",
    "core_pr": "堆芯压比",
    "ihx_pr": "IHX 氦侧压比",
    "eta_circulator": "氦风机等熵效率",
    "eta_circulator_motor": "氦风机电机效率（轴功 -> 电耗）",
    "fluid": '"auto" | "solar_salt" | "advanced_nitrate"',
    "main_branch_fraction": "主支路流量份额（optimise_split=False 时生效）",
    "optimise_split": "true=把支路份额也反解（推荐）；false=按 main_branch_fraction 固定",
    "min_pinch_k": "允许的最小端差 K；冷盐温度按它反解",
    "p_bar": "盐回路压力 bar",
    "pump_dp_bar": "盐泵压损 bar",
    "eta_pump": "盐泵水力效率",
    "eta_pump_motor": "盐泵电机效率",
    "ua_margin": "换热面积裕量系数（1.10 = 加 10%）",
    "u_superheater_w_m2k": "过热器总传热系数 W/m2K（典型 800~1200）",
    "u_evaporator_w_m2k": "蒸发器/水冷壁总传热系数 W/m2K（1500~2500）",
    "u_economizer_w_m2k": "省煤器总传热系数 W/m2K（1000~1500）",
    "u_reheater_w_m2k": "再热器总传热系数 W/m2K（800~1200）",
    "u_he_salt_w_m2k": "氦-盐 IHX 总传热系数 W/m2K",
    "freezing_margin_k": "冷盐温度相对凝固点的最小裕量 K",
    "t_warn_freeze_c": "变工况告警：盐最低温度低于此值报凝固风险 °C",
    "t_warn_margin_c": "变工况告警：盐最低温度低于此值报裕量不足 °C",
    "hours": "100% 工况下储罐可维持（或充满）的小时数",
    "tube_od_mm": "换热管外径 mm",
    "tube_wall_mm": "换热管壁厚 mm",
    "tube_length_m": "单根管长 m",
    "pitch_ratio": "管间距/管外径（正三角形 1.25~1.5）",
    "shell_clearance_m": "管束与壳体内壁的间隙 m",
    "max_shell_id_m": "单台壳体最大内径 m（超过则并联）",
    "tank_height_to_diameter": "储罐高径比 H/D（0.35~0.5）",
    "ullage": "储罐气相空间比例（0.10~0.15）",
    "salt_inventory_margin": "回路内滞留盐量占活动盐量的比例",
    "load_points": "要算的负荷点（相对额定出力），从 1.0 递减",
    "eta_ratio": "汽轮机循环效率相对额定的修正，与 load_points 一一对应",
    "sliding_exponent": "滑压指数 k：p/p0 = (m/m0)^k（0.85~1.0）",
    "sliding_floor": "滑压下限（流量比），低于它转定压节流",
    "turbine_aux_fixed_fraction": "汽轮机岛辅机中不随负荷变的比例",
    "feedwater_t_c": "【变工况】给水温度随负荷的表；留空 [] = 按抽汽压力缩放估算",
}


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ConfigError("cannot write a non-finite value to TOML")
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, str):
        return f'"{value}"'
    if value is None:
        raise ConfigError(
            "cannot serialise None to TOML：请先 resolve()，或删除该可选字段"
        )
    raise ConfigError(f"cannot serialise {value!r}")


def dump_config(cfg: PlantConfig, *, with_comments: bool = True) -> str:
    lines: list[str] = [
        "# =============================================================================",
        "# C2N 锅炉侧替换方案 —— 输入参数（由 run.py --write-config 导出）",
        "# 单位：温度 °C，压力 bar(a)，功率/热量 MW，流量 kg/s",
        "# 亚临界填 [plant] 的 drum_p_bar / economizer_subcooling_k；",
        "# 超临界填 boiler_p_bar / wall_outlet_t_c / economizer_outlet_t_c。",
        "# 不确定就留 turbine_class = \"auto\"，由压力自动判定。",
        "# =============================================================================",
        "",
    ]
    for section, klass in _SECTIONS.items():
        lines.append(f"[{section}]")
        for field_def in fields(klass):
            value = getattr(getattr(cfg, section), field_def.name)
            if value is None:
                continue
            if with_comments:
                note = _FIELD_NOTES.get(field_def.name, "")
                if note:
                    lines.append(f"# {note}")
            lines.append(f"{field_def.name} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)
