"""测试入口（不依赖 pytest，业主/CI 都能直接跑）。

    python tests/run_tests.py            # 全部
    python tests/run_tests.py config     # 只跑配置层（不需要 CoolProp）
    python tests/run_tests.py physics    # 只跑纯函数（物性/传热）
    python tests/run_tests.py design     # 两种机型的方案定性检查
    python tests/run_tests.py reference  # 与完整 TESPy 模型 / 独立复算的对照

分组说明
--------
``config``     机型判定、缺省补齐、校验分级、TOML 往返 —— 合并两套内核时最容易
               出错的一层，且**不需要 CoolProp**。
``physics``    物性、LMTD、泵功这类纯函数。
``design``     亚临界与超临界各自的方案定性检查（面积随端差单调、储热随小时数
               单调、不可行输入被拦住……）。
``reference``  数值对照：亚临界对 ``C2N_HTGR_Salt``（TESPy），超临界对
               ``C2N_HTGR_Salt_SCPP`` 与 CoolProp 手算。**这是可信度的锚点。**
"""

from __future__ import annotations

import importlib
import inspect
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULES = {
    "config": "tests.test_config",
    "i18n": "tests.test_i18n",
    "physics": "tests.test_physics",
    "design_subcritical": "tests.test_design_subcritical",
    "design_supercritical": "tests.test_design_supercritical",
    "reference_subcritical": "tests.test_reference_subcritical",
    "reference_supercritical": "tests.test_reference_supercritical",
    "reference_card": "tests.test_scpp_reference",
    "fleet": "tests.test_fleet",
}

ALIASES = {
    "physics": ["physics"],
    "lang": ["config", "i18n"],
    "design": ["design_subcritical", "design_supercritical"],
    "reference": ["reference_subcritical", "reference_supercritical", "reference_card"],
    "all": list(MODULES),
}


def run(names: list[str]) -> int:
    failures: list[tuple[str, str]] = []
    count = 0
    for label in names:
        module_name = MODULES[label]
        try:
            module = importlib.import_module(module_name)
        except Exception:
            failures.append((label, traceback.format_exc()))
            print(f"\n=== {module_name} ===\n  FAIL 导入失败")
            continue
        print(f"\n=== {module_name} ===")
        for name, func in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("test_") or func.__module__ != module_name:
                continue
            count += 1
            t0 = time.time()
            try:
                func()
            except Exception:
                failures.append((f"{label}.{name}", traceback.format_exc()))
                print(f"  FAIL {name}  ({time.time() - t0:.2f} s)")
            else:
                print(f"  ok   {name}  ({time.time() - t0:.2f} s)")

    print("\n" + "=" * 70)
    if failures:
        for name, tb in failures:
            print(f"\n--- {name} ---\n{tb}")
        print(f"{len(failures)} of {count} tests FAILED")
        return 1
    print(f"{count} tests passed")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        return run(list(MODULES))

    requested: list[str] = []
    for item in argv:
        if item in ALIASES:
            requested.extend(ALIASES[item])
        elif item in MODULES:
            requested.append(item)
        else:
            print(f"未知测试组 {item!r}；可选：{sorted(MODULES)} 或别名 {sorted(ALIASES)}")
            return 2

    seen: list[str] = []
    for name in requested:
        if name not in seen:
            seen.append(name)
    return run(seen)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
