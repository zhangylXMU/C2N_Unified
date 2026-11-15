"""输出：Markdown 报告、CSV 表、JSON 汇总。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .design import DesignResult


def write_sections_csv(result: DesignResult, path: str | Path) -> Path:
    """四台换热器 + IHX 的设计表。"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [sec.as_row() for sec in result.salt_loop.sections.values()]
    rows.append(result.ihx.as_row())
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: (f"{value:.4f}" if isinstance(value, float) else value)
                for key, value in row.items()
            })
    return path


def write_states_csv(states, path: str | Path) -> Path:
    """状态点表（编号与拓扑图一致）。"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [sp.as_row() for sp in states]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_json(result: DesignResult, path: str | Path) -> Path:
    payload = {
        "plant": result.cfg.plant.__dict__,
        "reactor": {
            "n_modules": result.reactor.n_modules,
            "module_power_mwth": result.reactor.module_power_mwth,
            "q_core_mw": result.reactor.q_core_mw,
            "q_ihx_mw": result.reactor.q_ihx_mw,
            "m_helium_kgs": result.reactor.m_helium_kgs,
            "t_core_in_c": result.reactor.t_core_in_c,
            "p_circulator_shaft_mw": result.reactor.p_circulator_mw,
            "p_circulator_electric_mw": result.aux_circulator_mw,
        },
        "steam_side": result.steam.as_rows(),
        "sections": [sec.as_row() for sec in result.salt_loop.sections.values()],
        "ihx": result.ihx.as_row(),
        "salt_loop": {
            "t_hot_c": result.salt_loop.t_hot_c,
            "t_cold_c": result.salt_loop.t_cold_c,
            "m_discharge_kgs": result.salt_loop.m_discharge_kgs,
            "m_charge_kgs": result.salt_loop.m_charge_kgs,
            "split_fraction": result.salt_loop.split_fraction,
            "pinches": result.salt_loop.pinches,
            "min_pinch_k": result.salt_loop.min_pinch_k,
            "binding": result.salt_loop.binding,
        },
        "storage": result.storage.__dict__,
        "balance": {
            "q_net_mw": result.q_net_mw,
            "mode": result.mode,
            "modules_self_sufficient": result.modules_self_sufficient,
            "q_core_self_sufficient_mw": result.q_core_self_sufficient_mw,
            "sustainable_load_pct": result.sustainable_load_pct,
        },
        "power": {
            "gross_electric_mw": result.gross_electric_mw,
            "aux_circulator_mw": result.aux_circulator_mw,
            "aux_salt_pump_mw": result.aux_salt_pump_mw,
            "aux_turbine_island_mw": result.aux_turbine_island_mw,
            "boiler_aux_mw": result.boiler_aux_mw,
            "net_power_mw": result.net_power_mw,
            "coal_net_power_mw": result.coal_net_power_mw,
            "net_power_delta_mw": result.net_power_delta_mw,
            "eta_cycle_pct": result.eta_cycle_pct,
        },
        "part_load": [point.as_row() for point in result.part_load],
        "checks": [check.as_row() for check in result.checks],
    }
    path = Path(path)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def _sizing_section(result: DesignResult) -> list[str]:
    """两个业主问题：换热面积/外形、盐量/罐尺寸。"""

    s = result.sizing
    sc = result.cfg.sizing
    lines = [
        "",
        "## 3. 设备尺寸：业主最常问的两个数",
        "",
        "> 这一节把 `kA` 与 `MWh` 折算成**面积、外形、盐量、罐尺寸**。全部按常规"
        "壳管式换热器与立式圆筒罐估算，误差约 ±30 %，用于判断\"厂房放不放得下、"
        "盐好不好运\"，**不替代设备选型计算**。",
        "",
        "### 3.1 要多少换热面积、换热器多大",
        "",
        "| 设备 | 热负荷 MW | 面积 m² | 管数 | 管长 m | 管束 Ø m | 壳体内径 m | 壳体高 m | 并联台数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, bundle in s.exchangers.items():
        q = result.salt_loop.sections[name].q_mw
        lines.append(
            f"| {name} | {q:.1f} | {bundle.area_m2:,.0f} | {bundle.n_tubes:,} | "
            f"{bundle.tube_length_m:.0f} | {bundle.bundle_diameter_m:.2f} | "
            f"{bundle.shell_id_m:.2f} | {bundle.shell_height_m:.1f} | "
            f"{bundle.n_shells} |"
        )
    ihx = s.ihx
    lines.append(
        f"| IHX（氦-盐） | {result.ihx.q_mw:.1f} | {ihx.area_m2:,.0f} | "
        f"{ihx.n_tubes:,} | {ihx.tube_length_m:.0f} | {ihx.bundle_diameter_m:.2f} | "
        f"{ihx.shell_id_m:.2f} | {ihx.shell_height_m:.1f} | {ihx.n_shells} |"
    )

    max_id = max(
        [b.shell_id_m for b in s.exchangers.values()] + [ihx.shell_id_m]
    )
    lines += [
        "",
        f"- 四台换热器合计 **{s.area_total_m2:,.0f} m²**，IHX **{ihx.area_m2:,.0f} m²**，"
        f"全部换热面 **{s.area_total_m2 + ihx.area_m2:,.0f} m²**。",
        f"- 最大单台壳体 **Ø{max_id:.1f} m × {ihx.shell_height_m:.1f} m**，"
        f"四台换热器共 **{s.shell_count_total} 台并联壳体**，IHX 共 {ihx.n_shells} 台。",
        f"- 面积取\"选型 kA\"（已含裕量 {result.cfg.salt.ua_margin:.2f}），"
        "按 `面积 = kA / U` 折算；各设备的 U 见第 2 节。",
        "",
        "**本节假设**（全部可在 `[sizing]` 里改）：",
        "",
        f"1. 壳管式、立式布置；换热管 Ø{sc.tube_od_mm:.0f}×{sc.tube_wall_mm:.0f} mm、"
        f"长 {sc.tube_length_m:.0f} m，正三角形排列、管间距 {sc.pitch_ratio:.2f}d"
        f"（= {s.pitch_mm:.1f} mm）。",
        f"2. 管束直径按 `p·√(1.103 N)` 估算（六边形密排）；壳体内径 = 管束 + "
        f"2×{sc.shell_clearance_m:.2f} m；壳体高 = 管长 + 1.2 m（管板、接管、封头）。",
        f"3. 单台壳体内径超过 {sc.max_shell_id_m:.1f} m 时并联多台（运输限制），"
        "面积按壳体截面积均分。",
        "4. 壳体壁厚、保温、管板厚度、折流板与压降均未建模；U 是经验值，"
        "实际面积由厂家按介质流速与污垢系数确定。",
        "5. 氦-盐 IHX 工程上多用印刷电路板式（PCHE）或螺旋管式，"
        "本表按等效壳管式给出，实际体积会小很多。",
        "",
        "### 3.2 要多少熔盐、储罐多大",
        "",
        "| 项目 | 数值 | 单位 |",
        "|---|---:|---|",
        f"| 储罐活动盐量（参与储热） | {s.salt_active_t:,.0f} | t |",
        f"| 回路滞留盐量（+{100 * s.inventory_margin:.0f} %） | "
        f"{s.salt_inventory_extra_t:,.0f} | t |",
        f"| **需采购{result.salt_short}总量** | **{s.salt_total_t:,.0f}** | **t** |",
        f"| 热罐有效容积 / 含气相空间 | {s.hot_tank.active_volume_m3:,.0f} / "
        f"{s.hot_tank.tank_volume_m3:,.0f} | m³ |",
        f"| 冷罐有效容积 / 含气相空间 | {s.cold_tank.active_volume_m3:,.0f} / "
        f"{s.cold_tank.tank_volume_m3:,.0f} | m³ |",
        f"| 热罐直径 × 高度 | Ø{s.hot_tank.diameter_m:.1f} × "
        f"{s.hot_tank.height_m:.1f} | m |",
        f"| 冷罐直径 × 高度 | Ø{s.cold_tank.diameter_m:.1f} × "
        f"{s.cold_tank.height_m:.1f} | m |",
        f"| 单罐占地 / 两罐合计 | {s.hot_tank.footprint_m2:,.0f} / "
        f"{s.hot_tank.footprint_m2 + s.cold_tank.footprint_m2:,.0f} | m² |",
        f"| 储热容量 / 时长 | {result.storage.capacity_mwh:,.0f} / "
        f"{result.storage.hours:.1f} | MWh / h |",
        "",
        "**本节假设**（全部可在 `[sizing]` 里改）：",
        "",
        f"1. 双罐（冷/热分离）、完全混合；真实分层会略减所需容积（差 < 2 %）。",
        f"2. 每个罐都要能容纳全部活动盐量（放空/充满两种极端），"
        f"气相空间 {100 * s.ullage:.0f} %（液位波动 + 氮封）。",
        f"3. 立式圆筒、平底拱顶，高径比 H/D = {s.h_over_d:.1f}；"
        "罐壁钢材、保温、基础、伴热均未建模。",
        f"4. 盐密度按各罐温度（热罐 {result.salt_loop.t_hot_c:.0f} °C / "
        f"冷罐 {result.salt_loop.t_cold_c:.1f} °C）取自{result.salt_short}拟合式。",
        f"5. 回路滞留按活动盐量的 {100 * s.inventory_margin:.0f} % 计"
        "（管道、IHX、四台换热器与泵坑持液）。",
        "",
    ]
    return lines


def write_markdown(
    result: DesignResult,
    states,
    path: str | Path,
    figure: str = "scheme.png",
) -> Path:
    cfg = result.cfg
    p, r, s, st = cfg.plant, cfg.reactor, cfg.salt, cfg.storage
    pl = cfg.partload
    steam, loop, storage = result.steam, result.salt_loop, result.storage

    lines: list[str] = []
    lines += [
        f"# C2N 锅炉侧替换方案（{p.grade_label}）：{p.name}",
        "",
        "> 由 `python run.py` 自动生成。目标是**最小改动**：汽轮机本体、回热系统、",
        "> 凝汽器、发电机全部不动，只替换锅炉侧的四台换热器、增加熔盐双罐储热与",
        "> 高温气冷堆。",
        "",
        f"![改造方案]({figure})",
        "",
        "## 1. 业主提供的汽轮机侧边界（100 % 工况）",
        "",
        "| 项目 | 数值 | 单位 |",
        "|---|---:|---|",
        f"| 汽轮机毛电（口径：{p.gross_basis}） | {result.gross_electric_mw:.1f} | MW |",
        f"| 主蒸汽压力 / 温度 / 流量 | {p.main_steam_p_bar:.1f} / {p.main_steam_t_c:.0f} / {p.main_steam_m_kgs:.2f} | bar / °C / kg/s |",
        f"| 冷再热压力 / 温度 / 流量 | {p.cold_reheat_p_bar:.1f} / {p.cold_reheat_t_c:.1f} / {p.reheat_m_kgs:.2f} | bar / °C / kg/s |",
        f"| 热再热压力 / 温度 | {p.hot_reheat_p_bar:.1f} / {p.hot_reheat_t_c:.0f} | bar / °C |",
    ]
    if p.is_supercritical:
        lines += [
            f"| 水冷壁出口压力（= 蒸发段出口） | {p.p_boiler_bar:.1f} | bar |",
            f"| 水冷壁出口温度 | {p.wall_outlet_t_c:.1f} | °C |",
            f"| 省煤器出口温度 | {p.economizer_outlet_t_c:.1f} | °C |",
            f"| 拟临界温度（该压力下 cp 峰值） | "
            f"{result.steam.t_pc_c:.1f} | °C |",
        ]
    else:
        lines += [
            f"| 汽包（蒸汽发生器）压力 | {p.p_boiler_bar:.1f} | bar |",
            f"| 省煤器出口过冷度 | {p.economizer_subcooling_k:.1f} | K |",
        ]
    lines += [
        f"| 给水压力 / 温度 | {p.feedwater_p_bar:.1f} / {p.feedwater_t_c:.1f} | bar / °C |",
        f"| 盐种 | {s.spec.label}（分解温度 {s.spec.t_decompose_c:.0f} °C） | — |",
        "",
        "## 2. 四台换热器（新的锅炉换热面）",
        "",
        "这四个部件是锅炉与汽轮机的耦合面，也是本次改造**唯一需要更换的设备**。",
        "kA 由**沿程积分** `∫dQ/ΔT` 求得（端点 LMTD 在蒸汽比热剧变时会偏 20~40 %）。",
        "",
        "| 换热器 | 热负荷 MW | 所需 kA MW/K | 选型 kA MW/K | 面积 m² | 等效 LMTD K | 端差 热/冷 K | 沿程最小 K |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, sec in loop.sections.items():
        lines.append(
            f"| {steam.label(name)} | {sec.q_mw:.1f} | {sec.ka_mw_k:.2f} | "
            f"{sec.ka_selected_mw_k:.2f} | {sec.area_m2:,.0f} | {sec.lmtd_k:.2f} | "
            f"{sec.dt_hot_end_k:.1f}/{sec.dt_cold_end_k:.1f} | {sec.dt_min_k:.2f} |"
        )

    lines += [
        "",
        f"- 四台合计 **{steam.q_steam_mw:.1f} MW**（主蒸汽段 "
        f"{steam.q_boiler_mw:.1f} + 再热段 {steam.q_reheater_mw:.1f}）。",
        f"- 端差瓶颈 **{loop.binding}（{loop.min_pinch_k:.2f} K）**，"
        f"设计下限 {s.min_pinch_k:.1f} K。",
        f"- 面积按 U = 过热器 {s.u_superheater_w_m2k:.0f} / 蒸发器 "
        f"{s.u_evaporator_w_m2k:.0f} / 省煤器 {s.u_economizer_w_m2k:.0f} / 再热器 "
        f"{s.u_reheater_w_m2k:.0f} W/(m²·K) 折算，"
        f"面积裕量系数 {s.ua_margin:.2f}（乘在\"选型 kA\"上，不改变所需 kA）。",
        f"- 主支路份额由反解得到：**{loop.split_fraction:.3f}**"
        + ("（optimise_split = true）" if s.optimise_split else "（固定值）") + "。",
    ]
    total_area = sum(sec.area_m2 for sec in loop.sections.values())
    if result.area_pinch_plus1_m2 > 0.0:
        delta = 100.0 * (result.area_pinch_plus1_m2 - total_area) / total_area
        lines.append(
            f"- **面积对夹点极敏感**：最小端差放宽 1 K（{s.min_pinch_k:.0f} → "
            f"{s.min_pinch_k + 1:.0f} K），四台总面积 {total_area:,.0f} → "
            f"{result.area_pinch_plus1_m2:,.0f} m²（{delta:+.0f} %）。"
        )
    lines += _sizing_section(result)
    lines += [
        "",
        "## 4. 状态点（编号与拓扑图一致）",
        "",
        "| 编号 | 节点 | 工质 | m kg/s | p bar | T °C | h kJ/kg | 说明 |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for sp in states:
        lines.append(
            f"| {sp.id} | {sp.name} | {sp.fluid} | {sp.m_kgs:.1f} | "
            f"{sp.p_bar:.2f} | {sp.t_c:.1f} | {sp.h_kj_kg:.0f} | {sp.note} |"
        )

    lines += [
        "",
        "> 熔盐回路闭合：冷罐 → IHX → 热罐 → 热盐总管 →"
        "（过热器→蒸发器→省煤器 | 再热器）→ 冷盐总管 → 冷罐。",
        "> 编号与 `states.csv`、拓扑图上的圆标一一对应。",
        "",
        "## 5. 高温气冷堆",
        "",
        "| 项目 | 数值 | 单位 |",
        "|---|---:|---|",
        f"| 模块数 × 单模块功率 | {r.n_modules} × {r.module_power_mwth:.0f} | — / MWth |",
        f"| 堆芯总热功率 | {r.q_core_mw:.1f} | MWth |",
        f"| 氦气出口 / IHX 出口温度 | {r.t_hot_c:.0f} / {r.t_ihx_out_c:.0f} | °C |",
        f"| 氦气压力（堆芯进口） | {r.p_cold_bar:.1f} | bar |",
        f"| 氦气总流量 | {result.reactor.m_helium_kgs:.2f} | kg/s |",
        f"| 氦风机轴功 / 电耗 | {result.reactor.p_circulator_mw:.2f} / "
        f"{result.aux_circulator_mw:.2f} | MW |",
        f"| IHX 热负荷 | {result.reactor.q_ihx_mw:.1f} | MW |",
        f"| IHX 所需 kA / 面积 | {result.ihx.ka_mw_k:.3f} / {result.ihx.area_m2:,.0f} | MW/K / m² |",
        "",
        "### 堆功率够不够？",
        "",
        f"100 % 工况下汽轮机需要 **{steam.q_steam_mw:.1f} MW**，"
        f"本配置的堆提供 **{result.reactor.q_ihx_mw:.1f} MW**；",
    ]
    if result.q_net_mw < 0:
        lines += [
            f"缺口 **{abs(result.q_net_mw):.1f} MW 由储罐放热**，"
            f"{storage.hours:.1f} h 需要 {storage.capacity_mwh:.0f} MWh 储热容量。",
            "",
            f"> **注意：储热只能放、不能充。** 反应堆满功率时始终欠 "
            f"{abs(result.q_net_mw):.1f} MW，罐子在 {storage.hours:.1f} h 后放空，"
            f"此后若不加模块，机组只能维持 **{result.sustainable_load_pct:.1f} %** "
            f"负荷（按热负荷比例估算）。若要求持续满发，需 "
            f"**{result.modules_self_sufficient} 个模块"
            f"（{result.q_core_self_sufficient_mw:.0f} MWth）**。",
        ]
    else:
        lines += [
            f"余量 **{result.q_net_mw:.1f} MW 充入储罐**，"
            f"{storage.hours:.1f} h 充满 {storage.capacity_mwh:.0f} MWh。",
            "",
            "> 堆功率大于汽轮机需求，储罐在 100 % 工况下是**充热**状态；"
            "放热能力要留到低负荷或停机备用时才用得上。",
        ]

    lines += [
        "",
        "## 6. 熔盐回路与储热",
        "",
        "| 项目 | 数值 | 单位 |",
        "|---|---:|---|",
        f"| 热盐 / 冷盐温度 | {loop.t_hot_c:.1f} / {loop.t_cold_c:.2f} | °C |",
        f"| 盐流量（放热侧 / 充热侧） | {loop.m_discharge_kgs:.0f} / {loop.m_charge_kgs:.0f} | kg/s |",
        f"| 主支路 / 再热支路流量 | {loop.m_main_kgs:.0f} / {loop.m_reheat_kgs:.0f} | kg/s |",
        f"| 储热时长（100 % 工况） | {storage.hours:.1f} | h |",
        f"| 储热容量 | {storage.capacity_mwh:.0f} | MWh |",
        f"| {result.salt_short}质量 | {storage.mass_t:,.0f} | t |",
        f"| 热罐 / 冷罐容积 | {storage.v_hot_m3:,.0f} / {storage.v_cold_m3:,.0f} | m³ |",
        f"| 冷热罐总容积 | {storage.total_m3:,.0f} | m³ |",
        f"| 盐泵电功率 | {result.aux_salt_pump_mw:.2f} | MW |",
        "",
        f"冷盐温度是**反解结果**：取使最紧端差等于 {s.min_pinch_k:.1f} K 的盐流量，"
        f"因此冷罐温度 {loop.t_cold_c:.2f} °C 是这套换热面的必然结果，不是人为设定。",
        "",
        "盐泵电功率按充热、放热两条回路**同时运行**计（反应堆常开，蒸汽发生器回路"
        "也常在运行状态）。",
        "",
        "## 7. 出力与效率（同口径对比）",
        "",
        "| 项目 | 煤电（改造前） | C2N（改造后） | 单位 |",
        "|---|---:|---:|---|",
    ]
    for name, coal, c2n, unit in result.comparison_rows():
        lines.append(f"| {name} | {coal} | {c2n} | {unit} |")

    if result.aux_turbine_island_mw <= 0.0:
        estimate_aux = 0.045 * result.gross_electric_mw
        lines += [
            "",
            f"> `turbine_island_aux_mw` 未填（= 0），上表的\"净电\"没有扣汽轮机岛辅机。"
            f"若按毛电 4.5 % 估算（约 {estimate_aux:.1f} MW），绝对净电约为 "
            f"**{result.net_power_mw - estimate_aux:.1f} MW**；"
            "但**净电变化**（改造前后之差）不受影响。",
        ]

    lines += [
        "",
        "- **净电变化 = 煤粉炉辅机 − 氦风机 − 盐泵**，与汽轮机岛辅机无关。",
        f"- **汽机侧净循环效率 = {result.eta_cycle_pct:.2f} %**"
        " ＝ 净电 / 四台换热器吸热。",
        "  分子 = 毛电 − 汽轮机岛辅机 − 氦风机电耗 − 盐泵电耗（全厂净电）；",
        "  分母 = 四台换热器汽水侧吸热（主蒸汽段 + 再热段）。",
        "  这是**蒸汽循环**的效率，**不是一次能源效率**：储罐吞吐与堆芯热功率都不进分母。",
        "  充热工况下堆的多余热量进了储罐、当刻没有变成电，这一指标自然偏低；",
        "  放热工况下储罐补的热量算在分母里，它也不会虚高。",
        "",
        "## 8. 非额定工况估算（不建模汽轮机通流）",
        "",
        "只引入三个可标定的工程关系，其余是精确热平衡：",
        "",
        f"- **滑压**：`p/p0 = (m/m0)^{pl.sliding_exponent:.2f}`，流量比低于 "
        f"{pl.sliding_floor:.2f} 时转定压节流；",
        f"- **级组效率**：`eta/eta0 = f(负荷)`，f 由 `[partload].eta_ratio` 给出"
        f"（{len(pl.load_points)} 个点，默认取自亚临界再热机组滑压运行的常规范围）；",
        "- **给水温度**："
        + (
            "本次用输入卡给的给水温度表（"
            + result.part_load[0].t_fw_source
            + "）。表只提供随负荷变化的形状，绝对水平锚定在 `[plant].feedwater_t_c`。"
            if pl.feedwater_t_c
            else "本次按\"抽汽级数不变、抽汽压力随滑压比缩放\"估算。"
        ),
        "",
        "| 负荷 % | 工况 | 主蒸汽 kg/s | 主蒸汽 bar | 给水 °C | 汽侧吸热 MW | 毛电 MW | 净电 MW | 储罐 ±MW | 满发可维持 h | 最低盐温 °C | 效率 % |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for pt in result.part_load:
        mode = "充热" if pt.q_net_mw > 5.0 else ("放热" if pt.q_net_mw < -5.0 else "平衡")
        if abs(pt.q_net_mw) <= 5.0:
            hours = "—（平衡）"
        elif pt.q_net_mw > 0.0:
            hours = f"充满 {pt.storage_hours:.1f}"
        else:
            hours = f"{pt.storage_hours:.1f}"
        flag = "❗" if pt.salt_warnings else ""
        lines.append(
            f"| {100 * pt.load:.0f} | {mode} | {pt.m_main_kgs:.1f} | {pt.p_main_bar:.1f} | "
            f"{pt.t_fw_c:.1f} | {pt.q_steam_mw:.1f} | {pt.gross_mw:.1f} | "
            f"{pt.net_mw:.1f} | {pt.q_net_mw:+.1f} | {hours} | "
            f"{flag}{pt.t_salt_min_c:.1f} | {pt.eta_cycle_pct:.2f} |"
        )
    lines += [
        "",
        f"> 汽机侧净循环效率随负荷单调变化："
        f"{result.part_load[0].eta_cycle_pct:.1f} %（100 %）→ "
        f"{result.part_load[-1].eta_cycle_pct:.1f} %（{100 * result.part_load[-1].load:.0f} %）。"
        "分母是四台换热器吸热，所以储罐充/放引起的「多余热量」不会让它出现台阶。",
        "",
        "**盐侧温度**：冷罐温度由 IHX 钉死（反应堆恒定、充热盐流量恒定 ⇒ "
        f"`Q_IHX = m_chg·Δh` 只有唯一解），本算例恒为 **{result.part_load[0].t_cold_c:.1f} °C**，"
        "不随汽轮机负荷变化；随负荷下降的是**四台换热器出口的盐温**，最低点出现在"
        "省煤器出口（上表「最低盐温」列）。",
        "",
        f"告警阈值：低于 **{s.t_warn_freeze_c:.0f} °C** 报「凝固风险」，"
        f"低于 **{s.t_warn_margin_c:.0f} °C** 报「距凝固温度裕量不足」（见 `[salt]`）。",
        "",
        f"> 两个盐温判据不是一回事，不矛盾：第 9 节的设计校核用"
        f" **{s.t_freeze_c:.0f} + {s.freezing_margin_k:.0f} = "
        f"{s.t_freeze_c + s.freezing_margin_k:.0f} °C**（红线，低于即判不可行）；"
        f"这里的 {s.t_warn_freeze_c:.0f} / {s.t_warn_margin_c:.0f} °C 是"
        "**运行建议值**（低负荷时要不要干预）。前者判\"能不能这样设计\"，"
        "后者判\"运行中什么时候该告警\"。",
    ]
    flagged = [pt for pt in result.part_load if pt.salt_warnings]
    if flagged:
        lines += ["", "**本算例触发的盐侧告警**：", ""]
        for pt in flagged:
            for note in pt.salt_warnings:
                lines.append(f"- {note}")
    else:
        lines += [
            "",
            f"本算例全负荷段最低盐温 {min(pt.t_salt_min_c for pt in result.part_load):.1f} °C，"
            "未触发告警。",
        ]

    lines += [
        "",
        "> **满发可维持 h** 的定义：反应堆保持稳态（额定满功率）运行、汽轮机满发时，",
        "> 储罐里存的熔盐能让机组维持满发多少小时（= 储热容量 / 放热功率）。",
        "> 充热的行给的是相反的量——**充满时间**（多少小时后罐满，之后必须降堆或弃热）。",
        "",
        "> 储罐列：**负 = 放热**（补汽轮机差额），**正 = 充热**（堆的余量存进罐）。",
        "> 本表假设**反应堆恒定满功率**；若深调峰时同步降低堆功率，效率会高于表中值",
        "> （那就超出本工具的定位，属于 `C2N_HTGR_Salt` 的变工况计算）。",
        "",
        "估算误差约 ±1.5 个百分点，主要来自 `eta_ratio` 这条曲线；",
        "请用机组热力特性书或性能试验数据替换 `[partload].eta_ratio`。",
        "",
        "## 9. 校核",
        "",
        "| 校核项 | 数值 | 判据 | 结论 |",
        "|---|---:|---|---|",
    ]
    if result.boiler_aux_mw <= 0.0:
        estimate = 0.02 * result.gross_electric_mw
        delta = estimate - result.aux_circulator_mw - result.aux_salt_pump_mw
        lines += [
            f"> `boiler_aux_mw` 未填（= 0），上表只反映了氦风机与盐泵的新增。"
            f"若按毛电的 2 % 估算煤粉炉辅机（约 {estimate:.1f} MW），"
            f"净电变化约为 **{delta:+.1f} MW**"
            f"（{100.0 * delta / result.gross_electric_mw:+.1f} %）"
            "——这才是同口径的改造代价。",
            "",
        ]
    for check in result.checks:
        lines.append(
            f"| {check.name} | {check.value} | {check.criterion} | "
            f"{'通过' if check.ok else '**不通过**'} |"
        )

    lines += [
        "",
        "## 10. 最小改动清单",
        "",
        "| 类别 | 设备 | 说明 |",
        "|---|---|---|",
        f"| 新增 | 高温气冷堆模块 ×{r.n_modules} | 含一回路氦风机 |",
        f"| 新增 | IHX ×{r.n_modules} | 合计 {result.ihx.area_m2:,.0f} m²"
        f"（单台 {result.ihx.area_m2 / r.n_modules:,.0f} m²） |",
        f"| 新增 | 热盐罐 / 冷盐罐 | 共 {storage.total_m3:,.0f} m³ |",
        f"| 新增 | 盐泵 ×2 | 压损 {s.pump_dp_bar:.1f} bar，电耗 {result.aux_salt_pump_mw:.2f} MW |",
        "| **替换** | 过热器 / 蒸发器 / 省煤器 / 再热器 | 面积见第 2 节 |",
        "| **取消** | 煤粉炉及其辅机（送/引风机、磨煤机等） | 自用电约"
        f" {result.boiler_aux_mw:.1f} MW |",
        "| 不改动 | 汽轮机本体、回热系统、凝汽器、发电机 | 边界条件由业主给定 |",
        "",
        "## 11. 假设与局限",
        "",
        "1. 汽轮机侧完全不动，只以四台换热器的汽水参数作为边界；因此**不校验**"
        "汽轮机通流能力与末级湿度（那需要机组本体数据）。",
        "2. **同时支持亚临界汽包炉与超临界直流炉**：机型由 `[plant].turbine_class`"
        "（或主蒸汽压力）判定。亚临界按饱和温度分段；超临界没有饱和线，按水冷壁"
        "出口温度与拟临界温度分段——**该温度是业主给的边界，不是模型预测的**。",
        "3. 四台换热器按逆流、沿程积分定 kA，再按给定 U 折算面积；U 是经验值，"
        "面积只用于量级判断，不能替代换热器选型计算。",
        "4. 未建模减温喷水：若机组用过热器喷水，`Q_sh` 会偏大，请把喷水量并入给水流量"
        "或单独提供修正。",
        "5. 储罐按**完全混合、绝热**定容（真实分层会略微减小所需容积，差 < 2 %），"
        "储热时长不含散热损失；储热只能放、不能充的问题见第 5 节。",
        f"6. {s.spec.label}的物性为工程拟合式，不用于冻结风险与详细传热计算。",
    ]
    if not s.spec.commercial:
        lines.append(f"7. **盐种是假设**：{s.spec.note}")
        lines.append("8. 不含经济性：投资、年发电量、回本年限均未建模。")
    else:
        lines.append("7. 不含经济性：投资、年发电量、回本年限均未建模。")
    lines.append("")
    if result.warnings:
        lines.insert(6, "> **输入提示**：" + "；".join(result.warnings))
        lines.insert(7, "")

    path = Path(path)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
