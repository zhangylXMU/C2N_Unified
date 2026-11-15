"""与完整 TESPy 模型（C2N_HTGR_Salt_SCPP）的逐项对照。

参照值取自 `../C2N_HTGR_Salt_SCPP/results/report.md` 与 `summary.json` 的
100 % 工况；对应的边界条件写在 `config/reference_supercritical_600mw.toml` 里（注释逐项说明
了记账口径的对齐方式）。

这张对照说明的是：**两个独立实现（TESPy 网络 vs 解析+沿程积分）在同一组
边界条件下给出的热负荷、盐温、kA、净电一致**。差异只出现在"怎么记账"上，
不在物理上。
"""

from __future__ import annotations

from pathlib import Path

from c2n.config import load_config
from c2n.design import run_design

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "config" / "reference_supercritical_600mw.toml"

#: C2N_HTGR_Salt_SCPP 100 % 工况（report.md / summary.json）
REF_Q = {"省煤器": 200.9, "蒸发器": 478.5, "过热器": 299.1, "再热器": 234.7}
REF_KA = {"过热器": 3.295, "蒸发器": 9.883, "省煤器": 7.996, "再热器": 9.066}
REF_IHX_KA = 15.845
REF_T_COLD = 312.647
REF_M_DISCHARGE = 2591.436
REF_SALT_OUT = {"过热器": 526.89, "蒸发器": 375.85, "省煤器": 311.65, "再热器": 316.72}
REF_NET = 543.925
REF_ETA = 44.83
REF_Q_STEAM = 1213.212
REF_Q_IHX = 1029.956


def _design():
    return run_design(load_config(CARD))


def test_duties_match_the_tespy_reference() -> None:
    """四段热负荷：解析式与 TESPy 网络应几乎逐位一致（< 0.3 MW）。"""

    d = _design()
    for name, expected in REF_Q.items():
        assert abs(d.salt_loop.sections[name].q_mw - expected) < 0.3, name
    assert abs(d.steam.q_steam_mw - REF_Q_STEAM) < 0.3
    assert abs(d.reactor.q_ihx_mw - REF_Q_IHX) < 0.3


def test_salt_side_matches() -> None:
    """冷罐温度、放热盐流量、四段盐出口温度。"""

    d = _design()
    loop = d.salt_loop
    assert abs(loop.t_cold_c - REF_T_COLD) < 0.1
    assert abs(loop.m_discharge_kgs - REF_M_DISCHARGE) < 2.0
    for name, expected in REF_SALT_OUT.items():
        actual = {
            "过热器": loop.t_sh_out_c,
            "蒸发器": loop.t_ev_out_c,
            "省煤器": loop.t_econ_out_c,
            "再热器": loop.t_rh_out_c,
        }[name]
        assert abs(actual - expected) < 0.15, name


def test_kA_matches() -> None:
    """沿程积分 kA：两个实现都用 60 段积分，应差 < 0.5 %。"""

    d = _design()
    for name, expected in REF_KA.items():
        actual = d.salt_loop.sections[name].ka_mw_k
        assert abs(actual - expected) / expected < 0.005, name
    assert abs(d.ihx.ka_mw_k - REF_IHX_KA) / REF_IHX_KA < 0.005


def test_net_power_and_efficiency_match() -> None:
    """净电与效率：把记账口径对齐（轴功口径 + 汽轮机岛辅机 15.30 MW）。"""

    d = _design()
    assert abs(d.net_power_mw - REF_NET) < 0.5
    assert abs(d.eta_cycle_pct - REF_ETA) < 0.05


def test_bottleneck_is_the_reheater_cold_end() -> None:
    """参考模型的瓶颈是再热器冷端 5.15 K，本工具应当复现。"""

    d = _design()
    assert abs(d.salt_loop.min_pinch_k - 5.15) < 0.02
    assert d.salt_loop.binding == "再热器"
