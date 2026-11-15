"""离线自检：没有 CoolProp 时也能验证"装配正确"，有 CoolProp 时验证"数字没漂"。

它做两件事：

1. **结构自检**（任何环境都能跑）：五张算例卡逐个求解，检查
   能量闭合、有限性、变工况单调性、契约字段齐全、JSON 可序列化、
   机型自动判定、错误输入是否被挡住。
2. **基准自检**（仅当装了真 CoolProp）：把 ``tests/`` 里的回归数字再核对一遍。

用法::

    python tools/offline_selfcheck.py            # 自动：有真 CoolProp 就用真的
    python tools/offline_selfcheck.py --fake     # 强制用假物性（只验结构）
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tomllib
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import fake_coolprop  # noqa: E402

CASES = [
    "config/subcritical_600mw.toml",
    "config/subcritical_300mw.toml",
    "config/supercritical_1000mw.toml",
    "config/supercritical_660mw.toml",
    "config/reference_supercritical_600mw.toml",
]

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"    [FAIL] {message}")


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(v)) for v in values)


def run_case(rel: str) -> None:
    from c2n.assess import assess
    from c2n.config import load_config
    from c2n.design import run_design

    print(f"\n== {rel}")
    cfg = load_config(ROOT / rel)
    result = run_design(cfg)

    print(
        f"   机型 {cfg.plant.grade_label:<5} 盐 {cfg.salt.spec.short:<4} "
        f"| Q汽侧 {result.steam.q_steam_mw:8.1f} MW "
        f"| 净电 {result.net_power_mw:8.1f} MW "
        f"| 模块 {result.reactor.n_modules} "
        f"| 端差 {result.salt_loop.min_pinch_k:5.2f} K "
        f"| 变工况点 {len(result.part_load)}"
    )

    check(result.steam.q_steam_mw > 0.0, f"{rel}: 汽侧吸热必须为正")
    check(
        abs(result.reactor.q_ihx_mw - result.steam.q_steam_mw - result.q_net_mw) < 1e-6,
        f"{rel}: 能量不闭合",
    )
    check(_finite(result.net_power_mw), f"{rel}: 净电非有限值")
    check(_finite(result.eta_cycle_pct), f"{rel}: 效率非有限值")
    check(_finite(result.salt_loop.t_cold_c), f"{rel}: 冷罐温度非有限值")
    check(len(result.part_load) == len(cfg.partload.load_points), f"{rel}: 变工况点缺失")

    for name, sec in result.salt_loop.sections.items():
        check(_finite(sec.ka_mw_k), f"{rel}: {name} kA 非有限值")
        check(sec.ka_mw_k > 0.0, f"{rel}: {name} kA 必须为正")
        check(sec.area_m2 > 0.0, f"{rel}: {name} 面积必须为正")

    # 盐侧反解必须精确命中设计端差（这是两次二分的正确性判据）
    check(
        abs(result.salt_loop.min_pinch_k - cfg.salt.min_pinch_k) < 0.05,
        f"{rel}: 盐侧最小端差 {result.salt_loop.min_pinch_k:.3f} "
        f"未命中目标 {cfg.salt.min_pinch_k:.3f}",
    )

    # IHX 是否可行由 helium / 盐两侧独立决定，允许不可行（kA=inf），但不允许 NaN
    check(
        not math.isnan(result.ihx.ka_mw_k),
        f"{rel}: IHX kA 出现 NaN",
    )

    loads = [pt.load for pt in result.part_load]
    check(loads == sorted(loads, reverse=True), f"{rel}: 负荷点必须递减")
    for prev, cur in zip(result.part_load, result.part_load[1:]):
        if prev.q_steam_mw > 0 and cur.q_steam_mw > 0:
            check(
                cur.q_steam_mw <= prev.q_steam_mw + 1e-6,
                f"{rel}: 汽侧吸热应随负荷下降（{prev.load:.0%}->{cur.load:.0%}）",
            )
            check(
                cur.net_mw <= prev.net_mw + 1e-6,
                f"{rel}: 净电应随负荷下降（{prev.load:.0%}->{cur.load:.0%}）",
            )

    envelope = assess(
        tomllib.loads((ROOT / rel).read_text(encoding="utf-8-sig")),
        case_id=Path(rel).stem,
    )
    text = json.dumps(envelope, ensure_ascii=False, allow_nan=False)
    check(len(text) > 1000, f"{rel}: 信封太短，可能装配失败")

    option = envelope["technical"]["options"][0]
    for key in ("option_id", "label", "reactor", "feasibility", "sizing", "performance"):
        check(key in option, f"{rel}: 契约缺字段 {key}")
    check(
        envelope["technical"]["provenance"]["kernel"] == "c2n-screening",
        f"{rel}: provenance.kernel 不对",
    )
    check(
        len(option["sizing"]["hx"]) == 5,
        f"{rel}: 换热器表应为四段 + IHX 共 5 行",
    )
    for row in option["checks"]:
        check("id" in row and "ok" in row, f"{rel}: 校核项缺 id/ok")
        check(isinstance(row.get("value"), (int, float)), f"{rel}: 校核 {row['id']} 缺数值")
    hx_rows = [s for s in envelope["details"]["sections"] if s["id"] != "IHX"]
    check(
        len(hx_rows) == 4
        and all(
            isinstance(s.get("q_mw"), (int, float))
            and isinstance(s.get("ka_mw_k"), (int, float))
            for s in hx_rows
        ),
        f"{rel}: 四段换热器表缺数值",
    )
    check(
        len(envelope["details"]["states"]) >= 17,
        f"{rel}: 状态点少于 17 个",
    )


def run_guards() -> None:
    """错误输入必须被挡住；可疑但不致命的输入必须照算 + 警示。"""

    from c2n.assess import assess
    from c2n.config import ConfigError

    print("\n== 输入守卫")

    # 1) 亚临界机组给了超临界压力 -> 必须报错（机型与压力矛盾）
    try:
        assess(
            {
                "plant": {
                    "turbine_class": "subcritical",
                    "main_steam_p_bar": 265.0,
                    "main_steam_t_c": 600.0,
                }
            }
        )
        check(False, "亚临界 + 26.5 MPa 应报错，但没有")
    except ConfigError as exc:
        check("超临界" in str(exc) or "临界" in str(exc), f"报错信息不可读：{exc}")
        print(f"    [OK] 机型矛盾被挡住：{str(exc).splitlines()[0][:60]}…")

    # 2) 缺专属字段 -> 自动补缺省值 + 提示（不报错）
    env, = [
        assess(
            {
                "plant": {
                    "turbine_class": "supercritical",
                    "main_steam_p_bar": 265.0,
                    "main_steam_t_c": 600.0,
                    "main_steam_m_kgs": 819.0,
                    "gross_power_mw": 1000.0,
                },
                "salt": {"t_hot_c": 620.0},
                "reactor": {"n_modules": 8},
            }
        )
    ]
    notes = " ".join(env["details"]["assumptions"])
    check("水冷壁出口温度" in notes, "缺省补齐后应留下'按典型值计算'的提示")
    print(f"    [OK] 缺省补齐留下 {len(env['details']['assumptions'])} 条假设")

    # 3) 毛电与蒸汽流量明显不自洽 -> 照算 + 警示
    env = assess(
        {
            "plant": {
                "gross_power_mw": 590.0,
                "main_steam_m_kgs": 300.0,
            }
        }
    )
    warnings = " ".join(env["technical"]["warnings"])
    check("不自洽" in warnings, "毛电/流量不自洽应给出警示")
    check(env["technical"]["options"][0]["performance"]["net_mw"] > 0, "照算失败")
    print(f"    [OK] 不自洽输入照算并给出提示（净电 "
          f"{env['technical']['options'][0]['performance']['net_mw']:.1f} MW）")


def run_reference_numbers() -> None:
    """只有真 CoolProp 才有意义：钉住两张基准卡的关键数字。

    这些数字不是"文献值"，而是**旧工具（C2N_CPP / C2N_SCPP）在同一个 CoolProp
    下的输出**——由 ``tools/compare_legacy.py`` 逐项审计确认（相对偏差 0）。
    所以它们是"合并没有改动数值路径"的证据。
    """

    from c2n.config import load_config
    from c2n.design import run_design

    print("\n== 基准数字（需真 CoolProp）")

    cases = [
        (
            "config/subcritical_600mw.toml",
            [
                ("汽侧总吸热", lambda d: d.steam.q_steam_mw, 1266.3910, 0.05, "MW"),
                ("IHX 热负荷", lambda d: d.reactor.q_ihx_mw, 1029.9563, 0.05, "MW"),
                ("净电", lambda d: d.net_power_mw, 531.6361, 0.05, "MW"),
                ("冷罐温度", lambda d: d.salt_loop.t_cold_c, 303.1949, 0.05, "°C"),
                ("盐总量", lambda d: d.sizing.salt_total_t, 9425.7, 0.5, "t"),
                ("储热容量", lambda d: d.storage.capacity_mwh, 945.7, 0.5, "MWh"),
            ],
        ),
        (
            "config/supercritical_1000mw.toml",
            [
                ("汽侧总吸热", lambda d: d.steam.q_steam_mw, 2176.9000, 0.05, "MW"),
                ("IHX 热负荷", lambda d: d.reactor.q_ihx_mw, 2059.9125, 0.05, "MW"),
                ("净电", lambda d: d.net_power_mw, 891.8948, 0.05, "MW"),
                ("冷罐温度", lambda d: d.salt_loop.t_cold_c, 321.3189, 0.05, "°C"),
                ("盐总量", lambda d: d.sizing.salt_total_t, 4071.1, 0.5, "t"),
                (
                    "四段总面积",
                    lambda d: sum(s.area_m2 for s in d.salt_loop.sections.values()),
                    82278.0,
                    5.0,
                    "m²",
                ),
            ],
        ),
    ]

    for rel, items in cases:
        d = run_design(load_config(ROOT / rel))
        print(f"    {rel}")
        for label, getter, expected, tol, unit in items:
            actual = getter(d)
            check(
                abs(actual - expected) <= tol,
                f"{rel}: {label} {actual:.4f} 偏离基准 {expected}（±{tol}）",
            )
            print(f"      {label:<12}{actual:>12.4f} {unit:<4} (基准 {expected})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fake", action="store_true", help="强制用假物性")
    args = parser.parse_args()

    if args.fake:
        fake_coolprop.install()
        print("物性：假实现（只验结构，数字无物理意义）")
    else:
        fake_coolprop.install()  # 有真 CoolProp 时 install() 不会覆盖
        real = fake_coolprop.has_real_coolprop()
        print(f"物性：{'真 CoolProp' if real else '假实现（未检测到 CoolProp）'}")

    try:
        for rel in CASES:
            run_case(rel)
        run_guards()
        if fake_coolprop.has_real_coolprop():
            run_reference_numbers()
        else:
            print("\n== 基准数字：跳过（没有真 CoolProp，装了之后重跑本脚本）")
    except Exception:
        traceback.print_exc()
        FAILURES.append("未捕获异常")

    print("\n" + "=" * 70)
    if FAILURES:
        print(f"自检失败：{len(FAILURES)} 项")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
