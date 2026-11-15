"""与 C2N_HTGR_Salt 的 100 % 工况逐项对照（关键回归测试）。

参考值取自 `C2N_HTGR_Salt`（完整 TESPy 模型，含 8 级抽汽汽轮机）在
100 % 工况的输出：`results/load_cases.csv` 与 `results/system_topology.csv`。
边界条件也取自同一处，因此这里检验的是：

    把汽轮机当成黑盒、只保留四台换热器，会不会改变锅炉侧的设计结论？

结论是：不会。热负荷、冷盐温度、四台换热器的 kA、净电功率都在 1e-3 相对
误差内复现（差别只来自盐焓拟合的截断）。
"""

from __future__ import annotations

from dataclasses import replace

from c2n.config import CoalPlantConfig, PlantConfig, SaltConfig
from c2n.design import run_design

#: 参考边界（C2N_HTGR_Salt, 100 % 工况）
#: 参考模型的 600 MW 是**轴功率**（各汽轮机功率之和），厂用电只含给水/凝结水泵，
#: 且汽轮机岛没有锅炉辅机；支路份额固定 0.825，端差按端点法定 5.5496 K。
CFG = PlantConfig(
    plant=replace(
        CoalPlantConfig(),
        gross_power_mw=600.008,
        gross_basis="shaft",
        turbine_island_aux_mw=12.055,
        # 参考模型取给水 185 bar、无煤粉炉辅机（它没有锅炉），这里显式对齐
        boiler_aux_mw=0.0,
        feedwater_p_bar=185.0,
    ),
    salt=replace(
        SaltConfig(),
        min_pinch_k=5.5496,
        optimise_split=False,
        main_branch_fraction=0.825,
    ),
)


def _design():
    return run_design(CFG)


def test_steam_side_duties_match_reference() -> None:
    d = _design()
    assert abs(d.steam.q_superheater_mw - 436.739) < 0.02
    assert abs(d.steam.q_evaporator_mw - 380.954) < 0.02
    assert abs(d.steam.q_economizer_mw - 237.595) < 0.02
    assert abs(d.steam.q_reheater_mw - 210.988) < 0.02
    assert abs(d.steam.q_steam_mw - 1266.276) < 0.05


def test_reactor_matches_reference() -> None:
    d = _design()
    assert abs(d.reactor.q_ihx_mw - 1029.956) < 0.02
    assert abs(d.reactor.p_circulator_mw - 29.956) < 0.01
    assert abs(d.reactor.m_helium_kgs - 483.979) < 0.05
    assert abs(d.reactor.t_core_in_c - 351.76) < 0.05
    # IHX = 堆芯 + 风机
    assert abs(d.reactor.q_ihx_mw - d.reactor.q_core_mw - d.reactor.p_circulator_mw) < 1e-9


def test_salt_loop_matches_reference() -> None:
    d = _design()
    # 约束同时含沿程中点与真实端点，因此与参考模型（端点法）完全一致
    assert abs(d.salt_loop.t_cold_c - 308.007) < 0.02
    assert abs(d.salt_loop.m_discharge_kgs - 3245.737) < 0.6
    assert abs(d.salt_loop.m_charge_kgs - 2640.0) < 0.6
    assert abs(d.salt_loop.t_sh_out_c - 458.469) < 0.05
    assert abs(d.salt_loop.t_ev_out_c - 364.488) < 0.05
    assert abs(d.salt_loop.t_econ_out_c - 305.358) < 0.05
    assert abs(d.salt_loop.t_rh_out_c - 320.486) < 0.05


def test_section_ka_uses_path_integration() -> None:
    """kA 用沿程积分；`C2N_HTGR_Salt` 已同步移植，两边逐台一致。

    旧端点 LMTD 在同一个设计点上给 7.871 / 11.008 / 12.500 / 15.562，
    沿程积分给 5.562 / 11.029 / 16.266 / 12.632（IHX 11.892）。
    端点法的单台误差 −19 %~+41 % 全部来自蒸汽在饱和线附近的比热剧变。
    """

    d = _design()
    expected = {
        "过热器": 5.562,
        "蒸发器": 11.029,
        "省煤器": 16.266,
        "再热器": 12.632,
    }
    for name, ka in expected.items():
        assert abs(d.salt_loop.sections[name].ka_mw_k - ka) < 0.05, name
    assert abs(d.ihx.ka_mw_k - 11.892) < 0.05
    # 端点法的旧值应当明显不同（过热器高估 > 30 %）
    endpoint = {"过热器": 7.871, "蒸发器": 11.008, "省煤器": 12.500, "再热器": 15.562}
    assert d.salt_loop.sections["过热器"].ka_mw_k < 0.8 * endpoint["过热器"]
    assert d.salt_loop.sections["省煤器"].ka_mw_k > 1.2 * endpoint["省煤器"]


def test_balance_and_net_power_match_reference() -> None:
    d = _design()
    assert abs(d.q_net_mw - (-236.319)) < 0.05
    assert abs(d.reactor.q_ihx_mw - d.steam.q_steam_mw - d.q_net_mw) < 1e-9
    # 参考模型的毛电是轴功率，这里按 gross_basis="shaft" 折算；
    # 差别 1 MW 来自氦风机的电机效率（参考模型直接扣轴功）。
    assert abs(d.net_power_mw - 546.069) < 0.1
    assert abs(d.eta_cycle_pct - 43.120) < 0.01
    assert d.feasible


def test_self_sufficiency_uses_five_modules() -> None:
    d = _design()
    assert d.modules_self_sufficient == 5
    assert abs(d.q_core_self_sufficient_mw - 1250.0) < 1e-6


def test_state_points_match_reference() -> None:
    """状态点（拓扑图上的节点）与完整模型的汽水侧焓值一致。"""

    from c2n.states import build_states

    d = _design()
    states = {sp.id: sp for sp in build_states(d)}
    assert len(states) == 17
    expected_h = {
        "W1": 1199.972,   # 给水
        "W2": 1695.051,   # 省煤器出口
        "W3": 2488.848,   # 蒸发器出口（饱和蒸汽）
        "W4": 3398.887,   # 主蒸汽
        "W5": 3014.614,   # 冷再热
        "W6": 3540.716,   # 热再热
    }
    for node, h in expected_h.items():
        assert abs(states[node].h_kj_kg - h) < 0.2, node
    # 盐侧：热罐 565 °C，冷罐温度由反解得到
    assert abs(states["S1"].t_c - 565.0) < 1e-9
    assert abs(states["S7"].t_c - states["S8"].t_c) < 1e-9
    # 工质标注齐全
    assert states["H1"].fluid == "氦气"
    assert states["S3"].fluid == "太阳盐"


def test_salt_loop_is_closed() -> None:
    """闭合回路的两条流线必须质量守恒。"""

    d = _design()
    # 充热回路：IHX 出口与冷罐出口是同一股盐流
    assert abs(d.salt_loop.m_charge_kgs - 2640.0) < 1.5
    # 放热回路：热盐总管 = 冷盐总管
    assert abs(d.salt_loop.m_discharge_kgs - 3245.7) < 1.0
    # 主支路 + 再热支路 = 放热总流量
    total = d.salt_loop.m_main_kgs + d.salt_loop.m_reheat_kgs
    assert abs(total - d.salt_loop.m_discharge_kgs) < 1e-6

def test_duties_against_if97_independent() -> None:
    """用 CoolProp 的 IF97 后端独立复算四段热负荷（模型默认用 IAPWS-95）。

    两个后端的焓值差 ~0.01 %，所以这项检查能抓住真正的公式错误，
    而不是物性实现差异。审阅报告的独立复算也走这条路。
    """

    import CoolProp.CoolProp as CP

    from c2n.steam_side import evaluate_steam_side

    cfg = CFG.plant

    def h(p_bar: float, t_c: float) -> float:
        return CP.PropsSI("H", "P", p_bar * 1e5, "T", t_c + 273.15,
                          "IF97::Water") / 1000.0

    def hq(p_bar: float, x: float) -> float:
        return CP.PropsSI("H", "P", p_bar * 1e5, "Q", x, "IF97::Water") / 1000.0

    t_sat = CP.PropsSI("T", "P", cfg.drum_p_bar * 1e5, "Q", 0,
                       "IF97::Water") - 273.15
    t_econ = t_sat - cfg.economizer_subcooling_k

    m, m_rh = cfg.main_steam_m_kgs, cfg.reheat_m_kgs
    q_econ = m * (h(cfg.drum_p_bar, t_econ) - h(cfg.feedwater_p_bar, cfg.feedwater_t_c)) / 1000
    q_evap = m * (hq(cfg.drum_p_bar, 1.0) - h(cfg.drum_p_bar, t_econ)) / 1000
    q_sh = m * (h(cfg.main_steam_p_bar, cfg.main_steam_t_c) - hq(cfg.drum_p_bar, 1.0)) / 1000
    q_rh = m_rh * (h(cfg.hot_reheat_p_bar, cfg.hot_reheat_t_c)
                   - h(cfg.cold_reheat_p_bar, cfg.cold_reheat_t_c)) / 1000

    steam = evaluate_steam_side(CFG)
    assert abs(steam.q_economizer_mw - q_econ) < 0.4
    assert abs(steam.q_evaporator_mw - q_evap) < 0.4
    assert abs(steam.q_superheater_mw - q_sh) < 0.4
    assert abs(steam.q_reheater_mw - q_rh) < 0.4
    # 总量也应一致（偏差相互抵消后更小）
    assert abs(steam.q_steam_mw - (q_econ + q_evap + q_sh + q_rh)) < 0.5


def test_salt_energy_closure() -> None:
    """盐侧逐段 m·Δh = Q —— 闭合到 1e-6 相对误差。"""

    from c2n.properties import salt_h

    d = _design()
    loop = d.salt_loop
    pairs = [
        ("过热器", loop.t_sh_out_c, loop.t_hot_c, loop.m_main_kgs),
        ("蒸发器", loop.t_ev_out_c, loop.t_sh_out_c, loop.m_main_kgs),
        ("省煤器", loop.t_econ_out_c, loop.t_ev_out_c, loop.m_main_kgs),
        ("再热器", loop.t_rh_out_c, loop.t_hot_c, loop.m_reheat_kgs),
    ]
    for name, t_in, t_out, m in pairs:
        q = m * (salt_h(t_out) - salt_h(t_in)) / 1000.0
        assert abs(q - loop.sections[name].q_mw) < 0.01, name
