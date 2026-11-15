"""把**旧工具**（`C2N_CPP` / `C2N_SCPP`）的一个算例结果导成 JSON。

为什么要它
----------
统一内核是把两个"平行分叉"合并回一份代码。合并是否忠实，唯一的判据是：
**同一份输入卡，旧工具与新内核给出的数字是否逐项一致**。

这个脚本在旧项目的目录里运行（两边都叫 ``cpp`` 包，所以必须分进程跑），把关键
结果打成 JSON；``compare_legacy.py`` 再拿它与新内核的输出逐项比对。

用法（一般由 compare_legacy.py 调用）::

    cd C2N_CPP
    python ../C2N_Unified/tools/legacy_dump.py --project . --config config/coal_plant.toml --package cpp
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, help="旧项目根目录")
    parser.add_argument("--config", required=True, help="输入卡（相对项目根）")
    parser.add_argument("--package", default="cpp", help="旧包的 import 名")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    sys.path.insert(0, str(project))

    config = __import__(f"{args.package}.config", fromlist=["load_config"])
    design = __import__(f"{args.package}.design", fromlist=["run_design"])

    cfg = config.load_config(project / args.config)
    result = design.run_design(cfg)

    steam = result.steam
    loop = result.salt_loop
    payload = {
        "project": str(project),
        "config": args.config,
        "q_sh": steam.q_superheater_mw,
        "q_ev": steam.q_evaporator_mw,
        "q_ec": steam.q_economizer_mw,
        "q_rh": steam.q_reheater_mw,
        "q_steam": steam.q_steam_mw,
        "q_ihx": result.reactor.q_ihx_mw,
        "q_core": result.reactor.q_core_mw,
        "p_circulator": result.reactor.p_circulator_mw,
        "m_helium": result.reactor.m_helium_kgs,
        "t_core_in": result.reactor.t_core_in_c,
        "t_cold": loop.t_cold_c,
        "t_hot": loop.t_hot_c,
        "m_discharge": loop.m_discharge_kgs,
        "m_charge": loop.m_charge_kgs,
        "split": loop.split_fraction,
        "min_pinch": loop.min_pinch_k,
        "binding": loop.binding,
        "ka": {name: sec.ka_mw_k for name, sec in loop.sections.items()},
        "ka_ihx": result.ihx.ka_mw_k,
        "area_total": sum(sec.area_m2 for sec in loop.sections.values()),
        "area_ihx": result.ihx.area_m2,
        "net": result.net_power_mw,
        "gross": result.gross_electric_mw,
        "delta_net": result.net_power_delta_mw,
        "eta": result.eta_cycle_pct,
        "coal_net": result.coal_net_power_mw,
        "salt_t": result.storage.mass_t,
        "salt_total_t": result.sizing.salt_total_t,
        "storage_mwh": result.storage.capacity_mwh,
        "q_net": result.q_net_mw,
        "modules": result.modules_self_sufficient,
        "sustainable": result.sustainable_load_pct,
        "part_load": [
            {
                "load": pt.load,
                "net": pt.net_mw,
                "gross": pt.gross_mw,
                "q_steam": pt.q_steam_mw,
                "eta": pt.eta_cycle_pct,
                "t_salt_min": pt.t_salt_min_c,
                "q_net": pt.q_net_mw,
            }
            for pt in result.part_load
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
