"""配置层测试：机型判定、缺省补齐、校验分级、TOML 往返。

这些测试**不需要 CoolProp**（只碰 ``c2n.config`` / ``c2n.fluids``），所以任何
环境都能跑——它们守的是"合并两个内核时最容易出错的那一层"：输入 schema、
自动判定、以及"哪些问题该拦、哪些只该提示"。
"""

from __future__ import annotations

import tomllib
from dataclasses import replace
from pathlib import Path

from c2n.config import (
    WATER_CRITICAL_P_BAR,
    CoalPlantConfig,
    ConfigError,
    PlantConfig,
    SaltConfig,
    apply_overrides,
    dump_config,
    finalize,
    load_config,
    resolve,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]


def _cfg(**plant: object) -> PlantConfig:
    return PlantConfig(plant=replace(CoalPlantConfig(), **plant))


# ---------------------------------------------------------------------------
# 1. 机型自动判定
# ---------------------------------------------------------------------------
def test_auto_detects_subcritical_from_pressure() -> None:
    cfg, _ = resolve(_cfg(main_steam_p_bar=166.7, main_steam_t_c=538.0))
    assert cfg.plant.turbine_class == "subcritical"
    assert cfg.plant.drum_p_bar is not None
    assert cfg.plant.boiler_p_bar is None
    assert cfg.plant.economizer_subcooling_k is not None
    assert cfg.plant.wall_outlet_t_c is None


def test_auto_detects_supercritical_from_pressure() -> None:
    cfg, _ = resolve(
        _cfg(main_steam_p_bar=265.0, main_steam_t_c=600.0, drum_p_bar=None)
    )
    assert cfg.plant.turbine_class == "supercritical"
    assert cfg.plant.is_supercritical
    assert cfg.plant.boiler_p_bar is not None
    assert cfg.plant.boiler_p_bar > WATER_CRITICAL_P_BAR
    assert cfg.plant.turbine_island_aux_mw == 26.5  # 其余字段不动


def test_boiler_pressure_wins_over_main_steam_pressure() -> None:
    """显式给了锅炉压力就按超临界，哪怕主蒸汽压力写得很低（会由校验拦）。"""

    cfg, _ = resolve(
        _cfg(main_steam_p_bar=170.0, boiler_p_bar=250.0, wall_outlet_t_c=450.0,
             economizer_outlet_t_c=340.0)
    )
    assert cfg.plant.is_supercritical
    assert cfg.plant.drum_p_bar is None


def test_explicit_class_wins() -> None:
    cfg, _ = resolve(
        _cfg(turbine_class="supercritical", main_steam_p_bar=250.0,
             wall_outlet_t_c=450.0, economizer_outlet_t_c=340.0)
    )
    assert cfg.plant.turbine_class == "supercritical"

    cfg, _ = resolve(
        _cfg(turbine_class="subcritical", main_steam_p_bar=166.7, drum_p_bar=185.0)
    )
    assert cfg.plant.turbine_class == "subcritical"


def test_ultra_supercritical_is_its_own_label_but_same_strategy() -> None:
    cfg, _ = resolve(
        _cfg(turbine_class="ultra_supercritical", main_steam_p_bar=265.0,
             main_steam_t_c=600.0, wall_outlet_t_c=450.0,
             economizer_outlet_t_c=340.0)
    )
    assert cfg.plant.is_supercritical  # 走同一套策略
    assert cfg.plant.grade_label == "超超临界"


# ---------------------------------------------------------------------------
# 2. 缺省补齐与提示
# ---------------------------------------------------------------------------
def test_missing_supercritical_fields_are_filled_with_notes() -> None:
    cfg, notes = resolve(
        _cfg(turbine_class="supercritical", main_steam_p_bar=265.0,
             main_steam_t_c=600.0)
    )
    assert cfg.plant.boiler_p_bar is not None
    assert cfg.plant.wall_outlet_t_c is not None
    assert cfg.plant.economizer_outlet_t_c is not None
    joined = " ".join(notes)
    assert "水冷壁出口温度" in joined
    assert "省煤器出口温度" in joined
    assert "请用热力特性书核对" in joined


def test_missing_subcritical_fields_are_filled_with_notes() -> None:
    cfg, notes = resolve(
        _cfg(drum_p_bar=None, economizer_subcooling_k=None,
             main_steam_p_bar=166.7, main_steam_t_c=538.0)
    )
    assert cfg.plant.drum_p_bar is not None
    assert cfg.plant.economizer_subcooling_k == 5.0
    assert "汽包压力" in " ".join(notes)


def test_resolve_is_idempotent() -> None:
    once, notes_once = resolve(_cfg(main_steam_p_bar=265.0, main_steam_t_c=600.0))
    twice, notes_twice = resolve(once)
    assert notes_twice == []
    assert twice == once

    finally_once = finalize(_cfg(main_steam_p_bar=265.0, main_steam_t_c=600.0))
    assert finalize(finally_once) == finally_once


def test_salt_fluid_follows_turbine_class() -> None:
    sub, _ = resolve(_cfg(main_steam_p_bar=166.7, main_steam_t_c=538.0))
    assert sub.salt.fluid == "solar_salt"
    assert sub.salt.spec.t_decompose_c == 600.0

    sup, _ = resolve(_cfg(main_steam_p_bar=265.0, main_steam_t_c=600.0))
    assert sup.salt.fluid == "advanced_nitrate"
    assert sup.salt.spec.t_decompose_c == 650.0


# ---------------------------------------------------------------------------
# 3. 校验分级：结构性问题拦，可疑输入只提示
# ---------------------------------------------------------------------------
def test_subcritical_with_supercritical_pressure_is_rejected() -> None:
    # finalize = resolve + validate：机型与压力矛盾时必须在这里就拦住
    try:
        finalize(_cfg(turbine_class="subcritical", main_steam_p_bar=265.0,
                      main_steam_t_c=600.0, drum_p_bar=185.0))
    except ConfigError as exc:
        assert "临界" in str(exc)
    else:
        raise AssertionError("亚临界 + 26.5 MPa 必须报错")


def test_supercritical_with_subcritical_pressure_is_rejected() -> None:
    cfg, _ = resolve(
        _cfg(turbine_class="supercritical", main_steam_p_bar=170.0,
             boiler_p_bar=185.0, wall_outlet_t_c=450.0,
             economizer_outlet_t_c=340.0)
    )
    try:
        validate(cfg)
    except ConfigError as exc:
        assert "临界压力" in str(exc)
    else:
        raise AssertionError("超临界 + 185 bar 必须报错")


def test_temperature_order_is_rejected() -> None:
    cfg, _ = resolve(
        _cfg(turbine_class="supercritical", main_steam_p_bar=265.0,
             main_steam_t_c=600.0, boiler_p_bar=278.0,
             wall_outlet_t_c=320.0, economizer_outlet_t_c=340.0)
    )
    try:
        validate(cfg)
    except ConfigError as exc:
        assert "省煤器出口" in str(exc)
    else:
        raise AssertionError("温度顺序颠倒必须报错")


def test_non_commercial_salt_is_a_warning_not_an_error() -> None:
    """照算 + 警示：盐种是假设，只在软提示里说，不能拦。"""

    cfg, _ = resolve(_cfg(main_steam_p_bar=265.0, main_steam_t_c=600.0))
    cfg = finalize(cfg)
    warnings = validate(cfg)
    assert any("假设" in w for w in warnings)


def test_salt_above_decomposition_keeps_computing_with_warning() -> None:
    cfg, _ = resolve(_cfg(main_steam_p_bar=166.7, main_steam_t_c=538.0))
    cfg = finalize(
        replace(cfg, salt=replace(cfg.salt, t_hot_c=610.0))
    )
    warnings = validate(cfg)
    assert any("分解温度" in w for w in warnings)


def test_unknown_key_is_rejected() -> None:
    try:
        apply_overrides(PlantConfig(), {"reactor": {"bogus": 1}})
    except ConfigError as exc:
        assert "bogus" in str(exc)
    else:
        raise AssertionError("未知字段必须被拒绝")


# ---------------------------------------------------------------------------
# 4. TOML 往返
# ---------------------------------------------------------------------------
def test_toml_roundtrip_keeps_values() -> None:
    base = load_config(ROOT / "config" / "subcritical_600mw.toml")
    text = dump_config(base)
    parsed = tomllib.loads(text)
    again = finalize(apply_overrides(PlantConfig(), parsed))
    assert again == base


def test_roundtrip_for_supercritical_card() -> None:
    base = load_config(ROOT / "config" / "supercritical_1000mw.toml")
    text = dump_config(base)
    again = finalize(apply_overrides(PlantConfig(), tomllib.loads(text)))
    assert again == base
    assert again.plant.is_supercritical


def test_dump_skips_empty_optional_fields() -> None:
    cfg = load_config(ROOT / "config" / "subcritical_600mw.toml")
    text = dump_config(cfg)
    assert "\nboiler_p_bar =" not in text
    assert "\nwall_outlet_t_c =" not in text
    assert "\ndrum_p_bar =" in text

    sc = load_config(ROOT / "config" / "supercritical_1000mw.toml")
    text = dump_config(sc)
    assert "\ndrum_p_bar =" not in text
    assert "\nboiler_p_bar =" in text


def test_salt_config_cannot_be_used_unresolved() -> None:
    """``salt.spec`` 在 ``fluid='auto'`` 时必须报错，而不是悄悄用太阳盐。"""

    try:
        SaltConfig().spec
    except ConfigError:
        return
    raise AssertionError("未解析的 salt.fluid 不应静默返回某个盐种")
