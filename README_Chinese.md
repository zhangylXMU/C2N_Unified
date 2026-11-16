# C2N 锅炉侧替换评估

**中文 · [English](README.md)**

> 给一座**已有汽轮机**的火电厂回答一个问题：只把锅炉换成"高温气冷堆 + 熔盐双罐储热"，
> 汽轮机完全不动，需要多大的堆、多少盐、多大的罐、四台换热器各要多大，净电会变成多少。

亚临界、超临界、超超临界共用**同一套模型**：区别只在蒸汽侧的分段基准与盐的分解温度，
由输入自动判定。设计点与 50–100 % 变工况一次算完，单算例约 0.25 s。

---

## 1. 它能回答什么

| 业主的问题 | 工具给出的结果 |
|---|---|
| 要多大堆？ | 模块数（单模块 250 MWth）、堆芯热功率、IHX 热负荷、"长期满发需要几个模块" |
| 要多少盐、多大罐？ | 盐流量、冷/热罐温度、活动盐量与含回路总盐量、冷热罐容积与罐径×高 |
| 四台换热器要多大？ | 每台的 Q、所需 kA、选型 kA、面积、U 值、两端温差与沿程最小温差 |
| 净电变多少？ | 毛电 − 汽机岛辅机 − 氦风机 − 盐泵，改造前后同口径对比 |
| 低负荷还行不行？ | 50–100 % 的净电、效率、储罐吞吐、盐回路最低温度与凝固风险 |
| 方案成立吗？ | 七项校核（端差、凝固裕量、分解温度、IHX 冷端、温度交叉、能量闭合、净电为正） |

精度定位：**预可研 / 初步可行性**，不构成设计依据；不含经济性。

---

## 2. 一个模型，两套蒸汽侧分段

|  | 亚临界（汽包炉） | 超临界 / 超超临界（直流炉） |
|---|---|---|
| 分段边界 | 饱和温度 `T_sat(p_drum)` | 水冷壁出口温度（业主给定） |
| 蒸发段的含义 | 省煤器出口 → 饱和汽 | 省煤器出口 → 水冷壁出口（无相变） |
| 专属输入 | `drum_p_bar`、`economizer_subcooling_k` | `boiler_p_bar`、`wall_outlet_t_c`、`economizer_outlet_t_c` |
| 盐（分解温度） | 太阳盐 600 °C | 新型硝酸盐 650 °C（**中试假设**） |
| 典型参数 | 16.7 MPa / 538 °C | 26.5 MPa / 600 °C |

其余（四段热负荷的焓差公式、沿程积分、盐侧两次二分、储罐定容、氦回路、净电口径、
变工况）**完全共用**。机组类型由 `turbine_class` 或压力自动判定，判定后另一套专属字段
被清空，下游代码不需要再猜。

---

## 3. 快速开始

```bash
python -m pip install -r requirements.txt     # 只需 CoolProp
python run.py                                 # 默认算例：600 MW 亚临界
python run.py --config config/supercritical_1000mw.toml
python run.py --fleet config/*.toml           # 多台机组批量比选
python run.py --write-config my.toml          # 导出带注释的输入模板
python run.py --json | jq .technical.options[0].performance
```

Python 里：

```python
from c2n.assess import assess

envelope = assess({"plant": {"turbine_class": "supercritical",
                             "main_steam_p_bar": 265.0,
                             "main_steam_t_c": 600.0,
                             "main_steam_m_kgs": 819.0,
                             "gross_power_mw": 1000.0},
                   "reactor": {"n_modules": 8},
                   "salt": {"t_hot_c": 620.0}})

option = envelope["technical"]["options"][0]
print(option["performance"]["net_mw"], option["feasibility"]["status"])
```

`assess()` 是网站与命令行共同的入口：同一份输入，两边给出同一组数字。

---

## 4. 输入

全部输入在 `config/*.toml`，分六段：`[plant] [reactor] [salt] [storage] [sizing] [partload]`。
单位：温度 °C，压力 bar(a)，功率 MW，流量 kg/s。

`[plant]` 的字段分三组：

- **两种机型共有**：毛电、主蒸汽/再热参数、给水、厂用电；
- **亚临界专属**：`drum_p_bar`、`economizer_subcooling_k`；
- **超临界专属**：`boiler_p_bar`、`wall_outlet_t_c`、`economizer_outlet_t_c`。

**留空不是错误。** 判定与补齐由 `c2n.config.resolve()` 完成，每一项补齐都会在结果里留下
一条可读的假设说明，例如：

> 未给水冷壁出口温度：按 450 °C 计算（典型 430~470 °C），该值直接决定蒸发段/过热段的分界
> 与面积，**必须**用热力特性书核对。

所以"我只知道铭牌参数"也能先出一版结果——只是每条补齐都必须由业主确认。

---

## 5. 输出

默认写到 `results/<算例名>/`：

| 文件 | 内容 |
|---|---|
| `report.md` | 中文报告：边界、定尺寸、储热、净电同口径、变工况、校核、假设与局限 |
| `report_brief.md` | 一页版（业主存档） |
| `summary.json` | 完整信封：契约 + 界面素材（见 §8） |
| `sections.csv` / `states.csv` / `part_load.csv` | 换热面、17 个状态点、变工况表 |
| `scheme.png` | 一页纸框图（需 matplotlib，缺失时自动跳过） |

---

## 6. 计算方法

| 环节 | 做法 |
|---|---|
| 四段热负荷 | `Q = m·Δh`，焓由 CoolProp 的 `(p, T)` 求得 |
| 亚临界分段 | 省煤器出口 = `T_sat(p_drum) − 过冷度`；蒸发段到饱和汽 |
| 超临界分段 | 省煤器出口、水冷壁出口由业主给定；拟临界温度取 `cp` 峰值（1 K 扫描 350–460 °C） |
| 换热能力 | `kA = ∫dQ/ΔT`，沿程 60 段积分（端点 LMTD 在比热剧变处会偏 20~40 %） |
| 盐侧反解 | 内层对冷罐温度二分（使沿程最小端差 = 设计值），外层对主支路份额二分（使主/再热支路同时达标） |
| 储热定容 | `容量 = |Q_net| × hours`；盐量 `= 容量 / (cp̄·ΔT)` |
| 氦回路 | 风机等熵压缩、堆芯能量平衡反解流量、`Q_IHX = Q_core + P_circ` |
| 净电 | `毛电 − 汽机岛辅机 − 氦风机电耗 − 盐泵电耗`，改造前后同口径 |
| 变工况 | 滑压 `p/p0=(m/m0)^k` + 级组效率修正表 + 给水温度表，反解主蒸汽流量 |

**变工况只引入三条可标定关系**，其余（四段热负荷、辅机、储罐吞吐）都是与额定点同一个
`duties_at()` 算出来的精确热平衡。设计点对完整 TESPy 模型的偏差 < 0.5 %；低负荷处
2~3 %，差异主要在辅机记账口径（氦风机恒定、盐泵随流量、汽机岛辅机按固定/变动分解）。

---

## 7. 校验与提示分级

| 级别 | 判据 | 例子 |
|---|---|---|
| **硬错误**（拒绝计算） | 物理上无解，或机型自相矛盾 | 亚临界却给 26.5 MPa；超临界却给 18.5 MPa；给水温度高于主蒸汽；热盐温度不高于主蒸汽 |
| **软提示**（照算 + 标注） | 能算，但结果可能不可信 | 毛电与主蒸汽流量隐含的循环效率超出常见区间；热盐逼近分解温度；储热量级异常；堆热功率大于汽机热负荷 |

软提示进 `warnings`，同时出现在终端、报告开头与结果页顶部。**一条都不吞掉**——业主的
输入往往就是不全的，先给一份带标注的结果，比弹一个错误框有用。

---

## 8. 服务层与数据契约

```python
assess(case: dict) -> dict
```

返回的信封：

```json
{
  "ok": true,
  "case_id": "case-20261115T125039",
  "generated_at": "2026-11-15T12:50:39+08:00",
  "input": { "plant": {…}, "reactor": {…}, "salt": {…} },
  "technical": { "schema_version": "1.0", "mode": "boiler_replacement",
                 "options": [{ "feasibility": {…}, "sizing": {…},
                               "performance": {…}, "checks": […],
                               "provenance": {…} }] },
  "details": { "sections": […], "part_load": […], "states": […],
               "comparison": […], "assumptions": […], "checks_text": […] }
}
```

- `technical` 按 [`contracts/technical-result.schema.json`](contracts/technical-result.schema.json)
  组织，是将来接经济性模块的唯一耦合点；
- `details` 是界面素材（契约里放不下，也不该放）；
- `provenance.input_hash` 是归一化输入的 SHA-256，任何一份报告都能复现。

---

## 9. 中文 / English

内核的**所有面向用户的文案**都走 `c2n.i18n`，语言是请求级的（`contextvars`），
所以一个进程可以同时服务中英文请求，数字与判据在两种语言下完全一致。

```python
from c2n.i18n import language, tr

with language("en"):
    print(tr("design.check.pinch.name"))     # Minimum pinch of the four exchangers…
print(tr("design.check.pinch.name"))         # 四台换热器最小端差（沿程）
```

默认语言恒为中文，CLI 与既有脚本行为不变。`tests/test_i18n.py` 会检查两条硬约束：
英文结果里不许残留中文（用户自己填的机组名除外），且两种语言的数值逐项相同。

---

## 10. 测试

```bash
python tests/run_tests.py            # 110 项，约 25 s
python tests/run_tests.py config     # 配置层（**不需要 CoolProp**）
python tests/run_tests.py i18n       # 双语一致性
python tests/run_tests.py reference  # 与完整 TESPy 模型的数值对照
```

| 证据 | 结果 |
|---|---|
| 与独立的 TESPy 完整模型（10 级组、8 级抽汽）逐项对照 | kA 偏差 ≤ 0.1 %，净电差 ≤ 0.5 MW |
| CoolProp IF97 后端独立复算四段热负荷 | 差 < 0.4 MW（物性实现差异） |
| 基准卡：600 MW 亚临界 → 净电 531.6361 MW；1000 MW 超超临界 → 891.8948 MW | 已钉在 `tools/offline_selfcheck.py` |
| 能量/质量守恒、端差命中、单调性（面积随端差、储热随时长） | 全部通过 |

`tools/offline_selfcheck.py` 可以在**没有 CoolProp** 的环境里跑结构自检（用近似物性替身），
用来验证装配是否正确；装了 CoolProp 后同一个脚本会自动升级为数值基准检查。

---

## 11. 目录

```text
C2N_Unified/
├── run.py                  # 命令行入口
├── c2n/
│   ├── config.py           # 一个输入族 + 机型判定 + 校验分级 + TOML 读写
│   ├── fluids.py           # 熔盐：温度限值 + 标签（换盐 = 加条目）
│   ├── properties.py       # 水/蒸汽/氦（CoolProp）+ 熔盐拟合 + 拟临界温度
│   ├── steam_side.py       # 两套分段策略 + 统一 SteamSide
│   ├── salt_side.py        # 盐侧反解（两次二分 + 沿程端差约束）
│   ├── reactor.py          # 氦回路 + IHX + 模块数反解
│   ├── offdesign.py        # 50–100 % 变工况
│   ├── design.py           # 组装、净电同口径、七项校核
│   ├── sizing.py           # kA → 面积 → 壳体 / 罐径
│   ├── states.py           # 17 个状态点
│   ├── report.py           # Markdown 报告 + CSV
│   ├── diagram.py          # 一页纸框图（可选）
│   ├── fleet.py            # 批量比选
│   ├── assess.py           # 服务层：JSON 进、契约 JSON 出
│   └── i18n.py             # 中英文案字典（请求级语言）
├── config/                 # 五张算例卡（亚临界 / 超临界 / 验证卡）
├── contracts/              # 数据契约（JSON Schema + 示例 + 校验脚本）
├── tests/                  # 110 项测试
├── tools/                  # 离线自检、假物性、跨实现对照、导出工具
└── docs/model-card.md      # 模型卡 MC-C2N-001
```

---

## 12. 依赖与环境

- Python **≥ 3.11**（标准库 `tomllib`）
- **CoolProp**（唯一必需的三方库，`requirements.txt`）
- matplotlib 仅画框图时用；缺失时 `run.py` 自动跳过，不影响结果

本机若装不上 CoolProp（离线环境），可以：

```bash
python tools/offline_selfcheck.py --fake     # 用替身物性验证装配（数字无物理意义）
```

---

## 13. 局限

1. 汽轮机按**黑盒**处理：只取四台换热器的汽水参数作为边界，不校验通流能力、末级湿度与胀差。
2. 换热面积按经验 U 值折算（±30 %），不替代换热器选型计算。
3. 变工况是估算，不是逐点通流计算。
4. 储罐按完全混合、绝热定容，不含斜温层与散热。
5. 超临界方案依赖**新型硝酸盐 650 °C** 这条中试假设；结果页与报告会强制标注。
6. 不含经济性：投资、年发电量、回本年限均未建模。
7. 本工具是预可研深度，不构成设计依据。

---

## 许可

MIT © 2026 Dr. ZHANG Yaoli，见 [LICENSE](LICENSE)。
