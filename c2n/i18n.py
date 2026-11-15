"""界面与诊断文案的双语字典。

为什么要有这一层
----------------
工具是给两边人用的：业主看中文，外方/合作方看英文。把中文句子散落在 20 个函数
里，做"中英切换"就只能靠改 200 处 f-string；而且**数字与结论必须完全一致**，
不能出现"中文版说超临界、英文版说亚临界"的分裂。

所以规矩是：

1. 所有**面向用户**的句子都从这里取（``tr("key", **参数)``），函数里不再写中文；
2. 语言是**请求级**的（``contextvars``），不是全局开关——一个线程算中文、另一个
   线程算英文互不影响；
3. 数字、单位、专业标识符（``plant.drum_p_bar``、``SH``、``kPa``）不进字典，
   它们本来就与语言无关。

默认语言恒为中文，所以既有测试与 CLI 行为不变。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

ZH = "zh"
EN = "en"
LANGUAGES = (ZH, EN)

_current: ContextVar[str] = ContextVar("c2n_language", default=ZH)

#: 键 -> (中文, English)。占位符用 ``str.format`` 语法。
_CATALOG: dict[str, tuple[str, str]] = {
    # =====================================================================
    # 配置层：错误（拦住计算）
    # =====================================================================
    "err.prefix": ("配置无效：", "Invalid configuration:"),
    "err.positive": ("{name} 必须 > 0", "{name} must be > 0"),
    "err.non_negative": ("{name} 必须 >= 0", "{name} must be >= 0"),
    "err.open_unit": ("{name} 必须在 (0, 1) 内", "{name} must be within (0, 1)"),
    "err.half_open_unit": (
        "{name} 必须在 (0, 1] 内", "{name} must be within (0, 1]"
    ),
    "err.gross_basis": (
        'plant.gross_basis 必须是 "generator" 或 "shaft"',
        'plant.gross_basis must be "generator" or "shaft"',
    ),
    "err.feedwater_above_main": (
        "plant.feedwater_t_c 必须低于主蒸汽温度",
        "plant.feedwater_t_c must be below the main steam temperature",
    ),
    "err.efficiencies": (
        "发电机/机械效率必须在 (0, 1] 内",
        "generator/mechanical efficiency must be within (0, 1]",
    ),
    "err.reheater_ratio": (
        "plant.reheater_pressure_ratio 必须在 [0.8, 1.0) 内",
        "plant.reheater_pressure_ratio must be within [0.8, 1.0)",
    ),
    "err.sc_missing": (
        "超临界机组需要 boiler_p_bar / wall_outlet_t_c / "
        "economizer_outlet_t_c 三个字段",
        "supercritical units need boiler_p_bar / wall_outlet_t_c / "
        "economizer_outlet_t_c",
    ),
    "err.sc_pressure_below_critical": (
        "选择了超临界（{grade}），但锅炉压力 {p:.1f} bar 不高于临界压力 "
        "{pc:.1f} bar：超临界没有饱和线，分段无定义。"
        "请把 plant.turbine_class 改为 'subcritical'（或 'auto'）",
        "Turbine class is {grade}, but the boiler pressure {p:.1f} bar does not "
        "exceed the critical pressure {pc:.1f} bar. A supercritical unit has no "
        "saturation line, so the section boundaries are undefined. Set "
        "plant.turbine_class to 'subcritical' (or 'auto').",
    ),
    "err.sc_temp_order": (
        "温度必须满足 给水 < 省煤器出口 < 水冷壁出口 < 主蒸汽；"
        "当前 {fw:.1f} / {econ:.1f} / {wall:.1f} / {main:.1f} °C",
        "Temperatures must satisfy feedwater < economiser outlet < furnace-wall "
        "outlet < main steam; got {fw:.1f} / {econ:.1f} / {wall:.1f} / "
        "{main:.1f} °C",
    ),
    "err.boiler_p_le_main": (
        "plant.boiler_p_bar 必须高于 plant.main_steam_p_bar（过热器需要压降）",
        "plant.boiler_p_bar must exceed plant.main_steam_p_bar "
        "(the superheater needs a pressure drop)",
    ),
    "err.sub_main_above_critical": (
        "主蒸汽压力 {p:.1f} bar 已达水的临界压力 {pc:.1f} bar，但机型选了亚临界："
        "亚临界靠饱和线分段，压到这里就没有饱和线了。请把 plant.turbine_class "
        "改为 'supercritical' / 'ultra_supercritical'（或 'auto'）",
        "Main steam pressure {p:.1f} bar has reached the critical pressure "
        "{pc:.1f} bar, but the turbine class is subcritical. A subcritical unit "
        "is divided at the saturation line, which no longer exists here. Set "
        "plant.turbine_class to 'supercritical' / 'ultra_supercritical' "
        "(or 'auto').",
    ),
    "err.sub_missing": (
        "亚临界机组需要 drum_p_bar / economizer_subcooling_k",
        "subcritical units need drum_p_bar / economizer_subcooling_k",
    ),
    "err.drum_above_critical": (
        "选择了亚临界，但汽包压力 {p:.1f} bar 已达到临界压力 {pc:.1f} bar："
        "超临界机组没有汽包、没有饱和线。请把 plant.turbine_class 改为 "
        "'supercritical'（或 'auto'）",
        "Turbine class is subcritical, but the drum pressure {p:.1f} bar has "
        "reached the critical pressure {pc:.1f} bar. A supercritical unit has no "
        "drum and no saturation line. Set plant.turbine_class to 'supercritical' "
        "(or 'auto').",
    ),
    "err.drum_le_main": (
        "plant.drum_p_bar 必须高于 plant.main_steam_p_bar（过热器需要压降）",
        "plant.drum_p_bar must exceed plant.main_steam_p_bar "
        "(the superheater needs a pressure drop)",
    ),
    "err.subcooling": (
        "plant.economizer_subcooling_k 必须 > 0：过冷度为 0 时省煤器出口正好落在"
        "饱和线上，模型无法确定分界。请给 ≥ 2 K。",
        "plant.economizer_subcooling_k must be > 0: with zero subcooling the "
        "economiser outlet sits exactly on the saturation line and the section "
        "boundary is undefined. Use at least 2 K.",
    ),
    "err.reactor_t_hot": (
        "reactor.t_hot_c 必须高于 reactor.t_ihx_out_c",
        "reactor.t_hot_c must exceed reactor.t_ihx_out_c",
    ),
    "err.salt_fluid_unresolved": (
        "[salt].fluid 未解析（应为 'auto' 之外的具体盐种）",
        "[salt].fluid is unresolved (expected a concrete fluid, not 'auto')",
    ),
    "err.salt_hot_le_ihx": (
        "salt.t_hot_c 必须高于 reactor.t_ihx_out_c（否则 IHX 无法冷却氦气）",
        "salt.t_hot_c must exceed reactor.t_ihx_out_c "
        "(otherwise the IHX cannot cool the helium)",
    ),
    "err.salt_hot_le_main": (
        "热盐 {salt:.0f} °C 必须高于主蒸汽 {main:.0f} °C（过热器需要传热温差）",
        "Hot salt {salt:.0f} °C must exceed main steam {main:.0f} °C "
        "(the superheater needs a driving temperature difference)",
    ),
    "err.min_pinch": ("salt.min_pinch_k 必须 > 0", "salt.min_pinch_k must be > 0"),
    "err.ua_margin": ("salt.ua_margin 必须 >= 1", "salt.ua_margin must be >= 1"),
    "err.warn_order": (
        "salt.t_warn_freeze_c 必须低于 salt.t_warn_margin_c",
        "salt.t_warn_freeze_c must be below salt.t_warn_margin_c",
    ),
    "err.storage_hours": ("storage.hours 必须 > 0", "storage.hours must be > 0"),
    "err.tube_geometry": (
        "sizing.tube_od_mm 必须 > 0 且 tube_wall_mm < od/2",
        "sizing.tube_od_mm must be > 0 and tube_wall_mm < od/2",
    ),
    "err.pitch_ratio": (
        "sizing.pitch_ratio 必须 >= 1.05（否则管子相碰）",
        "sizing.pitch_ratio must be >= 1.05 (otherwise tubes touch)",
    ),
    "err.max_shell_id": (
        "sizing.max_shell_id_m 相对 shell_clearance_m 太小",
        "sizing.max_shell_id_m is too small for sizing.shell_clearance_m",
    ),
    "err.ullage": (
        "sizing.ullage 必须在 [0, 0.5) 内",
        "sizing.ullage must be within [0, 0.5)",
    ),
    "err.partload_empty": (
        "partload.load_points 不能为空",
        "partload.load_points must not be empty",
    ),
    "err.partload_len": (
        "partload.eta_ratio 有 {got} 项，应为 {want} 项",
        "partload.eta_ratio has {got} entries, expected {want}",
    ),
    "err.partload_fw_len": (
        "partload.feedwater_t_c 有 {got} 项，应为 {want} 项或空",
        "partload.feedwater_t_c has {got} entries, expected {want} or empty",
    ),
    "err.partload_point": (
        "partload.load_points[{i}] 必须在 (0, 1] 内",
        "partload.load_points[{i}] must be within (0, 1]",
    ),
    "err.partload_desc": (
        "partload.load_points 必须严格递减",
        "partload.load_points must be strictly decreasing",
    ),
    "err.partload_ratio": (
        "partload.eta_ratio[{i}] 必须在 (0.2, 1.05] 内",
        "partload.eta_ratio[{i}] must be within (0.2, 1.05]",
    ),
    "err.partload_monotonic": (
        "partload.eta_ratio 不能随负荷下降而上升",
        "partload.eta_ratio must not increase as load drops",
    ),
    "err.sliding_exponent": (
        "partload.sliding_exponent 必须在 [0.5, 1.5] 内",
        "partload.sliding_exponent must be within [0.5, 1.5]",
    ),
    "err.sliding_floor": (
        "partload.sliding_floor 必须在 [0.3, 1.0] 内",
        "partload.sliding_floor must be within [0.3, 1.0]",
    ),
    "err.turbine_aux_fraction": (
        "partload.turbine_aux_fixed_fraction 必须在 [0, 1] 内",
        "partload.turbine_aux_fixed_fraction must be within [0, 1]",
    ),
    "err.unknown_class": (
        "validate() 无法判定机组类型：请给 turbine_class 或压力",
        "validate() cannot determine the turbine class: give turbine_class or "
        "a pressure",
    ),
    "err.missing_sc_fields": (
        "超临界机组缺少 boiler_p_bar / wall_outlet_t_c",
        "supercritical unit is missing boiler_p_bar / wall_outlet_t_c",
    ),
    "err.missing_sc_econ": (
        "超临界机组缺少 economizer_outlet_t_c",
        "supercritical unit is missing economizer_outlet_t_c",
    ),
    "err.missing_sub_fields": (
        "亚临界机组缺少 drum_p_bar / economizer_subcooling_k",
        "subcritical unit is missing drum_p_bar / economizer_subcooling_k",
    ),
    "err.no_module_count": (
        "在 64 个模块内找不到能覆盖 {q:.1f} MW 的配置，"
        "请增大 reactor.module_power_mwth 或减少汽轮机热负荷",
        "No module count up to 64 can cover {q:.1f} MW; increase "
        "reactor.module_power_mwth or reduce the turbine heat demand",
    ),
    # ---- 配置层：解析阶段的假设 ----
    "note.infer_overall": (
        "未给汽包压力也未给锅炉压力，且主蒸汽压力低于临界压力：按亚临界处理，"
        "并按主蒸汽压力 / 0.90 反推汽包压力",
        "No drum pressure and no boiler pressure given, and the main steam "
        "pressure is subcritical: treating the unit as subcritical and deriving "
        "the drum pressure as main steam pressure / 0.90",
    ),
    "note.sub_drum": (
        "未给汽包压力：按主蒸汽压力 / 0.90 取 {drum:.1f} bar"
        "（相当于过热器压损 10 %），请用热力特性书核对",
        "Drum pressure not given: using main steam pressure / 0.90 = "
        "{drum:.1f} bar (10 % superheater drop). Check it against the boiler "
        "heat-balance sheet.",
    ),
    "note.subcooling": (
        "未给省煤器出口过冷度：按 {sub:.1f} K 计算（决定省煤器/蒸发器分界）",
        "Economiser outlet subcooling not given: using {sub:.1f} K (it sets the "
        "economiser/evaporator boundary)",
    ),
    "note.sc_boiler": (
        "未给水冷壁出口压力：按主蒸汽压力 × 1.05 取 {p:.1f} bar"
        "（相当于过热器压损 5 %），请用热力特性书核对",
        "Furnace-wall outlet pressure not given: using main steam pressure × "
        "1.05 = {p:.1f} bar (5 % superheater drop). Check it against the boiler "
        "heat-balance sheet.",
    ),
    "note.sc_wall": (
        "未给水冷壁出口温度：按 {t:.0f} °C 计算（典型 430~470 °C），"
        "该值直接决定蒸发段/过热段的分界与面积，**必须**用热力特性书核对",
        "Furnace-wall outlet temperature not given: using {t:.0f} °C (typical "
        "430–470 °C). This value sets the evaporator/superheater boundary and "
        "the heat-transfer area, so it **must** be checked against the boiler "
        "heat-balance sheet.",
    ),
    "note.sc_econ": (
        "未给省煤器出口温度：按给水温度 + 42 K 取 {t:.0f} °C，请用热力特性书核对",
        "Economiser outlet temperature not given: using feedwater + 42 K = "
        "{t:.0f} °C. Check it against the boiler heat-balance sheet.",
    ),
    "note.salt_raised": (
        "热罐温度 {old:.0f} °C 不高于主蒸汽 {main:.0f} °C，过热器没有传热温差："
        "已按主蒸汽 + 20 K 取 {new:.0f} °C，请按 {salt}的分解温度核对",
        "Hot-tank temperature {old:.0f} °C is not above main steam {main:.0f} °C, "
        "leaving no driving difference in the superheater: raised to main steam "
        "+ 20 K = {new:.0f} °C. Check it against the decomposition limit of "
        "{salt}.",
    ),
    # ---- 配置层：软提示 ----
    "warn.salt_noncommercial": (
        "盐种为「{label}」。{note}",
        "Salt medium: {label}. {note}",
    ),
    "warn.salt_above": (
        "热盐 {t:.0f} °C 高于「{salt}」的分解温度 {limit:.0f} °C："
        "方案照算，但盐会变质，需换盐或降参",
        "Hot salt {t:.0f} °C is above the decomposition limit {limit:.0f} °C of "
        "{salt}. The case is still computed, but the salt degrades: change the "
        "salt or reduce the parameters.",
    ),
    "warn.salt_margin": (
        "热盐 {t:.0f} °C 距「{salt}」分解温度 {limit:.0f} °C 不足 15 K",
        "Hot salt {t:.0f} °C is within 15 K of the decomposition limit "
        "{limit:.0f} °C of {salt}",
    ),
    "warn.main_close_salt": (
        "主蒸汽 {main:.0f} °C 与热盐 {salt:.0f} °C 相差不足 15 K，过热器面积会非常大",
        "Main steam {main:.0f} °C is within 15 K of hot salt {salt:.0f} °C: the "
        "superheater area becomes very large",
    ),
    "warn.usc_low_temp": (
        "机组类型为{grade}，但主蒸汽温度只有 {t:.0f} °C："
        "常见超超临界为 600 °C / 620 °C，请确认选型",
        "Turbine class is {grade}, but main steam is only {t:.0f} °C. Typical "
        "ultra-supercritical units run at 600 °C / 620 °C; confirm the rating.",
    ),
    "warn.drum_high": (
        "汽包压力 {p:.0f} bar 偏高（接近临界），请确认机组类型",
        "Drum pressure {p:.0f} bar is high (close to critical); confirm the unit "
        "type",
    ),
    "warn.fw_low_drum": (
        "给水压力 {fw:.0f} bar 不高于汽包压力 {drum:.0f} bar + 5 bar；"
        "实机给水泵出口通常比汽包高 10~20 bar，否则省煤器内没有流动压头",
        "Feedwater pressure {fw:.0f} bar is not above drum pressure {drum:.0f} "
        "bar + 5 bar. In practice the feed pump discharge is 10–20 bar above the "
        "drum, otherwise the economiser has no flow head.",
    ),
    "warn.fw_low_boiler": (
        "给水压力 {fw:.0f} bar 比锅炉压力 {boiler:.0f} bar 高不到 20 bar；"
        "超临界给水泵出口通常 30~32 MPa",
        "Feedwater pressure {fw:.0f} bar is less than 20 bar above boiler "
        "pressure {boiler:.0f} bar. Supercritical feed pumps usually discharge at "
        "30–32 MPa.",
    ),
    "warn.main_low": (
        "主蒸汽压力偏低（< 90 bar），请确认机组等级",
        "Main steam pressure is low (< 90 bar); confirm the unit rating",
    ),
    "warn.pinch_low": (
        "最小端差 < 5 K，对大型盐-汽换热器偏激进",
        "Minimum pinch < 5 K is aggressive for a large salt-to-steam exchanger",
    ),
    "warn.boiler_aux_zero": (
        "未填 plant.boiler_aux_mw：净电对比不含\"煤粉炉辅机消失\"这笔收益",
        "plant.boiler_aux_mw is not set: the net-power comparison omits the "
        "credit for removing the coal-boiler auxiliaries",
    ),
    "warn.turbine_aux_zero": (
        "未填 plant.turbine_island_aux_mw：报告的\"净电\"没有扣汽轮机岛辅机，"
        "绝对值偏高；改造前后净电的变化量不受影响",
        "plant.turbine_island_aux_mw is not set: the reported net power does not "
        "deduct turbine-island auxiliaries, so the absolute value is high. The "
        "before/after change is unaffected.",
    ),
    "warn.reheat_gt_main": (
        "再热蒸汽流量 {reheat:.0f} kg/s 大于主蒸汽流量 {main:.0f} kg/s："
        "通常会小于（高压缸抽汽），请核对",
        "Reheat flow {reheat:.0f} kg/s exceeds main steam flow {main:.0f} kg/s. "
        "It is normally smaller (HP extraction); please check.",
    ),
    # =====================================================================
    # 求解层
    # =====================================================================
    "design.warn.reactor_covers": (
        "堆热功率 {ihx:.0f} MW ≥ 汽轮机等效热功率 {steam:.0f} MW："
        "额定工况下储罐处于**充热**状态，储热无法用于顶峰发电。"
        "若要让储热发挥作用，反应堆热功率应小于汽轮机等效热功率"
        "（可减到 {n} 个模块，或换更大的汽轮机）。",
        "Reactor heat {ihx:.0f} MW ≥ turbine heat demand {steam:.0f} MW: at "
        "rated load the store is **charging**, so it cannot be used to peak. "
        "To make the store useful, reactor heat should stay below the turbine "
        "heat demand (drop to {n} modules, or use a larger turbine).",
    ),
    "design.warn.implied_eta": (
        "输入可能不自洽：毛电 {gross:.1f} MW / 四台吸热 {steam:.1f} MW = "
        "{eta:.2f} %，超出 {p:.0f} bar 压力等级的常见范围 {lo:.0f}~{hi:.0f} %。"
        "请核对 gross_power_mw 与主蒸汽流量是否来自同一工况。",
        "The input may be inconsistent: gross {gross:.1f} MW / heat absorbed "
        "{steam:.1f} MW = {eta:.2f} %, outside the usual {lo:.0f}–{hi:.0f} % "
        "band for a {p:.0f} bar unit. Check that gross_power_mw and the main "
        "steam flow come from the same load case.",
    ),
    "design.warn.storage_mass": (
        "储热量级异常：{salt} {mass:,.0f} t（{cap:,.0f} MWh），"
        "常见工程规模在 1 万 t 以内。请确认 reactor.n_modules 与 storage.hours。",
        "Unusual storage size: {salt} {mass:,.0f} t ({cap:,.0f} MWh); typical "
        "projects stay below 10,000 t. Check reactor.n_modules and "
        "storage.hours.",
    ),
    "design.check.pinch.name": (
        "四台换热器最小端差（沿程）",
        "Minimum pinch of the four exchangers (along the path)",
    ),
    "design.check.pinch.value": (
        "{v:.2f} K @ {binding}",
        "{v:.2f} K @ {binding}",
    ),
    "design.check.pinch.criterion": ("≥ {v:.1f} K", "≥ {v:.1f} K"),
    "design.check.freeze.name": (
        "冷盐与凝固点裕量",
        "Cold-salt margin above freezing",
    ),
    "design.check.freeze.value": ("{v:.1f} K", "{v:.1f} K"),
    "design.check.freeze.criterion": (
        "≥ {v:.1f} K（即 ≥ {t:.0f} °C）",
        "≥ {v:.1f} K (i.e. ≥ {t:.0f} °C)",
    ),
    "design.check.decompose.name": (
        "热盐分解温度",
        "Hot-salt decomposition limit",
    ),
    "design.check.decompose.value": ("{v:.0f} °C", "{v:.0f} °C"),
    "design.check.decompose.criterion": (
        "≤ {v:.0f} °C（{salt}）",
        "≤ {v:.0f} °C ({salt})",
    ),
    "design.check.ihx.name": ("IHX 冷端温差", "IHX cold-end approach"),
    "design.check.ihx.value": ("{v:.1f} K", "{v:.1f} K"),
    "design.check.ihx.criterion": ("≥ {v:.1f} K", "≥ {v:.1f} K"),
    "design.check.crossing.name": (
        "各换热段沿程温差",
        "Temperature difference along each exchanger",
    ),
    "design.check.crossing.value_ok": ("全部为正", "all positive"),
    "design.check.crossing.value_bad": ("存在温度交叉", "temperature cross-over"),
    "design.check.crossing.criterion": ("无温度交叉", "no temperature cross-over"),
    "design.check.closure.name": ("100 % 工况热平衡", "Energy balance at 100 % load"),
    "design.check.closure.value": (
        "{ihx:.2f} = {steam:.2f} {net:+.2f}",
        "{ihx:.2f} = {steam:.2f} {net:+.2f}",
    ),
    "design.check.closure.criterion": (
        "IHX = 汽侧吸热 + 储罐放热",
        "IHX = steam-side heat + storage discharge",
    ),
    "design.check.net.name": ("净电为正", "Net power is positive"),
    "design.check.net.value": ("{v:.2f} MW", "{v:.2f} MW"),
    "design.check.net.criterion": ("> 0", "> 0"),
    "design.check.message": (
        "{name}：{value}（判据 {criterion}）",
        "{name}: {value} (criterion {criterion})",
    ),
    "comparison.gross": ("毛电（发电机端）", "Gross power (generator terminals)"),
    "comparison.turbine_aux": ("− 汽轮机岛辅机", "− Turbine-island auxiliaries"),
    "comparison.boiler_aux": ("− 煤粉炉辅机", "− Coal-boiler auxiliaries"),
    "comparison.new_aux": ("− 氦风机 + 盐泵", "− Circulators + salt pumps"),
    "comparison.net": ("净电", "Net power"),
    "comparison.delta": ("净电变化", "Net power change"),
    # =====================================================================
    # 汽水侧 / 盐侧
    # =====================================================================
    "section.superheater": ("过热器", "Superheater"),
    "section.evaporator": ("蒸发器", "Evaporator"),
    "section.evaporator_once_through": ("蒸发段（水冷壁）", "Evaporator (furnace wall)"),
    "section.economizer": ("省煤器", "Economiser"),
    "section.reheater": ("再热器", "Reheater"),
    "section.ihx": ("氦-盐中间换热器", "Helium-to-salt IHX"),
    # ---- 状态点 ----
    "state.fluid.helium": ("氦气", "helium"),
    "state.fluid.water": ("水", "water"),
    "state.fluid.steam": ("蒸汽", "steam"),
    "state.fluid.supercritical_water": ("超临界水", "supercritical water"),
    "state.H1": ("堆芯出口", "Core outlet"),
    "state.H2": ("IHX 氦侧出口（风机进口）", "IHX helium outlet (circulator inlet)"),
    "state.H3": ("氦风机出口（堆芯进口）", "Circulator outlet (core inlet)"),
    "state.S1": ("IHX 盐侧出口", "IHX salt-side outlet"),
    "state.S2": ("热罐出口 / 热盐总管", "Hot-tank outlet / hot-salt header"),
    "state.S3": ("过热器出口", "Superheater outlet"),
    "state.S4": ("蒸发器出口", "Evaporator outlet"),
    "state.S5": ("省煤器出口", "Economiser outlet"),
    "state.S6": ("再热器出口", "Reheater outlet"),
    "state.S7": ("冷盐总管（混合）", "Cold-salt header (mixed)"),
    "state.S8": ("冷罐出口 → IHX", "Cold-tank outlet → IHX"),
    "state.W1": ("给水（省煤器进口）", "Feedwater (economiser inlet)"),
    "state.W2": ("省煤器出口", "Economiser outlet"),
    "state.W3_sat": ("蒸发器出口（饱和蒸汽）", "Evaporator outlet (saturated steam)"),
    "state.W3_sc": ("水冷壁出口（蒸发段出口）", "Furnace-wall outlet (evaporator outlet)"),
    "state.W4": ("过热器出口（主蒸汽）", "Superheater outlet (main steam)"),
    "state.W5": ("冷再热（再热器进口）", "Cold reheat (reheater inlet)"),
    "state.W6": ("热再热（再热器出口）", "Hot reheat (reheater outlet)"),
    "state.note.charging": ("充热回路", "charging loop"),
    "state.note.discharge_start": ("放热回路起点", "discharge loop start"),
    "state.note.main_branch": ("主支路", "main branch"),
    "state.note.main_return": ("主支路回盐", "main-branch salt return"),
    "state.note.reheat_return": ("再热支路回盐", "reheat-branch salt return"),
    "state.note.charging_start": ("充热回路起点", "charging loop start"),
    "state.note.circulator_ratio": ("风机压比 {v:.4f}", "circulator pressure ratio {v:.4f}"),
    "state.note.circulator_work": ("风机轴功 {v:.1f} MW", "circulator shaft work {v:.1f} MW"),
    "state.note.split": (
        "主支路 {m1:.0f} + 再热支路 {m2:.0f} kg/s",
        "main {m1:.0f} + reheat {m2:.0f} kg/s",
    ),
    "state.note.subcooling": ("过冷 {v:.1f} K", "subcooled by {v:.1f} K"),
    "state.note.below_pc": (
        "拟临界 {t:.0f} °C 以下 {dt:.0f} K",
        "{dt:.0f} K below pseudo-critical {t:.0f} °C",
    ),
    "state.note.saturated_vapour": ("x = 1", "x = 1"),
    "state.note.no_phase_change": ("超临界、无相变", "supercritical, no phase change"),
    "err.h_main_below_sat": (
        "主蒸汽焓不高于饱和蒸汽焓：请检查主蒸汽温度是否高于饱和温度",
        "Main steam enthalpy is not above saturated-vapour enthalpy: check that "
        "main steam temperature exceeds the saturation temperature",
    ),
    "err.h_econ_above_sat": (
        "省煤器出口温度高于饱和温度：请减小过冷度",
        "Economiser outlet temperature is above saturation: reduce the "
        "subcooling",
    ),
    "err.h_main_below_wall": (
        "主蒸汽焓不高于水冷壁出口焓：请检查主蒸汽温度是否高于水冷壁出口温度",
        "Main steam enthalpy is not above furnace-wall outlet enthalpy: check "
        "that main steam temperature exceeds the furnace-wall outlet "
        "temperature",
    ),
    "err.wall_below_econ": (
        "水冷壁出口温度必须高于省煤器出口温度",
        "Furnace-wall outlet temperature must exceed the economiser outlet "
        "temperature",
    ),
    "err.econ_below_fw": (
        "省煤器出口焓不高于给水焓：请检查给水温度",
        "Economiser outlet enthalpy is not above feedwater enthalpy: check the "
        "feedwater temperature",
    ),
    "err.salt_pinch_unreachable": (
        "端差无法达到 {target:.1f} K：在冷盐 {hi:.0f} °C 时最大只能到 "
        "{achievable:.1f} K（受主蒸汽/再热温度与热盐温度之差限制）。"
        "请降低 salt.min_pinch_k 或提高 salt.t_hot_c",
        "The pinch cannot reach {target:.1f} K: at a cold-salt temperature of "
        "{hi:.0f} °C the best achievable value is {achievable:.1f} K, limited by "
        "the gap between main/reheat steam and hot salt. Lower salt.min_pinch_k "
        "or raise salt.t_hot_c.",
    ),
    "err.salt_bounds": (
        "冷盐温度下界 {lo:.1f} °C 已接近热盐温度 {hi:.1f} °C，无法在合理温差内运行",
        "The cold-salt lower bound {lo:.1f} °C is too close to the hot-salt "
        "temperature {hi:.1f} °C: no reasonable temperature difference is "
        "available",
    ),
    "err.salt_dh": (
        "盐的焓降不为正：请检查热盐与冷盐温度",
        "Salt enthalpy drop is not positive: check the hot and cold salt "
        "temperatures",
    ),
    "err.helium_dh": (
        "氦气在堆芯的焓升不为正：请检查氦温设定",
        "Helium enthalpy rise in the core is not positive: check the helium "
        "temperatures",
    ),
    # =====================================================================
    # 服务层（网站 / 契约）
    # =====================================================================
    "assess.mode_unsupported": (
        "本内核只支持 boiler_replacement（锅炉替代），收到 {mode!r}；"
        "完全替代/部分替代不在热工内核里",
        "This kernel only supports boiler_replacement; got {mode!r}. "
        "Full and partial replacement are not part of the thermal kernel.",
    ),
    "assess.unknown_section": (
        "未知的配置段 {sections}；应为 plant / reactor / salt / storage / "
        "sizing / partload",
        "Unknown config section(s) {sections}; expected plant / reactor / salt / "
        "storage / sizing / partload",
    ),
    "option.label": (
        "高温气冷堆 ×{n} + {salt}双罐储热 + {grade}汽轮机",
        "{n} × HTGR modules + {salt} two-tank storage + {grade} turbine",
    ),
    "feasibility.blocking": (
        "{name}：{value}（判据 {criterion}）",
        "{name}: {value} (criterion {criterion})",
    ),
    "narrative.headline": (
        "{n} 个 {module:.0f} MWth 模块（{core:.0f} MWth）可覆盖 {gross:.0f} MW "
        "{grade}机组的锅炉侧热量；需{salt} {salt_t:,.0f} t、"
        "换热面积 {area:,.0f} m²，净电 {net:.1f} MW"
        "（同口径变化 {delta:+.1f} MW）",
        "{n} × {module:.0f} MWth modules ({core:.0f} MWth) cover the boiler-side "
        "heat of the {gross:.0f} MW {grade} unit; requires {salt_t:,.0f} t of "
        "{salt} and {area:,.0f} m² of heat-transfer area; net power "
        "{net:.1f} MW (same-basis change {delta:+.1f} MW)",
    ),
    "narrative.storage_discharge": (
        "额定工况储罐净放热 {q:.0f} MW，{h:.0f} h 需 {cap:.0f} MWh",
        "At rated load the store discharges {q:.0f} MW; {h:.0f} h needs "
        "{cap:.0f} MWh",
    ),
    "narrative.storage_charge": (
        "额定工况堆有余量 {q:.0f} MW 充入储罐，{h:.0f} h 充满 {cap:.0f} MWh",
        "At rated load the reactor has {q:.0f} MW spare, charging the store in "
        "{h:.0f} h ({cap:.0f} MWh)",
    ),
    "narrative.pinch": (
        "四台换热器总热负荷 {q:.1f} MW，端差瓶颈 {binding}（{pinch:.1f} K）",
        "Total heat duty of the four exchangers {q:.1f} MW; binding pinch at "
        "{binding} ({pinch:.1f} K)",
    ),
    "narrative.sustainable": (
        "可持续负荷 {load:.1f} %；若要求长期满发需 {n} 个模块（{core:.0f} MWth）",
        "Sustainable load {load:.1f} %; continuous full output would need {n} "
        "modules ({core:.0f} MWth)",
    ),
    "assume.black_box": (
        "汽轮机本体按黑盒处理：只取四台换热器的汽水参数作为边界，"
        "不校验通流能力、末级湿度与胀差",
        "The turbine is treated as a black box: only the steam-side conditions "
        "of the four exchangers are used as the boundary. Flow capacity, last-"
        "stage wetness and differential expansion are not checked.",
    ),
    "assume.partload": (
        "变工况为估算（滑压 + 级组效率修正 + 给水温度表），不是逐点通流计算",
        "Part-load results are estimates (sliding pressure + stage-group "
        "efficiency correction + feedwater temperature table), not stage-by-"
        "stage flow calculations",
    ),
    # =====================================================================
    # 可下载的 Markdown 报告
    # =====================================================================
    "report.title": (
        "# C2N 锅炉侧替换技术评估：{name}",
        "# C2N boiler replacement — technical assessment: {name}",
    ),
    "report.case_id": ("算例编号", "Case ID"),
    "report.generated_at": ("生成时间", "Generated at"),
    "report.kernel": ("内核", "Kernel"),
    "report.model_card": ("模型卡", "Model card"),
    "report.input_hash": ("输入指纹", "Input hash"),
    "report.verdict": ("结论", "Verdict"),
    "report.unit_and_boundary": ("机组与边界", "Unit and boundary"),
    "report.unit": ("机组", "Unit"),
    "report.gross_net": (
        "毛电 {gross:.1f} MW，净电 {net:.1f} MW（同口径变化 {delta:+.1f} MW）",
        "Gross {gross:.1f} MW, net {net:.1f} MW (same-basis change "
        "{delta:+.1f} MW)",
    ),
    "report.main_steam": (
        "主蒸汽 {p:.1f} bar / {t:.0f} °C",
        "Main steam {p:.1f} bar / {t:.0f} °C",
    ),
    "report.salt": (
        "盐种：{fluid}，热罐 {hot:.1f} °C / 冷罐 {cold:.1f} °C",
        "Salt: {fluid}; hot tank {hot:.1f} °C / cold tank {cold:.1f} °C",
    ),
    "report.hx_title": ("四台换热器与 IHX", "The four exchangers and the IHX"),
    "report.storage_title": ("储热与盐量", "Thermal storage and salt inventory"),
    "report.storage": (
        "储热容量 {cap:.0f} MWh（{h:.1f} h，{mode}）",
        "Storage capacity {cap:.0f} MWh ({h:.1f} h, {mode})",
    ),
    "report.salt_inventory": (
        "{fluid} 活动盐量 {mass:,.0f} t，冷热罐总容积 {vol:,.0f} m³",
        "{fluid} active inventory {mass:,.0f} t; total tank volume "
        "{vol:,.0f} m³",
    ),
    "report.partload_title": ("变工况（50 %–100 %）", "Part load (50–100 %)"),
    "report.partload_note": (
        "> 变工况为**估算**：只引入滑压规律、级组效率修正与给水温度三条可标定"
        "关系，其余是精确热平衡。设计点与完整 TESPy 模型的偏差 < 0.5 %，"
        "变工况在低负荷处偏差可达 2~3 %（主要在辅机记账口径）。",
        "> Part-load results are **estimates**: only three calibratable relations "
        "are introduced (sliding pressure, stage-group efficiency, feedwater "
        "temperature); everything else is an exact heat balance. At the design "
        "point the deviation from the full TESPy model is below 0.5 %; at low "
        "load it reaches 2–3 %, mostly from auxiliary-power accounting.",
    ),
    "report.checks_title": ("校核", "Checks"),
    "report.assumptions_title": ("假设与提示", "Assumptions and notes"),
    "report.warnings_title": ("本次输入触发的提示", "Notes triggered by this input"),
    "report.disclaimer": (
        "本报告为**初步评估**（预可研深度），不构成设计依据；不含经济性。",
        "This report is a **preliminary assessment** (pre-feasibility depth). It "
        "is not a design basis and excludes economics.",
    ),
    "report.th.passed": ("通过", "pass"),
    "report.th.failed": ("**不通过**", "**fail**"),
    "report.h.item": ("项目", "Item"),
    "report.h.value": ("数值", "Value"),
    "report.h.criterion": ("判据", "Criterion"),
    "report.h.verdict": ("结论", "Verdict"),
    "report.h.device": ("设备", "Equipment"),
    "report.h.q": ("Q MW", "Q MW"),
    "report.h.ka": ("所需 kA MW/K", "kA required, MW/K"),
    "report.h.ka_sel": ("选型 kA MW/K", "kA selected, MW/K"),
    "report.h.area": ("面积 m²", "Area m²"),
    "report.h.u": ("U W/m²K", "U W/m²K"),
    "report.h.dtmin": ("沿程最小 ΔT K", "Min ΔT along path, K"),
    "report.h.load": ("负荷 %", "Load %"),
    "report.h.m": ("主蒸汽 kg/s", "Main steam kg/s"),
    "report.h.qsteam": ("汽侧吸热 MW", "Steam-side heat MW"),
    "report.h.gross": ("毛电 MW", "Gross MW"),
    "report.h.net": ("净电 MW", "Net MW"),
    "report.h.qnet": ("储罐 ±MW", "Storage ±MW"),
    "report.h.eta": ("效率 %", "Efficiency %"),
    "report.h.tsalt": ("最低盐温 °C", "Min salt °C"),
}

#: 允许在英文界面里出现的非 ASCII 符号（单位、数学符号）
_ALLOWED_SYMBOLS = set("≥≤×·÷–—°±µ³²") | {"\u00a0"}


def get_language() -> str:
    """当前请求/上下文使用的语言。"""

    return _current.get()


def set_language(lang: str) -> str:
    """设置当前上下文语言；未知语言直接报错（不要静默回落）。"""

    if lang not in LANGUAGES:
        raise ValueError(f"unsupported language {lang!r}; expected one of {LANGUAGES}")
    _current.set(lang)
    return lang


@contextmanager
def language(lang: str) -> Iterator[str]:
    """``with language("en"): ...`` —— 退出时恢复原语言。"""

    if lang not in LANGUAGES:
        raise ValueError(f"unsupported language {lang!r}; expected one of {LANGUAGES}")
    token = _current.set(lang)
    try:
        yield lang
    finally:
        _current.reset(token)


def tr(key: str, **kwargs: object) -> str:
    """取一条本地化文案。

    键不存在时**抛异常**而不是回落中文：漏翻译要立刻暴露，不能悄悄混着两种语言。
    """

    try:
        zh, en = _CATALOG[key]
    except KeyError:
        raise KeyError(f"missing i18n key {key!r}") from None
    template = en if _current.get() == EN else zh
    return template.format(**kwargs) if kwargs else template


def has_key(key: str) -> bool:
    return key in _CATALOG


def keys() -> tuple[str, ...]:
    return tuple(sorted(_CATALOG))


def contains_cjk(text: str) -> bool:
    """是否含中日韩文字（用于测试"英文界面不该漏中文"）。"""

    return any("\u4e00" <= ch <= "\u9fff" for ch in text)
