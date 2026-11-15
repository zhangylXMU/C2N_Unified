"""超临界核心物理的独立校验。

这里没有"完整 TESPy 参考模型"可对（超临界版是新写的），所以改成**逐项独立复算**：
每一段热负荷都用 CoolProp 直接算一遍，再与工具结果比；拟临界温度、能量闭合、
盐回路质量闭合各自单测。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import CoolProp.CoolProp as CP

from c2n.config import load_config
from c2n.design import run_design
from c2n.properties import pseudo_critical_t, salt_h
from c2n.steam_side import evaluate_steam_side

ROOT = Path(__file__).resolve().parents[1]

#: 超临界算例（1000 MW 超超临界，热罐 620 °C、新盐 650 °C 分解）
SC = load_config(ROOT / "config" / "supercritical_1000mw.toml")


def _h(p_bar: float, t_c: float) -> float:
    return CP.PropsSI("H", "P", p_bar * 1e5, "T", t_c + 273.15, "Water") / 1000.0


def test_duties_match_coolprop_directly() -> None:
    """四段热负荷与 CoolProp 手算逐项一致（< 0.01 MW）。"""

    cfg = SC
    p = cfg.plant
    s = evaluate_steam_side(cfg)

    m, m_rh = p.main_steam_m_kgs, p.reheat_m_kgs
    q_econ = m * (_h(p.boiler_p_bar, p.economizer_outlet_t_c)
                  - _h(p.feedwater_p_bar, p.feedwater_t_c)) / 1000.0
    q_evap = m * (_h(p.boiler_p_bar, p.wall_outlet_t_c)
                  - _h(p.boiler_p_bar, p.economizer_outlet_t_c)) / 1000.0
    q_sh = m * (_h(p.main_steam_p_bar, p.main_steam_t_c)
                - _h(p.boiler_p_bar, p.wall_outlet_t_c)) / 1000.0
    q_rh = m_rh * (_h(p.hot_reheat_p_bar, p.hot_reheat_t_c)
                   - _h(p.cold_reheat_p_bar, p.cold_reheat_t_c)) / 1000.0

    assert abs(s.q_economizer_mw - q_econ) < 0.01
    assert abs(s.q_evaporator_mw - q_evap) < 0.01
    assert abs(s.q_superheater_mw - q_sh) < 0.01
    assert abs(s.q_reheater_mw - q_rh) < 0.01
    # 蒸发段（水冷壁）应该占大头：超临界没有潜热，但 cp 在拟临界区极大
    assert s.q_evaporator_mw > s.q_superheater_mw > s.q_reheater_mw


def test_pseudo_critical_temperature() -> None:
    """265 bar 的拟临界温度应在 380~400 °C（cp 峰值位置）。"""

    t_pc = pseudo_critical_t(265.0)
    assert 380.0 <= t_pc <= 400.0
    # 省煤器出口必须低于它、水冷壁出口必须高于它
    p = SC.plant
    assert p.economizer_outlet_t_c < t_pc < p.wall_outlet_t_c


def test_energy_closure() -> None:
    """一回路与储热的能量闭合严格成立。"""

    d = run_design(SC)
    assert abs(d.reactor.q_core_mw + d.reactor.p_circulator_mw
               - d.reactor.q_ihx_mw) < 1e-9
    assert abs(d.reactor.q_ihx_mw - d.steam.q_steam_mw - d.q_net_mw) < 1e-9


def test_salt_loop_mass_closure() -> None:
    """主支路 + 再热支路 = 放热总流量；充热回路独立。"""

    d = run_design(SC)
    loop = d.salt_loop
    assert abs(loop.m_main_kgs + loop.m_reheat_kgs - loop.m_discharge_kgs) < 1e-9
    assert loop.m_charge_kgs > 0.0
    # 盐焓差与 IHX 热负荷闭合
    dh = salt_h(loop.t_hot_c) - salt_h(loop.t_cold_c)
    assert abs(loop.m_charge_kgs * dh / 1000.0 - d.reactor.q_ihx_mw) < 0.05


def test_new_salt_allows_620_but_not_660() -> None:
    """新盐 650 °C 分解：620 °C 合规，660 °C 应判不通过。"""

    ok = run_design(SC)
    assert ok.salt_loop.t_hot_c == 620.0
    assert any(c.name == "热盐分解温度" and c.ok for c in ok.checks)

    hot = run_design(
        replace(SC, salt=replace(SC.salt, t_hot_c=660.0))
    )
    failed = [c for c in hot.checks if c.name == "热盐分解温度" and not c.ok]
    assert failed, "超过 650 °C 必须判不通过"
    assert SC.salt.spec.t_decompose_c == 650.0


def test_wall_outlet_drives_the_superheater_duty() -> None:
    """水冷壁出口温度抬高 -> 蒸发段吸热增加、过热器吸热减少（总和不变）。"""

    base = run_design(SC)
    hotter = run_design(
        replace(
            SC,
            plant=replace(SC.plant, wall_outlet_t_c=470.0),
        )
    )
    assert hotter.steam.q_evaporator_mw > base.steam.q_evaporator_mw
    assert hotter.steam.q_superheater_mw < base.steam.q_superheater_mw
    assert abs(hotter.steam.q_boiler_mw - base.steam.q_boiler_mw) < 0.5
