"""物性与传热小工具的单测（不涉及完整设计）。"""

from __future__ import annotations

import math

from c2n.properties import (
    h_sat_vap,
    h_water,
    salt_h,
    salt_t,
    t_sat,
    t_water,
)
from c2n.thermal import lmtd, pump_power_mw, section


def test_salt_enthalpy_roundtrip() -> None:
    for t_c in (250.0, 308.0, 400.0, 565.0, 600.0):
        h = salt_h(t_c)
        assert abs(salt_t(h) - t_c) < 1e-6


def test_salt_enthalpy_monotonic() -> None:
    values = [salt_h(t) for t in (240.0, 350.0, 450.0, 560.0)]
    assert all(b > a for a, b in zip(values, values[1:]))


def test_water_saturation() -> None:
    # 185 bar 的饱和温度约 359.3 °C
    assert abs(t_sat(185.0) - 359.26) < 0.1
    # 饱和蒸汽焓高于饱和水
    assert h_sat_vap(185.0) > h_water(185.0, 359.26 - 5.0)


def test_water_enthalpy_roundtrip() -> None:
    h = h_water(166.7, 538.0)
    assert abs(t_water(166.7, h) - 538.0) < 1e-6


def test_lmtd_limits() -> None:
    assert abs(lmtd(10.0, 10.0) - 10.0) < 1e-9
    assert abs(lmtd(20.0, 10.0) - lmtd(10.0, 20.0)) < 1e-9
    assert math.isnan(lmtd(-1.0, 10.0))
    assert not math.isfinite(section("bad", 10.0, 300, 200, 250, 260).ka_mw_k)


def test_section_ka_definition() -> None:
    sec = section("sh", 100.0, 565.0, 460.0, 340.0, 538.0)
    assert abs(sec.ka_mw_k - 100.0 / sec.lmtd_k) < 1e-9
    assert sec.dt_hot_end_k == 27.0
    assert sec.feasible


def test_pump_power() -> None:
    # 5886 kg/s, 2 bar, η=0.8, ρ=1812 -> 约 0.81 MW
    p = pump_power_mw(5886.0, 2.0, 0.80, 1812.0)
    assert 0.79 < p < 0.83
    assert pump_power_mw(0.0, 2.0, 0.8, 1800.0) == 0.0
