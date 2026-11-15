"""命令行入口：设计点 + 变工况 + 报告 + 批量比选。

用法::

    python run.py                          # 默认算例（600 MW 亚临界）
    python run.py --config my_plant.toml
    python run.py --config config/supercritical_1000mw.toml
    python run.py --fleet config/*.toml     # 多台机组批量比选
    python run.py --write-config out.toml   # 导出带注释的输入模板
    python run.py --json                    # 只把信封 JSON 打到 stdout

输出（默认写到 ``results/<config 名>/``）：

* ``report.md``           中文报告（业主版，含变工况表与校核）
* ``summary.json``        完整信封（契约 + 界面素材），与网站 API 同构
* ``sections.csv``        四段换热器 + IHX
* ``states.csv``          17 个状态点
* ``part_load.csv``       50–100 % 变工况
* ``scheme.png``          一页纸框图（需要 matplotlib；没有就跳过）
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import tomllib
from pathlib import Path

from c2n.assess import KERNEL, build_envelope, report_markdown
from c2n.config import DEFAULT_CONFIG, ConfigError, dump_config, load_config
from c2n.design import run_design
from c2n.fleet import expand_paths, print_fleet, run_fleet, write_fleet_csv, write_fleet_markdown
from c2n.report import write_markdown, write_sections_csv, write_states_csv
from c2n.states import build_states
from c2n.version import __version__


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _case_from_config(path: Path | None) -> dict:
    if path is None:
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8-sig"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="C2N 锅炉侧替换评估（亚临界 + 超临界统一内核）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", type=Path, help="输入 TOML（缺省用内置默认算例）")
    parser.add_argument("--out", type=Path, help="输出目录（缺省 results/<算例名>）")
    parser.add_argument("--json", action="store_true", help="只打印信封 JSON")
    parser.add_argument("--write-config", type=Path, help="导出带注释的输入模板后退出")
    parser.add_argument("--fleet", nargs="+", help="批量比选：多份 TOML 或目录")
    parser.add_argument("--no-figure", action="store_true", help="不画框图")
    parser.add_argument("--quiet", action="store_true", help="不打印终端摘要")
    args = parser.parse_args(argv)

    if args.write_config:
        base = DEFAULT_CONFIG
        text = dump_config(base)
        args.write_config.parent.mkdir(parents=True, exist_ok=True)
        args.write_config.write_text(text, encoding="utf-8")
        print(f"已导出输入模板：{args.write_config}")
        return 0

    if args.fleet:
        paths = expand_paths(args.fleet)
        rows = run_fleet(paths)
        print_fleet(rows)
        out = args.out or Path("results") / "fleet"
        out.mkdir(parents=True, exist_ok=True)
        write_fleet_csv(rows, out / "fleet.csv")
        write_fleet_markdown(rows, out / "fleet.md")
        print(f"\n已写出 {out / 'fleet.md'} 与 {out / 'fleet.csv'}")
        return 0 if all(row.ok for row in rows) else 1

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"输入有误：\n{exc}", file=sys.stderr)
        return 2

    started = time.perf_counter()
    try:
        result = run_design(cfg)
    except (ValueError, ZeroDivisionError) as exc:
        print(f"求解失败：{exc}", file=sys.stderr)
        return 3
    runtime_ms = int((time.perf_counter() - started) * 1000)

    states = build_states(result)
    case = _case_from_config(args.config)
    envelope = build_envelope(
        result,
        case=case,
        case_id=args.config.stem if args.config else "default",
        runtime_ms=runtime_ms,
        warnings=list(result.warnings),
    )

    if args.json:
        json.dump(envelope, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    out = args.out or Path("results") / (args.config.stem if args.config else "default")
    out.mkdir(parents=True, exist_ok=True)

    figure = out / "scheme.png"
    drew = False
    if not args.no_figure:
        try:
            from c2n.diagram import draw

            draw(result, figure)
            drew = True
        except Exception as exc:  # matplotlib 不在或字体缺失都不该挡住计算
            print(f"（跳过框图：{exc}）", file=sys.stderr)

    write_markdown(result, states, out / "report.md", figure=figure.name if drew else "")
    write_sections_csv(result, out / "sections.csv")
    write_states_csv(states, out / "states.csv")
    (out / "summary.json").write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "report_brief.md").write_text(
        report_markdown(envelope), encoding="utf-8"
    )
    _write_csv(
        out / "part_load.csv",
        [
            {
                "load_pct": round(100.0 * pt.load, 1),
                **{k: v for k, v in pt.as_row().items() if k != "load_pct"},
            }
            for pt in result.part_load
        ],
    )

    if not args.quiet:
        p = cfg.plant
        print("=" * 78)
        print(f"  C2N 锅炉侧替换评估  ·  内核 {KERNEL} {__version__}")
        print(f"  {p.name}（{p.grade_label}）")
        print("=" * 78)
        for item, value, unit in result.summary_rows():
            print(f"  {item:<34}{value:>18}  {unit}")
        print("-" * 78)
        for check in result.checks:
            flag = "OK  " if check.ok else "FAIL"
            print(f"  [{flag}] {check.name:<24}{check.value:>18}  （判据 {check.criterion}）")
        if result.warnings:
            print("-" * 78)
            print("  提示：")
            for note in result.warnings:
                print(f"    - {note}")
        print("=" * 78)
        print(f"  输出目录：{out.resolve()}")
        print(f"  运行耗时：{runtime_ms} ms")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
