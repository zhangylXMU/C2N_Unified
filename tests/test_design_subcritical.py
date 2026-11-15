"""配置校验与几种典型方案的定性检查。"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

from c2n.config import (
    DEFAULT_CONFIG,
    ConfigError,
    PlantConfig,
    ReactorConfig,
    SaltConfig,
    StorageConfig,
    finalize,
    load_config,
    validate,
)
from c2n.design import run_design


def test_default_toml_matches_code_defaults() -> None:
    path = Path(__file__).resolve().parents[1] / "config" / "subcritical_600mw.toml"
    assert path.exists()
    # 统一内核的默认算例就是 600 MW 亚临界；比较前先 resolve（auto -> subcritical）
    assert load_config(path) == finalize(DEFAULT_CONFIG), (
        "config/subcritical_600mw.toml 与 dataclass 默认值不一致"
    )


def test_default_design_is_feasible() -> None:
    result = run_design(DEFAULT_CONFIG)
    assert result.feasible
    # 4 个模块（1000 MWth）不足以覆盖 1266 MW 的汽轮机热负荷 -> 储罐放热
    assert result.q_net_mw < 0.0
    assert result.mode == "放热"
    assert result.storage.capacity_mwh > 0.0
    assert result.net_power_mw > 0.0


def test_bigger_reactor_charges_the_store() -> None:
    cfg = replace(DEFAULT_CONFIG, reactor=ReactorConfig(n_modules=7))
    result = run_design(cfg)
    assert result.q_net_mw > 0.0
    assert result.mode == "充热"


def test_storage_hours_scale_capacity() -> None:
    a = run_design(replace(DEFAULT_CONFIG, storage=StorageConfig(hours=2.0)))
    b = run_design(replace(DEFAULT_CONFIG, storage=StorageConfig(hours=4.0)))
    assert abs(b.storage.capacity_mwh - 2.0 * a.storage.capacity_mwh) < 1e-6
    assert abs(b.storage.mass_t - 2.0 * a.storage.mass_t) < 1e-6


def test_tighter_pinch_needs_more_area() -> None:
    loose = run_design(replace(DEFAULT_CONFIG, salt=SaltConfig(min_pinch_k=15.0)))
    tight = run_design(replace(DEFAULT_CONFIG, salt=SaltConfig(min_pinch_k=5.0)))
    assert tight.salt_loop.min_pinch_k < loose.salt_loop.min_pinch_k
    for name in ("过热器", "蒸发器", "省煤器", "再热器"):
        assert tight.salt_loop.sections[name].ka_mw_k >= loose.salt_loop.sections[name].ka_mw_k


def test_validation_rejects_main_steam_above_salt() -> None:
    bad = replace(
        DEFAULT_CONFIG,
        plant=replace(DEFAULT_CONFIG.plant, main_steam_t_c=600.0),
    )
    try:
        validate(bad)
    except ConfigError:
        return
    raise AssertionError("主蒸汽温度高于热盐温度时必须报错")


def test_validation_rejects_impossible_pinch() -> None:
    """要求 30 K 端差时，主蒸汽/热盐温差只有 27 K，应当报错并给出可达值。"""

    cfg = replace(DEFAULT_CONFIG, salt=SaltConfig(min_pinch_k=30.0))
    try:
        run_design(cfg)
    except ValueError as exc:
        assert "端差" in str(exc)
        return
    raise AssertionError("不可达的端差应当抛出 ValueError")


def test_unknown_key_is_rejected() -> None:
    from c2n.config import apply_overrides

    try:
        apply_overrides(DEFAULT_CONFIG, {"reactor": {"bogus": 1}})
    except ConfigError as exc:
        assert "bogus" in str(exc)
        return
    raise AssertionError("未知参数名应当报错，避免拼写错误被静默忽略")

def test_supercritical_is_rejected() -> None:
    """超临界没有汽包，蒸发段分界不成立，必须给出中文拒绝信息。"""

    bad = replace(
        DEFAULT_CONFIG,
        plant=replace(DEFAULT_CONFIG.plant, drum_p_bar=255.0),
    )
    try:
        validate(bad)
    except ConfigError as exc:
        assert "超临界" in str(exc)
        return
    raise AssertionError("超临界输入必须被拒绝")


def test_gross_basis_is_consistent() -> None:
    """generator 口径不乘效率；shaft 口径乘效率；两者可互相折算。"""

    gen = replace(
        DEFAULT_CONFIG,
        plant=replace(DEFAULT_CONFIG.plant, gross_basis="generator",
                      gross_power_mw=589.84, turbine_island_aux_mw=0.0),
    )
    shaft = replace(
        gen,
        plant=replace(gen.plant, gross_basis="shaft", gross_power_mw=600.008),
    )
    a = run_design(gen)
    b = run_design(shaft)
    assert abs(a.gross_electric_mw - 589.84) < 1e-9
    assert abs(b.gross_electric_mw - 600.008 * 0.988 * 0.995) < 0.05
    assert abs(a.net_power_mw - b.net_power_mw) < 0.05


def test_boiler_aux_credit_appears_in_delta() -> None:
    """净电变化 = 锅炉辅机 − 氦风机 − 盐泵，与汽轮机岛辅机无关。"""

    base = run_design(
        replace(DEFAULT_CONFIG, plant=replace(DEFAULT_CONFIG.plant,
                                              boiler_aux_mw=0.0))
    )
    with_credit = run_design(
        replace(DEFAULT_CONFIG, plant=replace(DEFAULT_CONFIG.plant,
                                              boiler_aux_mw=12.0))
    )
    assert abs(
        (with_credit.net_power_delta_mw - base.net_power_delta_mw) - 12.0
    ) < 1e-9
    expected = 12.0 - base.aux_circulator_mw - base.aux_salt_pump_mw
    assert abs(with_credit.net_power_delta_mw - expected) < 1e-6


def test_area_and_selection() -> None:
    """选型 kA = 所需 kA × 裕量；面积 = 选型 kA / U。"""

    base = run_design(DEFAULT_CONFIG)
    margin = run_design(
        replace(DEFAULT_CONFIG, salt=replace(DEFAULT_CONFIG.salt, ua_margin=1.1))
    )
    for name, sec in base.salt_loop.sections.items():
        assert sec.area_m2 > 0.0
        assert abs(sec.ka_selected_mw_k - sec.ka_mw_k) < 1e-9
        assert abs(
            margin.salt_loop.sections[name].ka_selected_mw_k
            - 1.1 * sec.ka_mw_k
        ) < 1e-6
        # 裕量不改变"所需 kA"
        assert abs(margin.salt_loop.sections[name].ka_mw_k - sec.ka_mw_k) < 1e-9


def test_path_pinch_is_the_constraint() -> None:
    """反解约束是沿程最小温差：四台都恰好等于设计值。"""

    d = run_design(DEFAULT_CONFIG)
    target = DEFAULT_CONFIG.salt.min_pinch_k
    assert d.salt_loop.min_pinch_k >= target - 0.02
    assert d.salt_loop.min_pinch_k <= target + 0.05
    # 沿程最小温差 ≤ 两端最小温差
    for name, sec in d.salt_loop.sections.items():
        assert sec.dt_min_k <= min(sec.dt_hot_end_k, sec.dt_cold_end_k) + 1e-6, name


def test_split_is_optimised() -> None:
    """默认反解支路份额，且落在合理区间。"""

    d = run_design(DEFAULT_CONFIG)
    assert DEFAULT_CONFIG.salt.optimise_split is True
    assert 0.78 < d.salt_loop.split_fraction < 0.86
    fixed = run_design(
        replace(DEFAULT_CONFIG, salt=replace(DEFAULT_CONFIG.salt,
                                             optimise_split=False))
    )
    assert abs(fixed.salt_loop.split_fraction - 0.825) < 1e-9

def test_reported_end_temperatures_are_true_ends() -> None:
    """报告里的"端差 热/冷"必须是真端点，而不是分段中点（P1-A）。"""

    d = run_design(DEFAULT_CONFIG)
    cfg, steam, loop = d.cfg, d.steam, d.salt_loop
    expected = {
        "过热器": (cfg.salt.t_hot_c - cfg.plant.main_steam_t_c,
                  loop.t_sh_out_c - steam.t_sat_c),
        "蒸发器": (loop.t_sh_out_c - steam.t_sat_c,
                  loop.t_ev_out_c - steam.t_econ_out_c),
        "省煤器": (loop.t_ev_out_c - steam.t_econ_out_c,
                  loop.t_econ_out_c - cfg.plant.feedwater_t_c),
        "再热器": (cfg.salt.t_hot_c - cfg.plant.hot_reheat_t_c,
                  loop.t_rh_out_c - cfg.plant.cold_reheat_t_c),
    }
    for name, (hot, cold) in expected.items():
        sec = loop.sections[name]
        assert abs(sec.dt_hot_end_k - hot) < 1e-9, name
        assert abs(sec.dt_cold_end_k - cold) < 1e-9, name
        # 真实最小温差不可能大于任一端
        assert sec.dt_min_k <= min(hot, cold) + 1e-9, name


def test_zero_subcooling_is_rejected() -> None:
    """过冷度为 0 会让省煤器出口落在饱和线上，必须提前拒绝（P1-B）。"""

    bad = replace(
        DEFAULT_CONFIG,
        plant=replace(DEFAULT_CONFIG.plant, economizer_subcooling_k=0.0),
    )
    try:
        validate(bad)
    except ConfigError as exc:
        assert "过冷度" in str(exc)
        return
    raise AssertionError("过冷度 = 0 必须被拒绝")


def test_input_consistency_is_flagged() -> None:
    """毛电与热负荷隐含的循环效率越界时应告警。"""

    d = run_design(
        replace(DEFAULT_CONFIG,
                plant=replace(DEFAULT_CONFIG.plant, gross_power_mw=700.0))
    )
    assert any("不自洽" in w for w in d.warnings)


def test_area_sensitivity_is_reported() -> None:
    """夹点 +1 K 的面积要一并给出（面积对夹点极敏感）。"""

    d = run_design(DEFAULT_CONFIG)
    total = sum(sec.area_m2 for sec in d.salt_loop.sections.values())
    assert d.area_pinch_plus1_m2 > 0.0
    assert d.area_pinch_plus1_m2 < total  # 放宽端差 -> 面积更小

def test_sizing_answers_the_two_questions() -> None:
    """面积/外形 与 盐量/罐尺寸 必须自洽。"""

    d = run_design(DEFAULT_CONFIG)
    s = d.sizing
    assert s.area_total_m2 > 0.0
    for name, bundle in s.exchangers.items():
        assert bundle.n_tubes > 0, name
        assert bundle.n_shells >= 1, name
        assert bundle.shell_id_m <= DEFAULT_CONFIG.sizing.max_shell_id_m + 1e-9, name
        # 面积 = 管数 × π d L（四舍五入后 2 % 以内）
        area_check = (
            bundle.n_tubes * math.pi * 0.025 * bundle.tube_length_m
        )
        assert abs(area_check - bundle.area_m2) / bundle.area_m2 < 0.02, name

    # 储罐：容积 = 活动体积 /(1 - 气相比例)，且与 D、H 自洽
    for tank in (s.hot_tank, s.cold_tank):
        active = tank.mass_t * 1000.0 / tank.density_kg_m3
        assert abs(tank.active_volume_m3 - active) < 1e-6
        assert abs(tank.tank_volume_m3 - active / 0.88) < 1e-6
        volume = math.pi / 4.0 * tank.diameter_m**2 * tank.height_m
        assert abs(volume - tank.tank_volume_m3) / volume < 1e-9
    # 采购盐量 = 活动盐量 × (1 + 滞留比例)
    assert abs(s.salt_total_t - 1.10 * s.salt_active_t) < 1e-9


def test_shell_split_respects_the_diameter_limit() -> None:
    """壳体上限收紧时，必须自动并联更多台。"""

    tight = run_design(
        replace(DEFAULT_CONFIG,
                sizing=replace(DEFAULT_CONFIG.sizing, max_shell_id_m=2.6))
    )
    for bundle in tight.sizing.exchangers.values():
        assert bundle.shell_id_m <= 2.6 + 1e-9
    assert tight.sizing.shell_count_total > run_design(
        DEFAULT_CONFIG
    ).sizing.shell_count_total

def test_part_load_reproduces_the_rated_point() -> None:
    """100 % 那一行必须回到额定结果（同一套公式、同一组参数）。"""

    d = run_design(DEFAULT_CONFIG)
    top = d.part_load[0]
    assert abs(top.load - 1.0) < 1e-9
    assert abs(top.gross_mw - d.gross_electric_mw) < 0.2
    assert abs(top.net_mw - d.net_power_mw) < 0.3
    assert abs(top.q_steam_mw - d.steam.q_steam_mw) < 0.5
    assert abs(top.eta_cycle_pct - d.eta_cycle_pct) < 0.1
    assert abs(top.m_main_kgs - d.cfg.plant.main_steam_m_kgs) < 1.0


def test_part_load_is_monotone_and_flips_the_store() -> None:
    """负荷降低：毛电/净电单调下降；储罐从放热翻到充热。"""

    d = run_design(DEFAULT_CONFIG)
    gross = [pt.gross_mw for pt in d.part_load]
    net = [pt.net_mw for pt in d.part_load]
    assert gross == sorted(gross, reverse=True)
    assert net == sorted(net, reverse=True)
    assert d.part_load[0].q_net_mw < 0.0     # 100 %：放热
    assert d.part_load[-1].q_net_mw > 0.0    # 30 %：充热
    # 平衡点落在 70~90 % 之间
    balance = [pt.load for pt in d.part_load if abs(pt.q_net_mw) < 5.0]
    assert balance and 0.7 <= balance[0] <= 0.9


def test_feedwater_estimate_when_the_table_is_empty() -> None:
    """清空给水温度表 -> 走"抽汽压力缩放"估算，且随负荷单调下降。"""

    cfg = replace(
        DEFAULT_CONFIG,
        partload=replace(DEFAULT_CONFIG.partload, feedwater_t_c=()),
    )
    d = run_design(cfg)
    assert d.part_load[0].t_fw_source == "估算"
    assert abs(d.part_load[0].t_fw_c - cfg.plant.feedwater_t_c) < 0.2
    temps = [pt.t_fw_c for pt in d.part_load]
    assert temps == sorted(temps, reverse=True)


def test_partload_config_is_validated() -> None:
    """负荷点与效率修正必须等长、负荷点必须递减。"""

    bad = replace(
        DEFAULT_CONFIG,
        partload=replace(DEFAULT_CONFIG.partload, eta_ratio=(1.0, 0.9)),
    )
    try:
        validate(bad)
    except ConfigError as exc:
        assert "eta_ratio" in str(exc)
        return
    raise AssertionError("长度不一致时必须报错")


def test_feedwater_table_is_anchored_to_the_rated_value() -> None:
    """给水温度表只提供形状，绝对水平必须锚定业主的额定值（集团反馈 P0）。"""

    cfg = replace(
        DEFAULT_CONFIG,
        plant=replace(DEFAULT_CONFIG.plant, feedwater_t_c=239.0),
    )
    d = run_design(cfg)
    top = d.part_load[0]
    assert abs(top.t_fw_c - 239.0) < 0.1
    assert "平移" in top.t_fw_source
    # 100 % 行必须回到业主给的流量与压力
    assert abs(top.m_main_kgs - cfg.plant.main_steam_m_kgs) < 1.0
    assert abs(top.p_main_bar - cfg.plant.main_steam_p_bar) < 0.2


def test_low_pressure_units_do_not_trip_the_consistency_warning() -> None:
    """13 MPa 级超高压机组毛效率 ~39 %，不应报"输入不自洽"（集团反馈 P1）。"""

    from c2n.design import efficiency_band

    lo, hi = efficiency_band(132.4)
    assert lo <= 0.39 <= hi
    lo, hi = efficiency_band(166.7)
    assert lo <= 0.46 <= hi
    assert efficiency_band(132.4)[0] < efficiency_band(166.7)[0]
