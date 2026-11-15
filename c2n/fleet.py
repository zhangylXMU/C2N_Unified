"""集团级批量比选：一次跑多份输入卡，出一张对比表。

用法::

    python run.py --fleet config/*.toml
    python run.py --fleet config/            # 目录下所有 .toml
    python run.py --fleet a.toml b.toml

每份卡独立求解（与单机模式完全相同的代码路径），任何一份出错都不会中断其余；
输出 ``fleet.md``、``fleet.csv`` 与屏幕表格，按输入顺序排列。

表格里的列刻意做成"集团选型会用到的那些"：净电变化、全厂效率、面积、盐量、
最低盐温与防凝告警数——这些是决定"改不改、改哪台"的判据。
"""

from __future__ import annotations

import csv
import glob
import math
from dataclasses import dataclass, field
from pathlib import Path

from .config import ConfigError, load_config
from .design import DesignResult, run_design


@dataclass
class FleetRow:
    """一台机组的比选结果；求解失败时 ok=False、error 带原因。"""

    name: str
    path: str
    ok: bool = True
    error: str = ""

    gross_mw: float = math.nan
    q_steam_mw: float = math.nan
    n_modules: int = 0
    coverage_pct: float = math.nan
    storage_mode: str = ""
    net_mw: float = math.nan
    delta_net_mw: float = math.nan
    eta_pct: float = math.nan
    t_cold_c: float = math.nan
    t_fw_c: float = math.nan
    t_salt_min_c: float = math.nan
    t_salt_min_low_c: float = math.nan
    salt_warn_count: int = 0
    salt_warn_total: int = 0
    area_m2: float = math.nan
    salt_t: float = math.nan
    feasible: bool = False

    def as_row(self) -> dict:
        return {
            "machine": self.name,
            "file": self.path,
            "ok": self.ok,
            "error": self.error,
            "gross_mw": _round(self.gross_mw),
            "q_steam_mw": _round(self.q_steam_mw),
            "n_modules": self.n_modules,
            "coverage_pct": _round(self.coverage_pct),
            "storage_mode": self.storage_mode,
            "net_mw": _round(self.net_mw),
            "delta_net_mw": _round(self.delta_net_mw),
            "eta_pct": _round(self.eta_pct),
            "t_cold_c": _round(self.t_cold_c),
            "t_fw_c": _round(self.t_fw_c),
            "t_salt_min_c": _round(self.t_salt_min_c),
            "t_salt_min_low_c": _round(self.t_salt_min_low_c),
            "salt_warnings": self.salt_warn_count,
            "salt_warning_points": self.salt_warn_total,
            "area_m2": _round(self.area_m2, 0),
            "salt_t": _round(self.salt_t, 0),
            "feasible": self.feasible,
        }


def _round(value: float, digits: int = 2) -> float | str:
    if value is None or not math.isfinite(value):
        return "—"
    return round(value, digits)


def expand_paths(raw_paths: list[str]) -> list[Path]:
    """把「目录 / 通配符 / 文件」统一成去重后的文件列表（保持顺序）。"""

    found: list[Path] = []
    for raw in raw_paths:
        candidate = Path(raw)
        if candidate.is_dir():
            found.extend(sorted(candidate.glob("*.toml")))
        elif any(ch in raw for ch in "*?["):
            found.extend(Path(item) for item in sorted(glob.glob(raw)))
        else:
            found.append(candidate)

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in found:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _row_from_result(path: Path, result: DesignResult) -> FleetRow:
    cfg = result.cfg
    loads = result.part_load
    low = [pt for pt in loads if pt.load <= min(p.load for p in loads) + 1e-9]
    return FleetRow(
        name=cfg.plant.name,
        path=str(path),
        ok=True,
        gross_mw=result.gross_electric_mw,
        q_steam_mw=result.steam.q_steam_mw,
        n_modules=cfg.reactor.n_modules,
        coverage_pct=result.sustainable_load_pct,
        storage_mode=result.mode,
        net_mw=result.net_power_mw,
        delta_net_mw=result.net_power_delta_mw,
        eta_pct=result.eta_cycle_pct,
        t_cold_c=result.salt_loop.t_cold_c,
        t_fw_c=cfg.plant.feedwater_t_c,
        t_salt_min_c=loads[0].t_salt_min_c if loads else math.nan,
        t_salt_min_low_c=low[0].t_salt_min_c if low else math.nan,
        salt_warn_count=sum(1 for pt in loads if pt.salt_warnings),
        salt_warn_total=len(loads),
        area_m2=result.sizing.area_total_m2,
        salt_t=result.sizing.salt_total_t,
        feasible=result.feasible,
    )


def run_fleet(paths: list[Path]) -> list[FleetRow]:
    """逐份求解；单份失败只记录原因，不中断整批。"""

    rows: list[FleetRow] = []
    for path in paths:
        try:
            cfg = load_config(path)
            result = run_design(cfg)
        except (ConfigError, ValueError, RuntimeError) as exc:
            rows.append(
                FleetRow(name=path.stem, path=str(path), ok=False, error=str(exc))
            )
            continue
        except Exception as exc:  # noqa: BLE001 - 批量模式不允许单份炸掉整批
            rows.append(
                FleetRow(
                    name=path.stem,
                    path=str(path),
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        rows.append(_row_from_result(path, result))
    return rows


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
_HEADERS = [
    "机组", "毛电 MW", "汽侧吸热 MW", "模块", "堆覆盖 %", "储热",
    "净电 MW", "净电变化 MW", "汽机侧净循环效率 %", "冷罐 °C", "给水 °C",
    "最低盐温 °C", "最 30 % 盐温 °C", "防凝告警", "四台面积 m²", "盐量 t", "校核",
]


def _cells(row: FleetRow) -> list[str]:
    if not row.ok:
        return [row.name] + ["—"] * (len(_HEADERS) - 2) + ["**失败**"]
    return [
        row.name,
        _fmt(row.gross_mw, 1),
        _fmt(row.q_steam_mw, 1),
        str(row.n_modules),
        _fmt(row.coverage_pct, 1),
        row.storage_mode,
        _fmt(row.net_mw, 1),
        _fmt(row.delta_net_mw, 1, signed=True),
        _fmt(row.eta_pct, 2),
        _fmt(row.t_cold_c, 1),
        _fmt(row.t_fw_c, 1),
        _fmt(row.t_salt_min_c, 1),
        _fmt(row.t_salt_min_low_c, 1),
        f"{row.salt_warn_count}/{row.salt_warn_total}",
        _fmt(row.area_m2, 0),
        _fmt(row.salt_t, 0),
        "通过" if row.feasible else "**不通过**",
    ]


def _fmt(value: float, digits: int, signed: bool = False) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    if signed:
        return f"{value:+.{digits}f}"
    return f"{value:.{digits}f}"


def print_fleet(rows: list[FleetRow]) -> None:
    print("=" * 118)
    print("集团批量比选")
    print("=" * 118)
    header = ["机组", "毛电", "净电", "净电变化", "净循环效率", "冷罐", "最低盐温", "防凝告警", "四台面积", "盐量"]
    print("  " + "".join(f"{h:>12}" for h in header))
    print("  " + "".join(f"{u:>12}" for u in ["", "MW", "MW", "MW", "%", "°C", "°C", "", "m²", "t"]))
    for row in rows:
        if not row.ok:
            print(f"  {row.name:>12}{'求解失败':>12}  {row.error[:80]}")
            continue
        cells = [
            row.name,
            f"{row.gross_mw:.1f}",
            f"{row.net_mw:.1f}",
            f"{row.delta_net_mw:+.1f}",
            f"{row.eta_pct:.2f}",
            f"{row.t_cold_c:.1f}",
            f"{row.t_salt_min_c:.1f}",
            f"{row.salt_warn_count}/{row.salt_warn_total}",
            f"{row.area_m2:,.0f}",
            f"{row.salt_t:,.0f}",
        ]
        print("  " + "".join(f"{c:>12}" for c in cells))
    ok = [row for row in rows if row.ok]
    print("-" * 118)
    if ok:
        best = max(ok, key=lambda r: r.eta_pct)
        print(f"  共 {len(rows)} 台，求解成功 {len(ok)} 台；"
              f"汽机侧净循环效率最高：{best.name}（{best.eta_pct:.2f} %）")
        flagged = [r.name for r in ok if r.salt_warn_count]
        if flagged:
            print(f"  防凝告警：{', '.join(flagged)}")
    print("=" * 118)


def write_fleet_csv(rows: list[FleetRow], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].as_row()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_row())
    return path


def write_fleet_markdown(rows: list[FleetRow], path: str | Path) -> Path:
    lines = [
        "# 集团批量比选",
        "",
        f"共 {len(rows)} 台机组，按输入顺序排列。全部结果由 `python run.py --fleet` 生成，",
        "每台机组走的是与单机模式完全相同的求解路径。",
        "",
        "| " + " | ".join(_HEADERS) + " |",
        "|" + "|".join(["---"] + ["---:"] * (len(_HEADERS) - 2) + ["---"]) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cells(row)) + " |")

    lines += [
        "",
        "**列说明**",
        "",
        "- **堆覆盖 %**：反应堆满功率能覆盖 100 % 工况热负荷的比例；<100 % 表示要靠储罐补。",
        "- **储热**：额定工况且为放热 / 充热 / 平衡。",
        "- **净电变化**：= 煤粉炉辅机 − 氦风机 − 盐泵，与汽轮机岛辅机无关。",
        "- **汽机侧净循环效率**：净电 / 四台换热器吸热，额定工况；不是一次能源效率。",
        "- **最低盐温**：额定工况下盐回路的最低温（通常是省煤器出口）。",
        "- **最 30 % 盐温**：最低那个负荷点（默认 30 %）的同一指标，用于看深调峰风险。",
        "- **防凝告警**：触发了 220 / 290 °C 阈值（见 `[salt]`）的负荷点数 / 总点数。",
        "- **四台面积 / 盐量**：四台盐-汽换热器的总面积与**含回路滞留**的采购盐量。",
        "",
    ]
    failed = [row for row in rows if not row.ok]
    if failed:
        lines += ["**求解失败**", ""]
        for row in failed:
            lines.append(f"- `{row.path}`：{row.error}")
        lines.append("")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
