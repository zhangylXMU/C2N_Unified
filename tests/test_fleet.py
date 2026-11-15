"""批量比选（--fleet）的测试。"""

from __future__ import annotations

import shutil
from pathlib import Path

from c2n.fleet import (
    expand_paths,
    run_fleet,
    write_fleet_csv,
    write_fleet_markdown,
)

ROOT = Path(__file__).resolve().parents[1]
VALID = ROOT / "config" / "subcritical_300mw.toml"

#: 临时目录放在项目内（沙箱只允许写工作区，且 TemporaryDirectory 的清理会被拒）
_TMP = ROOT / "tests" / "_fleet_tmp"


def _make_fleet_dir(tmp: Path) -> Path:
    """两个可用 + 一个坏卡（固定目录，避免 tempfile 清理被沙箱拒绝）。"""

    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "a_300.toml").write_text(VALID.read_text(encoding="utf-8-sig"),
                                   encoding="utf-8")
    text = VALID.read_text(encoding="utf-8-sig").replace(
        "name = \"300 MW 亚临界再热机组\"", "name = \"测试 600\"").replace(
        "gross_power_mw = 300.0", "gross_power_mw = 600.0").replace(
        "main_steam_m_kgs = 240.000", "main_steam_m_kgs = 480.000").replace(
        "reheat_m_kgs = 200.500", "reheat_m_kgs = 401.000")
    (tmp / "b_600.toml").write_text(text, encoding="utf-8")
    (tmp / "c_broken.toml").write_text("[plant]\nbogus_key = 1\n", encoding="utf-8")
    return tmp


def test_expand_paths_handles_directory_glob_and_files() -> None:
    tmp = _make_fleet_dir(_TMP)
    if True:
        from_dir = expand_paths([str(tmp)])
        assert len(from_dir) == 3
        from_glob = expand_paths([str(tmp / "*.toml")])
        assert len(from_glob) == 3
        from_file = expand_paths([str(tmp / "a_300.toml")])
        assert len(from_file) == 1
        # 去重
        both = expand_paths([str(tmp), str(tmp / "a_300.toml")])
        assert len(both) == 3


def test_fleet_runs_and_survives_a_broken_card() -> None:
    tmp = _make_fleet_dir(_TMP)
    if True:
        rows = run_fleet(expand_paths([str(tmp)]))
        assert len(rows) == 3
        ok = [row for row in rows if row.ok]
        bad = [row for row in rows if not row.ok]
        assert len(ok) == 2 and len(bad) == 1
        assert "bogus_key" in bad[0].error
        # 两台有效机组都应有正净电与效率
        for row in ok:
            assert row.gross_mw > 0.0
            assert row.net_mw > 0.0
            assert 0.3 < row.eta_pct < 0.6 * 100
            assert row.area_m2 > 0.0
            assert row.salt_t > 0.0
        # 600 MW 那台毛电更大
        by_gross = sorted(ok, key=lambda r: r.gross_mw)
        assert by_gross[-1].gross_mw > by_gross[0].gross_mw


def test_fleet_markdown_and_csv_are_written() -> None:
    tmp = _make_fleet_dir(_TMP)
    if True:
        rows = run_fleet(expand_paths([str(tmp)]))
        md = write_fleet_markdown(rows, tmp / "fleet.md")
        csv_path = write_fleet_csv(rows, tmp / "fleet.csv")
        text = md.read_text(encoding="utf-8")
        assert "集团批量比选" in text
        assert "测试 600" in text
        assert "c_broken" in text or "求解失败" in text
        lines = csv_path.read_text(encoding="utf-8-sig").splitlines()
        assert len(lines) == 4  # 表头 + 3 行
        assert "machine" in lines[0]
