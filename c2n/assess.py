"""服务层：``assess()`` —— 输入 JSON 进，技术结果 JSON 出。

这一层是网站、CLI 与未来经济性模块的**唯一入口**，它做四件事：

1. **输入归一化**：接受与 TOML 同构的 JSON（``plant`` / ``reactor`` / ``salt`` /
   ``storage`` / ``sizing`` / ``partload``），用与 CLI 完全相同的
   ``apply_overrides → finalize`` 通道，所以网页与命令行不可能算出两个答案；
2. **自动选型**：由 ``turbine_class``（或压力）判定亚临界/超临界，调用同一个
   :func:`c2n.design.run_design`；
3. **契约化**：按 ``contracts/technical-result.schema.json`` 的
   ``TechnicalAssessment`` 组织输出（``technical`` 字段）；
4. **给界面留料**：把变工况曲线、状态点、四段换热器表、假设与提示放进
   ``details``（契约里放不下，也不该放——契约只承载"技术结果"本身）。

返回的信封::

    {
      "ok": true,
      "case_id": "...",
      "input": {...归一化后的输入...},
      "technical": {...符合 TechnicalAssessment 契约...},
      "details": {"sections": [], "part_load": [], "states": [],
                  "assumptions": [], "comparison": []}
    }

失败时（输入自相矛盾、物理上算不出来）抛 :class:`c2n.config.ConfigError`，
由调用方转成 HTTP 400 与可读的错误清单。
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import (
    DEFAULT_CONFIG,
    ConfigError,
    PlantConfig,
    apply_overrides,
    finalize,
)
from .design import DesignResult, run_design
from .i18n import tr
from .states import build_states
from .version import __version__

__all__ = [
    "KERNEL",
    "MODEL_CARD_ID",
    "assess",
    "build_envelope",
    "report_markdown",
]

KERNEL = "c2n-screening"
MODEL_CARD_ID = "MC-C2N-001"

#: 主机时区（报告与界面统一用北京时间；``computed_at`` 里带偏移量）
_TZ = timezone(timedelta(hours=8))

_SECTION_IDS = {
    "过热器": "SH",
    "蒸发器": "EV",
    "省煤器": "EC",
    "再热器": "RH",
    "IHX": "IHX",
}

_STORAGE_MODE = {"放热": "discharging", "充热": "charging", "平衡": "balanced"}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _num(value: float | None) -> float | None:
    """JSON 只认有限数：``inf`` / ``nan`` 一律变 ``null``。"""

    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _r(value: float | None, digits: int = 4) -> float | None:
    value = _num(value)
    return None if value is None else round(value, digits)


def _canonical(case: dict) -> str:
    return json.dumps(case, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def input_hash(case: dict) -> str:
    return "sha256:" + hashlib.sha256(_canonical(case).encode("utf-8")).hexdigest()


def _config_to_dict(cfg: PlantConfig) -> dict:
    """把解析后的配置写成可 JSON 化的字典（与 TOML 同构，便于页面回填）。"""

    def dump(obj: Any) -> dict:
        out: dict[str, Any] = {}
        for name in obj.__dataclass_fields__:  # noqa: SLF001 - 内部序列化
            if name == "notes":
                continue
            value = getattr(obj, name)
            out[name] = dump(value) if hasattr(value, "__dataclass_fields__") else value
        return out

    data = dump(cfg)
    data["salt"]["fluid"] = cfg.salt.fluid
    data["plant"]["turbine_class"] = cfg.plant.turbine_class
    data["plant"]["p_boiler_bar"] = cfg.plant.p_boiler_bar
    return data


def _load_case(case: dict | None) -> tuple[PlantConfig, list[str]]:
    """与 ``load_config`` 同一条通道：默认值 -> 覆盖 -> 判定 -> 校验。"""

    if case is None:
        cfg = finalize(DEFAULT_CONFIG)
        return cfg, []

    unknown = set(case) - {
        "plant",
        "reactor",
        "salt",
        "storage",
        "sizing",
        "partload",
    }
    if unknown:
        raise ConfigError(tr("assess.unknown_section", sections=sorted(unknown)))
    cfg = apply_overrides(DEFAULT_CONFIG, case)
    return finalize(cfg), []


# ---------------------------------------------------------------------------
# 契约片段
# ---------------------------------------------------------------------------
def _feasibility(result: DesignResult, warnings: list[str]) -> dict:
    failed = [c for c in result.checks if not c.ok]
    if failed:
        code = "OK"
        for check in failed:
            if check.id == "salt_decomposition":
                code = "FLUID_LIMIT"
                break
            if check.id in {"pinch", "ihx_cold_end", "temperature_crossing"}:
                code = "TEMPERATURE_MISMATCH"
                break
        return {
            "status": "infeasible",
            "code": code,
            "blocking_issues": [
                tr(
                    "feasibility.blocking",
                    name=c.name,
                    value=c.value,
                    criterion=c.criterion,
                )
                for c in failed
            ],
            "warnings": warnings,
        }
    return {
        "status": "marginal" if warnings else "feasible",
        "code": "OK",
        "blocking_issues": [],
        "warnings": warnings,
    }


def _hx_rows(result: DesignResult) -> list[dict]:
    steam = result.steam
    salt = result.cfg.salt
    rows: list[dict] = []
    for name, sec in result.salt_loop.sections.items():
        rows.append(
            {
                "id": _SECTION_IDS[name],
                "label": steam.label(name),
                "q_mw": _r(sec.q_mw),
                "ka_mw_k": _r(sec.ka_mw_k),
                "ka_selected_mw_k": _r(sec.ka_selected_mw_k),
                "area_m2": _r(sec.area_m2, 1),
                "u_w_m2k": _r(salt.u_for(name), 1),
                "dt_min_k": _r(sec.dt_min_k),
                "dt_hot_end_k": _r(sec.dt_hot_end_k),
                "dt_cold_end_k": _r(sec.dt_cold_end_k),
                "lmtd_k": _r(sec.lmtd_k),
            }
        )
    ihx = result.ihx
    rows.append(
        {
            "id": "IHX",
            "label": tr("section.ihx"),
            "q_mw": _r(ihx.q_mw),
            "ka_mw_k": _r(ihx.ka_mw_k),
            "ka_selected_mw_k": _r(ihx.ka_selected_mw_k),
            "area_m2": _r(ihx.area_m2, 1),
            "u_w_m2k": _r(salt.u_he_salt_w_m2k, 1),
            "dt_min_k": _r(ihx.dt_min_k),
            "dt_hot_end_k": _r(ihx.dt_hot_end_k),
            "dt_cold_end_k": _r(ihx.dt_cold_end_k),
            "lmtd_k": _r(ihx.lmtd_k),
        }
    )
    return rows


def _part_load_rows(result: DesignResult) -> list[dict]:
    rows: list[dict] = []
    for pt in result.part_load:
        rows.append(
            {
                "load_pct": round(100.0 * pt.load, 1),
                "m_main_kgs": _r(pt.m_main_kgs, 2),
                "p_main_bar": _r(pt.p_main_bar, 2),
                "p_boiler_bar": _r(pt.p_boiler_bar, 2),
                "t_fw_c": _r(pt.t_fw_c, 1),
                "q_steam_mw": _r(pt.q_steam_mw, 1),
                "gross_mw": _r(pt.gross_mw, 2),
                "aux_turbine_mw": _r(pt.aux_turbine_mw, 2),
                "aux_circulator_mw": _r(pt.aux_circulator_mw, 2),
                "aux_pump_mw": _r(pt.aux_pump_mw, 2),
                "net_mw": _r(pt.net_mw, 2),
                "q_net_mw": _r(pt.q_net_mw, 2),
                "storage_hours": _r(pt.storage_hours, 2),
                "eta_cycle_pct": _r(pt.eta_cycle_pct, 2),
                "sliding": bool(pt.sliding),
                "t_salt_min_c": _r(pt.t_salt_min_c, 2),
                "salt_warnings": list(pt.salt_warnings),
            }
        )
    return rows


def _narrative(result: DesignResult) -> dict:
    p = result.cfg.plant
    n = result.reactor.n_modules
    q_net = result.q_net_mw
    if q_net < 0:
        storage_note = tr(
            "narrative.storage_discharge",
            q=abs(q_net),
            h=result.storage.hours,
            cap=result.storage.capacity_mwh,
        )
    else:
        storage_note = tr(
            "narrative.storage_charge",
            q=q_net,
            h=result.storage.hours,
            cap=result.storage.capacity_mwh,
        )
    headline = tr(
        "narrative.headline",
        n=n,
        module=result.cfg.reactor.module_power_mwth,
        core=result.reactor.q_core_mw,
        gross=p.gross_electric_mw,
        grade=p.grade_label,
        salt=result.cfg.salt.spec.display_short,
        salt_t=result.sizing.salt_total_t,
        area=result.sizing.area_total_m2,
        net=result.net_power_mw,
        delta=result.net_power_delta_mw,
    )
    details = [
        storage_note,
        tr(
            "narrative.pinch",
            q=result.steam.q_steam_mw,
            binding=result.steam.label(result.salt_loop.binding),
            pinch=result.salt_loop.min_pinch_k,
        ),
        tr(
            "narrative.sustainable",
            load=result.sustainable_load_pct,
            n=result.modules_self_sufficient,
            core=result.q_core_self_sufficient_mw,
        ),
    ]
    return {"headline": headline, "details": details}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def build_envelope(
    result: DesignResult,
    *,
    case: dict | None,
    case_id: str,
    runtime_ms: int,
    warnings: list[str],
) -> dict:
    """把 :class:`DesignResult` 装配成"契约 + 界面素材"的信封。"""

    p = result.cfg.plant
    salt = result.cfg.salt
    spec = salt.spec
    now = datetime.now(_TZ).isoformat(timespec="seconds")
    provenance = {
        "kernel": KERNEL,
        "kernel_version": __version__,
        "model_card_id": MODEL_CARD_ID,
        "input_hash": input_hash(case or {}),
        "computed_at": now,
        "runtime_ms": int(runtime_ms),
    }

    option = {
        "option_id": f"htgr-{spec.key}-{result.reactor.n_modules}mod",
        "label": tr(
            "option.label",
            n=result.reactor.n_modules,
            salt=spec.display_short,
            grade=p.grade_label,
        ),
        "reactor": {
            "technology": "HTGR",
            "n_modules": result.reactor.n_modules,
        },
        "intermediate_loop": {
            "fluid": spec.key,
            "t_hot_c": _r(result.salt_loop.t_hot_c, 2),
            "t_cold_c": _r(result.salt_loop.t_cold_c, 2),
        },
        "feasibility": _feasibility(result, warnings),
        "sizing": {
            "n_modules": result.reactor.n_modules,
            "module_power_mwth": _r(result.cfg.reactor.module_power_mwth, 1),
            "q_core_mwth": _r(result.reactor.q_core_mw, 1),
            "q_ihx_mw": _r(result.reactor.q_ihx_mw, 2),
            "salt": {
                "fluid": spec.key,
                "mass_t": _r(result.storage.mass_t, 1),
                "volume_m3": _r(result.storage.total_m3, 1),
                "m_discharge_kgs": _r(result.salt_loop.m_discharge_kgs, 2),
                "m_charge_kgs": _r(result.salt_loop.m_charge_kgs, 2),
                "t_hot_c": _r(result.salt_loop.t_hot_c, 2),
                "t_cold_c": _r(result.salt_loop.t_cold_c, 2),
                "min_pinch_k": _r(result.salt_loop.min_pinch_k, 3),
                "binding_section": _SECTION_IDS.get(
                    result.salt_loop.binding, result.salt_loop.binding
                ),
            },
            "storage": {
                "capacity_mwh": _r(result.storage.capacity_mwh, 1),
                "hours": _r(result.storage.hours, 2),
                "mode": _STORAGE_MODE.get(result.mode, "balanced"),
                "net_power_mw": _r(result.q_net_mw, 2),
            },
            "hx": [
                {
                    "id": row["id"],
                    "q_mw": row["q_mw"],
                    "ka_mw_k": row["ka_mw_k"],
                    "ka_selected_mw_k": row["ka_selected_mw_k"],
                    "area_m2": row["area_m2"],
                    "u_w_m2k": row["u_w_m2k"],
                    "dt_min_k": row["dt_min_k"],
                    "dt_hot_end_k": row["dt_hot_end_k"],
                    "dt_cold_end_k": row["dt_cold_end_k"],
                    "lmtd_k": row["lmtd_k"],
                }
                for row in _hx_rows(result)
            ],
        },
        "performance": {
            "gross_mw": _r(result.gross_electric_mw, 2),
            "net_mw": _r(result.net_power_mw, 2),
            "aux_circulator_mw": _r(result.aux_circulator_mw, 2),
            "aux_salt_pump_mw": _r(result.aux_salt_pump_mw, 2),
            "aux_turbine_island_mw": _r(result.aux_turbine_island_mw, 2),
            "net_power_delta_mw": _r(result.net_power_delta_mw, 2),
            "eta_cycle_pct": _r(result.eta_cycle_pct, 2),
            "sustainable_load_pct": _r(result.sustainable_load_pct, 2),
            "self_sufficient_modules": int(result.modules_self_sufficient),
        },
        "checks": [check.as_contract() for check in result.checks],
        "narrative": _narrative(result),
        "provenance": provenance,
    }

    technical = {
        "schema_version": "1.0",
        "case_id": case_id,
        "mode": "boiler_replacement",
        "options": [option],
        "recommendation": None,
        "warnings": list(warnings),
        "provenance": provenance,
    }

    assumptions = list(result.cfg.notes)
    if not spec.commercial:
        assumptions.append(spec.display_note)
    assumptions.append(tr("assume.black_box"))
    assumptions.append(tr("assume.partload"))

    return {
        "ok": True,
        "case_id": case_id,
        "generated_at": now,
        "input": _config_to_dict(result.cfg),
        "technical": technical,
        "details": {
            "sections": _hx_rows(result),
            "part_load": _part_load_rows(result),
            "states": [sp.as_row() for sp in build_states(result)],
            "comparison": [
                {"item": item, "coal": coal, "c2n": c2n, "unit": unit}
                for item, coal, c2n, unit in result.comparison_rows()
            ],
            "assumptions": assumptions,
            "checks_text": [check.as_row() for check in result.checks],
        },
    }


def assess(
    case: dict | None = None,
    *,
    case_id: str | None = None,
    mode: str = "boiler_replacement",
) -> dict:
    """评估一个算例，返回"契约 + 界面素材"信封。

    Parameters
    ----------
    case
        与 TOML 同构的字典；``None`` 表示用内置默认算例（600 MW 亚临界）。
    case_id
        算例编号；缺省按时间戳生成，用于报告与溯源。
    mode
        只支持 ``"boiler_replacement"``（本内核的定位）。
    """

    if mode != "boiler_replacement":
        raise ConfigError(tr("assess.mode_unsupported", mode=mode))

    started = time.perf_counter()
    cfg, _ = _load_case(case)
    result = run_design(cfg)
    runtime_ms = int((time.perf_counter() - started) * 1000)

    if case_id is None:
        case_id = "case-" + datetime.now(_TZ).strftime("%Y%m%dT%H%M%S")

    return build_envelope(
        result,
        case=case,
        case_id=case_id,
        runtime_ms=runtime_ms,
        warnings=list(result.warnings),
    )


def report_markdown(envelope: dict) -> str:
    """由信封生成 Markdown 报告（不重新求解，纯渲染，随当前语言）。"""

    tech = envelope["technical"]
    details = envelope["details"]
    option = tech["options"][0]
    p = envelope["input"]["plant"]
    perf = option["performance"]
    sizing = option["sizing"]
    prov = tech["provenance"]
    salt = option["intermediate_loop"]["fluid"]

    lines: list[str] = [
        tr("report.title", name=p["name"]),
        "",
        f"- {tr('report.case_id')}: `{envelope['case_id']}`",
        f"- {tr('report.generated_at')}: {envelope['generated_at']}",
        f"- {tr('report.kernel')}: {prov['kernel']} {prov['kernel_version']}"
        f" ({tr('report.model_card')} {prov['model_card_id']})",
        f"- {tr('report.input_hash')}: `{prov['input_hash']}`",
        "",
        f"## {tr('report.verdict')}",
        "",
        option["narrative"]["headline"],
        "",
        *[f"- {item}" for item in option["narrative"]["details"]],
        "",
        f"## {tr('report.unit_and_boundary')}",
        "",
        f"- {tr('report.unit')}: {p['name']} ({p['turbine_class']})",
        "- "
        + tr(
            "report.gross_net",
            gross=perf["gross_mw"],
            net=perf["net_mw"],
            delta=perf["net_power_delta_mw"],
        ),
        "- "
        + tr(
            "report.main_steam",
            p=p["main_steam_p_bar"],
            t=p["main_steam_t_c"],
        ),
        "- "
        + tr(
            "report.salt",
            fluid=salt,
            hot=option["intermediate_loop"]["t_hot_c"],
            cold=option["intermediate_loop"]["t_cold_c"],
        ),
        "",
        f"## {tr('report.hx_title')}",
        "",
        "| "
        + " | ".join(
            [
                tr("report.h.device"),
                tr("report.h.q"),
                tr("report.h.ka"),
                tr("report.h.ka_sel"),
                tr("report.h.area"),
                tr("report.h.u"),
                tr("report.h.dtmin"),
            ]
        )
        + " |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in details["sections"]:
        lines.append(
            f"| {row['label']} | {row['q_mw']:.1f} | {row['ka_mw_k']:.3f} | "
            f"{row['ka_selected_mw_k']:.3f} | {row['area_m2']:,.0f} | "
            f"{row['u_w_m2k']:.0f} | {row['dt_min_k']:.2f} |"
        )
    lines += [
        "",
        f"## {tr('report.storage_title')}",
        "",
        "- "
        + tr(
            "report.storage",
            cap=sizing["storage"]["capacity_mwh"],
            h=sizing["storage"]["hours"],
            mode=sizing["storage"]["mode"],
        ),
        "- "
        + tr(
            "report.salt_inventory",
            fluid=sizing["salt"]["fluid"],
            mass=sizing["salt"]["mass_t"],
            vol=sizing["salt"]["volume_m3"],
        ),
        "",
        f"## {tr('report.partload_title')}",
        "",
        "| "
        + " | ".join(
            [
                tr("report.h.load"),
                tr("report.h.m"),
                tr("report.h.qsteam"),
                tr("report.h.gross"),
                tr("report.h.net"),
                tr("report.h.qnet"),
                tr("report.h.eta"),
                tr("report.h.tsalt"),
            ]
        )
        + " |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for pt in details["part_load"]:
        lines.append(
            f"| {pt['load_pct']:.0f} | {pt['m_main_kgs']:.1f} | "
            f"{pt['q_steam_mw']:.1f} | {pt['gross_mw']:.1f} | {pt['net_mw']:.1f} | "
            f"{pt['q_net_mw']:+.1f} | {pt['eta_cycle_pct']:.2f} | "
            f"{pt['t_salt_min_c']:.1f} |"
        )
    lines += [
        "",
        tr("report.partload_note"),
        "",
        f"## {tr('report.checks_title')}",
        "",
        f"| {tr('report.h.item')} | {tr('report.h.value')} | "
        f"{tr('report.h.criterion')} | {tr('report.h.verdict')} |",
        "|---|---:|---|---|",
    ]
    for check in details["checks_text"]:
        verdict = tr("report.th.passed") if check["ok"] else tr("report.th.failed")
        lines.append(
            f"| {check['name']} | {check['value']} | {check['criterion']} | "
            f"{verdict} |"
        )
    lines += ["", f"## {tr('report.assumptions_title')}", ""]
    lines += [f"- {item}" for item in details["assumptions"]]
    if tech.get("warnings"):
        lines += ["", f"### {tr('report.warnings_title')}", ""]
        lines += [f"- {item}" for item in tech["warnings"]]
    lines += [
        "",
        "---",
        "",
        tr("report.disclaimer"),
        "",
    ]
    return "\n".join(lines)
