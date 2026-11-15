"""与两套独立实现的逐项对照（维护用）。

用途
----
数值路径需要独立实现的交叉校验。这个脚本把两份只读的早期筛选核当参照，对同一组
输入卡逐项比对结果：

1. 用被参照项目自己的输入卡跑那边的代码，导出一组数字；
2. 用同一张卡（本仓库里的副本）跑 ``c2n``，导出同一组数字；
3. 逐项比对，任何一项超出容差就失败。

两边都依赖 CoolProp，所以跑在同一个解释器上（``sys.executable``），
但**分开进程**——两个被参照项目都叫 ``cpp`` 包，同进程会互相覆盖。

用法::

    python tools/compare_legacy.py
    python tools/compare_legacy.py --tol 1e-9

预期：全部项相对偏差 0.0（同一个 CoolProp、同一套公式）。若有偏差，说明合并
过程中动到了数值路径，必须查清楚再发布。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
UNIFIED = HERE.parent
WORKSPACE = UNIFIED.parent
DUMP = HERE / "legacy_dump.py"

#: (旧项目, 旧输入卡, 新仓库里的同一张卡)
CASES = [
    ("C2N_CPP", "config/coal_plant.toml", "config/subcritical_600mw.toml"),
    ("C2N_CPP", "config/300MW_plant.toml", "config/subcritical_300mw.toml"),
    ("C2N_SCPP", "config/usc_1000mw.toml", "config/supercritical_1000mw.toml"),
    ("C2N_SCPP", "config/usc_660mw.toml", "config/supercritical_660mw.toml"),
    ("C2N_SCPP", "config/scpp_reference.toml", "config/reference_supercritical_600mw.toml"),
]

#: 需要逐项比对的标量字段（其余字段是标签或结构）
SCALARS = [
    "q_sh", "q_ev", "q_ec", "q_rh", "q_steam", "q_ihx", "q_core",
    "p_circulator", "m_helium", "t_core_in", "t_cold", "t_hot",
    "m_discharge", "m_charge", "split", "min_pinch",
    "ka_ihx", "area_total", "area_ihx",
    "net", "gross", "delta_net", "eta", "coal_net",
    "salt_t", "salt_total_t", "storage_mwh", "q_net", "sustainable",
]


def dump(project: Path, config: str, package: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(DUMP), "--project", str(project),
         "--config", config, "--package", package],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(project),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{project.name} / {config} 运行失败：\n{proc.stderr[-2000:]}"
        )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def rel_diff(a: float, b: float) -> float:
    scale = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / scale


def compare(case: tuple[str, str, str], tol: float) -> bool:
    legacy_dir, legacy_cfg, unified_cfg = case
    print(f"\n== {legacy_dir} :: {legacy_cfg}")
    print(f"   -> C2N_Unified :: {unified_cfg}")

    old = dump(WORKSPACE / legacy_dir, legacy_cfg, "cpp")
    new = dump(UNIFIED, unified_cfg, "c2n")

    ok = True
    worst = (0.0, "")
    for key in SCALARS:
        a, b = float(old[key]), float(new[key])
        diff = rel_diff(a, b)
        if diff > worst[0]:
            worst = (diff, key)
        if diff > tol:
            ok = False
            print(f"   [DIFF] {key:<14} 旧 {a:>16.6f}  新 {b:>16.6f}  相对差 {diff:.3e}")

    for name in old["ka"]:
        a, b = float(old["ka"][name]), float(new["ka"][name])
        diff = rel_diff(a, b)
        if diff > worst[0]:
            worst = (diff, f"ka.{name}")
        if diff > tol:
            ok = False
            print(f"   [DIFF] ka.{name:<10} 旧 {a:>16.6f}  新 {b:>16.6f}  相对差 {diff:.3e}")

    if len(old["part_load"]) != len(new["part_load"]):
        ok = False
        print(f"   [DIFF] 变工况点数量 {len(old['part_load'])} vs {len(new['part_load'])}")
    else:
        for i, (po, pn) in enumerate(zip(old["part_load"], new["part_load"])):
            for key in ("load", "net", "gross", "q_steam", "eta", "t_salt_min", "q_net"):
                a, b = float(po[key]), float(pn[key])
                diff = rel_diff(a, b)
                if diff > worst[0]:
                    worst = (diff, f"part_load[{i}].{key}")
                if diff > tol:
                    ok = False
                    print(f"   [DIFF] pt{i}.{key:<10} 旧 {a:>14.4f}  新 {b:>14.4f}  {diff:.3e}")

    label = "一致" if ok else "**不一致**"
    print(f"   结论：{label}；最大相对偏差 {worst[0]:.3e}（{worst[1]}）")
    print(f"   关键值：净电 {old['net']:.3f} / {new['net']:.3f} MW；"
          f"冷罐 {old['t_cold']:.3f} / {new['t_cold']:.3f} °C；"
          f"端差 {old['min_pinch']:.3f} / {new['min_pinch']:.3f} K")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tol", type=float, default=1.0e-9, help="相对容差")
    args = parser.parse_args()

    missing = [c for c in CASES if not (WORKSPACE / c[0]).is_dir()]
    if missing:
        print("缺少旧项目目录，无法比对：")
        for c in missing:
            print(f"  - {c[0]}")
        return 2

    results = [compare(case, args.tol) for case in CASES]
    print("\n" + "=" * 70)
    if all(results):
        print(f"审计通过：{len(results)} 组算例，全部关键项相对偏差 ≤ {args.tol:g}")
        return 0
    print(f"审计失败：{results.count(False)} / {len(results)} 组算例存在偏差")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
