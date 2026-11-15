# tools

维护用脚本，不参与正常运行。按需取用。

| 脚本 | 作用 | 需要什么 |
|---|---|---|
| `offline_selfcheck.py` | 结构自检 + 基准数字。装了 CoolProp 时自动升级为数值回归 | Python；CoolProp 可选（缺省用替身） |
| `fake_coolprop.py` | 物性替身，让求解链在**没有 CoolProp** 的机器上也能跑通装配检查 | 无 |
| `legacy_dump.py` | 把一个算例的结果导成 JSON（供下面的对照脚本使用） | Python + CoolProp |
| `compare_legacy.py` | 与两份**独立的外部实现**逐项比对，用于交叉校验数值路径 | Python + CoolProp，且那两个项目要放在本仓库的同级目录 |

```bash
python tools/offline_selfcheck.py          # 常用：自检 + 基准
python tools/offline_selfcheck.py --fake   # 没有 CoolProp 时只验装配
python tools/compare_legacy.py             # 可选：跨实现对照（需要外部项目）
```

`compare_legacy.py` 默认在本仓库同级目录里找 `C2N_CPP` / `C2N_SCPP` 两套只读的参考实现；
找不到时会直接提示并退出，不影响其它任何功能。它们不是运行依赖，仅用于"同一组输入、
两套实现、结果是否逐项一致"的交叉校验。
