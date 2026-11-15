"""把汽水侧、堆侧、盐侧、储罐组装成一份"改造方案"。

输出回答业主的三个问题：

1. **要多大堆**：给定模块数与单模块功率，堆能否覆盖汽轮机热负荷；
   覆盖不了的部分由储罐补（并给出小时数、以及放空后必须降到多少负荷）。
2. **要多少盐、多大罐**：盐流量、冷/热罐温度、储罐容积、太阳盐质量。
3. **四台换热器要多大**：每台的 Q、kA、面积、两端温差与沿程最小温差。

净电口径
--------
改造前后汽轮机与汽轮机岛辅机都不变，变化的只是锅炉侧：

    煤电（改造前）净电 = 毛电 - 汽轮机岛辅机 - 煤粉炉辅机
    C2N（改造后）净电 = 毛电 - 汽轮机岛辅机 - 氦风机 - 盐泵
    净电变化           = 煤粉炉辅机 - 氦风机 - 盐泵      （与汽轮机岛辅机无关）

报告中给出这三行同口径对比，避免"拿 C2N 净电比煤电毛电"。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .config import (
    PlantConfig,
    efficiency_band as _efficiency_band,
    finalize,
    validate,
)
from .properties import salt_density
from .reactor import (
    ReactorDesign,
    ihx_section,
    modules_for_self_sufficiency,
    reactor_design,
)
from .i18n import tr
from .offdesign import LoadPoint, run_part_load
from .salt_side import (
    SaltLoop,
    StorageSizing,
    freezing_point_c,
    size_storage,
    solve_salt_loop,
)
from .sizing import Sizing, build_sizing
from .steam_side import (
    SteamSide,
    build_steam_profiles,
    evaluate_steam_side,
    section_label,
)
from .thermal import Section, pump_power_mw, with_selection


@dataclass(frozen=True)
class Check:
    """一条设计校核。

    同时带**人读**与**机读**两种形态：报告用 ``value`` / ``criterion`` 的中文串，
    网站与契约用 ``id`` / ``value_num`` / ``limit_num`` / ``unit`` 画红黄绿。
    """

    name: str
    value: str
    criterion: str
    ok: bool
    id: str = ""
    value_num: float | None = None
    limit_num: float | None = None
    unit: str = ""

    def as_row(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "criterion": self.criterion,
            "ok": self.ok,
        }

    def as_contract(self) -> dict:
        """``docs/contracts/common.schema.json#/$defs/Check`` 的形态。"""

        row: dict = {"id": self.id or self.name, "ok": self.ok}
        if self.value_num is not None:
            row["value"] = round(float(self.value_num), 6)
        if self.limit_num is not None:
            row["limit"] = round(float(self.limit_num), 6)
        if self.unit:
            row["unit"] = self.unit
        row["message"] = tr(
            "design.check.message",
            name=self.name,
            value=self.value,
            criterion=self.criterion,
        )
        return row


@dataclass
class DesignResult:
    """一次完整评估的结果。"""

    cfg: PlantConfig
    steam: SteamSide
    reactor: ReactorDesign
    salt_loop: SaltLoop
    storage: StorageSizing
    ihx: Section
    sizing: Sizing

    q_net_mw: float
    mode: str

    gross_electric_mw: float
    aux_circulator_mw: float
    aux_salt_pump_mw: float
    aux_turbine_island_mw: float
    boiler_aux_mw: float
    net_power_mw: float
    coal_net_power_mw: float
    eta_cycle_pct: float
    """汽机侧净循环效率 = 净电 / 四台换热器吸热。

    这是**蒸汽循环**的效率：每从四台换热器吸走 100 份热，有多少份变成净电。
    它不是一次能源效率——储罐吞吐不进入分母，堆芯热功率也不进入分母。
    """
    sustainable_load_pct: float

    modules_self_sufficient: int
    q_core_self_sufficient_mw: float

    area_pinch_plus1_m2: float = 0.0
    """最小端差 +1 K 时四台换热器的总面积（面积对夹点的敏感度）。"""

    part_load: list[LoadPoint] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def feasible(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def supercritical(self) -> bool:
        return self.cfg.plant.is_supercritical

    @property
    def salt_short(self) -> str:
        """盐的简称（"太阳盐" / "solar salt"），随当前语言。"""

        return self.cfg.salt.spec.display_short

    @property
    def grade_label(self) -> str:
        return self.cfg.plant.grade_label

    @property
    def storage_needed(self) -> bool:
        return self.q_net_mw < 0.0

    @property
    def net_power_delta_mw(self) -> float:
        """改造前后净电变化 = 锅炉辅机 - 氦风机 - 盐泵。"""

        return self.net_power_mw - self.coal_net_power_mw

    def comparison_rows(self) -> list[tuple[str, str, str, str]]:
        """(项目, 煤电, C2N, 单位) —— 净电的同口径对比。"""

        return [
            (tr("comparison.gross"),
             f"{self.gross_electric_mw:.2f}",
             f"{self.gross_electric_mw:.2f}", "MW"),
            (tr("comparison.turbine_aux"),
             f"{self.aux_turbine_island_mw:.2f}",
             f"{self.aux_turbine_island_mw:.2f}", "MW"),
            (tr("comparison.boiler_aux"), f"{self.boiler_aux_mw:.2f}", "0.00", "MW"),
            (tr("comparison.new_aux"), "0.00",
             f"{self.aux_circulator_mw + self.aux_salt_pump_mw:.2f}", "MW"),
            (tr("comparison.net"), f"{self.coal_net_power_mw:.2f}",
             f"{self.net_power_mw:.2f}", "MW"),
            (tr("comparison.delta"), "—", f"{self.net_power_delta_mw:+.2f}", "MW"),
        ]

    def summary_rows(self) -> list[tuple[str, str, str]]:
        """(项目, 数值, 单位)，用于报告与终端输出。"""

        return [
            ("汽轮机毛电（发电机端）", f"{self.gross_electric_mw:.2f}", "MW"),
            ("四台换热器总热负荷", f"{self.steam.q_steam_mw:.2f}", "MW"),
            ("堆芯热功率", f"{self.reactor.q_core_mw:.2f}", "MWth"),
            ("氦风机轴功 / 电耗",
             f"{self.reactor.p_circulator_mw:.2f} / {self.aux_circulator_mw:.2f}", "MW"),
            ("IHX 热负荷", f"{self.reactor.q_ihx_mw:.2f}", "MW"),
            ("储罐净吞吐（+充热 / −放热）", f"{self.q_net_mw:+.2f}", "MW"),
            (
                "储热容量（{} h）".format(self.storage.hours),
                f"{self.storage.capacity_mwh:.0f}",
                "MWh",
            ),
            ("{}质量（储罐活动盐量）".format(self.cfg.salt.spec.short),
             f"{self.storage.mass_t:,.0f}", "t"),
            ("{}总量（含回路滞留）".format(self.cfg.salt.spec.short),
             f"{self.sizing.salt_total_t:,.0f}", "t"),
            ("冷/热罐总容积", f"{self.storage.total_m3:,.0f}", "m³"),
            ("储罐直径 × 高度",
             f"{self.sizing.hot_tank.diameter_m:.1f} / {self.sizing.cold_tank.diameter_m:.1f} × "
             f"{self.sizing.hot_tank.height_m:.1f}", "m"),
            ("四台换热器总面积", f"{self.sizing.area_total_m2:,.0f}", "m²"),
            ("换热器并联壳体数", f"{self.sizing.shell_count_total}", "台"),
            ("盐侧流量（放热 / 充热）",
             f"{self.salt_loop.m_discharge_kgs:.0f} / {self.salt_loop.m_charge_kgs:.0f}",
             "kg/s"),
            ("主支路份额（反解）", f"{self.salt_loop.split_fraction:.3f}", "—"),
            ("热罐 / 冷罐温度",
             f"{self.salt_loop.t_hot_c:.1f} / {self.salt_loop.t_cold_c:.2f}", "°C"),
            ("新增辅机（氦风机 + 盐泵）",
             f"{self.aux_circulator_mw + self.aux_salt_pump_mw:.2f}", "MW"),
            ("C2N 净电", f"{self.net_power_mw:.2f}", "MW"),
            ("煤电净电（同口径）", f"{self.coal_net_power_mw:.2f}", "MW"),
            ("净电变化", f"{self.net_power_delta_mw:+.2f}", "MW"),
            ("汽机侧净循环效率（净电 / 汽侧吸热）",
             f"{self.eta_cycle_pct:.2f}", "%"),
        ]


def efficiency_band(p_main_bar: float) -> tuple[float, float]:
    """毛电 / 汽侧吸热的合理区间，按主蒸汽压力等级分。

    实现已上移到 :func:`c2n.config.efficiency_band`（校验层与求解层共用一份），
    这里保留同名转发，避免旧调用方与测试失效。
    """

    return _efficiency_band(p_main_bar)


def run_design(cfg: PlantConfig, warnings: list[str] | None = None) -> DesignResult:
    """执行一次完整评估。

    入参可以是"用户写的"配置（``turbine_class`` / ``fluid`` 还是 ``auto``）也可以
    是已解析的配置：内部先调 :func:`c2n.config.finalize`，它是幂等的。这样
    "忘了先 resolve"不会变成一个隐蔽的崩溃点——这是合并两个内核时踩到过的坑。
    """

    cfg = finalize(cfg)

    if warnings is None:
        # 解析阶段的假设（cfg.notes）+ 一致性/可行性提示，业主都要看到
        soft = list(cfg.notes) + validate(cfg)
    else:
        soft = list(warnings)

    steam = evaluate_steam_side(cfg)
    profiles = build_steam_profiles(steam)
    reactor = reactor_design(cfg)
    salt_loop = solve_salt_loop(cfg, steam, profiles, reactor.q_ihx_mw)

    q_net = reactor.q_ihx_mw - steam.q_steam_mw
    storage = size_storage(cfg, salt_loop, q_net)

    ihx = ihx_section(
        reactor.q_ihx_mw,
        reactor.t_hot_c,
        reactor.t_ihx_out_c,
        reactor.p_core_out_bar,
        reactor.p_ihx_out_bar,
        salt_loop.t_cold_c,
        salt_loop.t_hot_c,
    )
    ihx = with_selection(ihx, cfg.salt.ua_margin, cfg.salt.u_he_salt_w_m2k)

    sizing = build_sizing(
        cfg,
        section_areas={
            name: sec.area_m2 for name, sec in salt_loop.sections.items()
        },
        ihx_area_m2=ihx.area_m2,
        salt_active_t=storage.mass_t,
        salt_hot_density=salt_density(salt_loop.t_hot_c),
        salt_cold_density=salt_density(salt_loop.t_cold_c),
    )

    rho = salt_density(0.5 * (salt_loop.t_hot_c + salt_loop.t_cold_c))
    aux_pump = pump_power_mw(
        salt_loop.m_charge_kgs + salt_loop.m_discharge_kgs,
        cfg.salt.pump_dp_bar,
        cfg.salt.eta_pump,
        rho,
        cfg.salt.eta_pump_motor,
    )
    aux_circ = reactor.p_circulator_electric_mw

    gross = cfg.plant.gross_electric_mw
    turbine_aux = cfg.plant.turbine_island_aux_mw
    boiler_aux = cfg.plant.boiler_aux_mw
    net = gross - turbine_aux - aux_circ - aux_pump
    coal_net = gross - turbine_aux - boiler_aux

    # 汽机侧净循环效率 = 净电 / 四台换热器吸热（单一效率口径）
    # 注意：储罐吞吐与堆芯热功率都不进入分母，所以它不是一次能源效率。
    sustainable = 100.0 * reactor.q_ihx_mw / steam.q_steam_mw

    n_req = modules_for_self_sufficiency(cfg, steam.q_steam_mw)
    q_core_req = reactor_design(cfg, n_req).q_core_mw

    # 堆功率覆盖不住 100 % 工况时，这不是输入错误，而是"堆 + 储罐"方案本身的设计
    # 结果：它写在报告"堆功率够不够"一节与终端摘录里，不放进 warnings（那里只放
    # 需要业主补数据/改参数的问题）。

    # 面积对夹点极敏感：多给 1 K 端差能省多少面积
    try:
        loose = replace(
            cfg, salt=replace(cfg.salt, min_pinch_k=cfg.salt.min_pinch_k + 1.0)
        )
        loop_loose = solve_salt_loop(loose, steam, profiles, reactor.q_ihx_mw)
        area_plus1 = sum(sec.area_m2 for sec in loop_loose.sections.values())
    except ValueError:
        area_plus1 = float("nan")

    # 储热能不能发挥作用：堆热功率应小于汽轮机等效热功率
    if reactor.q_ihx_mw >= steam.q_steam_mw:
        soft.append(
            tr(
                "design.warn.reactor_covers",
                ihx=reactor.q_ihx_mw,
                steam=steam.q_steam_mw,
                n=max(cfg.reactor.n_modules - 1, 1),
            )
        )

    # 输入自洽性体检：毛电与四台吸热隐含的循环效率（区间按压力等级）
    implied = gross / steam.q_steam_mw
    lo, hi = efficiency_band(cfg.plant.main_steam_p_bar)
    if not lo <= implied <= hi:
        soft.append(
            tr(
                "design.warn.implied_eta",
                gross=gross,
                steam=steam.q_steam_mw,
                eta=100.0 * implied,
                p=cfg.plant.main_steam_p_bar,
                lo=100 * lo,
                hi=100 * hi,
            )
        )
    if storage.mass_t > 30_000.0:
        soft.append(
            tr(
                "design.warn.storage_mass",
                salt=cfg.salt.spec.display_short,
                mass=storage.mass_t,
                cap=storage.capacity_mwh,
            )
        )

    part_load = run_part_load(
        cfg,
        steam_rated=steam,
        gross_rated_mw=gross,
        q_ihx_mw=reactor.q_ihx_mw,
        q_core_mw=reactor.q_core_mw,
        aux_circulator_mw=aux_circ,
        aux_salt_pump_mw=aux_pump,
        aux_turbine_mw=turbine_aux,
        m_salt_charge_kgs=salt_loop.m_charge_kgs,
        m_salt_discharge_kgs=salt_loop.m_discharge_kgs,
        t_cold_c=salt_loop.t_cold_c,
        split_fraction=salt_loop.split_fraction,
        capacity_mwh=storage.capacity_mwh,
    )

    checks = _checks(cfg, steam, reactor, salt_loop, ihx, q_net, net)

    return DesignResult(
        cfg=cfg,
        steam=steam,
        reactor=reactor,
        salt_loop=salt_loop,
        storage=storage,
        ihx=ihx,
        sizing=sizing,
        q_net_mw=q_net,
        mode=storage.mode,
        gross_electric_mw=gross,
        aux_circulator_mw=aux_circ,
        aux_salt_pump_mw=aux_pump,
        aux_turbine_island_mw=turbine_aux,
        boiler_aux_mw=boiler_aux,
        net_power_mw=net,
        coal_net_power_mw=coal_net,
        eta_cycle_pct=100.0 * net / steam.q_steam_mw,
        sustainable_load_pct=sustainable,
        modules_self_sufficient=n_req,
        q_core_self_sufficient_mw=q_core_req,
        area_pinch_plus1_m2=area_plus1,
        part_load=part_load,
        checks=checks,
        warnings=soft,
    )


def _checks(
    cfg: PlantConfig,
    steam: SteamSide,
    reactor: ReactorDesign,
    loop: SaltLoop,
    ihx: Section,
    q_net: float,
    net_power_mw: float,
) -> list[Check]:
    checks: list[Check] = []
    freeze = freezing_point_c(cfg)
    spec = cfg.salt.spec
    dt_min_all = min(sec.dt_min_k for sec in list(loop.sections.values()) + [ihx])
    closure = abs(reactor.q_ihx_mw - steam.q_steam_mw - q_net)

    checks.append(
        Check(
            tr("design.check.pinch.name"),
            tr("design.check.pinch.value", v=loop.min_pinch_k, binding=section_label(
                loop.binding, supercritical=steam.supercritical)),
            tr("design.check.pinch.criterion", v=cfg.salt.min_pinch_k),
            loop.min_pinch_k >= cfg.salt.min_pinch_k - 0.02,
            id="pinch",
            value_num=loop.min_pinch_k,
            limit_num=cfg.salt.min_pinch_k,
            unit="K",
        )
    )
    checks.append(
        Check(
            tr("design.check.freeze.name"),
            tr("design.check.freeze.value", v=loop.t_cold_c - freeze),
            tr(
                "design.check.freeze.criterion",
                v=cfg.salt.freezing_margin_k,
                t=freeze + cfg.salt.freezing_margin_k,
            ),
            loop.t_cold_c - freeze >= cfg.salt.freezing_margin_k - 1e-6,
            id="freeze_margin",
            value_num=loop.t_cold_c - freeze,
            limit_num=cfg.salt.freezing_margin_k,
            unit="K",
        )
    )
    checks.append(
        Check(
            tr("design.check.decompose.name"),
            tr("design.check.decompose.value", v=loop.t_hot_c),
            tr(
                "design.check.decompose.criterion",
                v=spec.t_decompose_c,
                salt=spec.display_short,
            ),
            loop.t_hot_c <= spec.t_decompose_c,
            id="salt_decomposition",
            value_num=loop.t_hot_c,
            limit_num=spec.t_decompose_c,
            unit="°C",
        )
    )
    checks.append(
        Check(
            tr("design.check.ihx.name"),
            tr("design.check.ihx.value", v=ihx.dt_cold_end_k),
            tr("design.check.ihx.criterion", v=cfg.salt.min_pinch_k),
            ihx.dt_cold_end_k >= cfg.salt.min_pinch_k - 1e-6,
            id="ihx_cold_end",
            value_num=ihx.dt_cold_end_k,
            limit_num=cfg.salt.min_pinch_k,
            unit="K",
        )
    )
    finite = all(sec.feasible for sec in list(loop.sections.values()) + [ihx])
    checks.append(
        Check(
            tr("design.check.crossing.name"),
            tr("design.check.crossing.value_ok")
            if finite
            else tr("design.check.crossing.value_bad"),
            tr("design.check.crossing.criterion"),
            finite,
            id="temperature_crossing",
            value_num=dt_min_all,
            limit_num=0.0,
            unit="K",
        )
    )
    checks.append(
        Check(
            tr("design.check.closure.name"),
            tr(
                "design.check.closure.value",
                ihx=reactor.q_ihx_mw,
                steam=steam.q_steam_mw,
                net=q_net,
            ),
            tr("design.check.closure.criterion"),
            closure < 1e-6,
            id="energy_closure",
            value_num=closure,
            limit_num=1e-6,
            unit="MW",
        )
    )
    checks.append(
        Check(
            tr("design.check.net.name"),
            tr("design.check.net.value", v=net_power_mw),
            tr("design.check.net.criterion"),
            net_power_mw > 0.0,
            id="net_power_positive",
            value_num=net_power_mw,
            limit_num=0.0,
            unit="MW",
        )
    )
    # 注："堆功率能否独立带满 100 % 工况"不是可行性判据——本工具的基本方案就是
    # "堆恒定 + 储罐补差"，因此只在 warnings 与报告正文里给出可持续负荷。
    return checks
    return checks
