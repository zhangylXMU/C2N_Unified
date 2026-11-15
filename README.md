# C2N boiler replacement assessment

**[中文](README_Chinese.md) · English**

> Answers one question for a coal plant that keeps its turbine: if only the boiler is
> replaced by an HTGR plus a two-tank molten-salt store, how many reactor modules, how
> much salt, how much storage, how much heat-transfer surface — and what happens to net
> power?

Subcritical, supercritical and ultra-supercritical units share **one model**: they differ
only in where the steam side is divided and in the salt decomposition limit, and the tool
picks the right variant from the input. Design point and 50–100 % part load are computed
in one pass, about 0.25 s per case.

---

## 1. What it answers

| Owner's question | Result |
|---|---|
| How big a reactor? | Module count (250 MWth each), core power, IHX duty, modules needed for continuous full output |
| How much salt and storage? | Salt flow, hot/cold tank temperatures, active and total inventory, tank volume and dimensions |
| How large are the four exchangers? | Duty, required kA, selected kA, area, U value, terminal and minimum-along-path temperature differences |
| What happens to net power? | Gross − turbine-island auxiliaries − circulators − salt pumps, compared on the same basis |
| Does it still work at part load? | Net power, efficiency, storage throughput and the coldest salt temperature from 100 % down to 50 % |
| Is the design feasible? | Seven checks (pinch, freezing margin, decomposition limit, IHX cold end, temperature cross-over, energy closure, positive net power) |

Scope: **pre-feasibility**. Not a design basis. Economics are out of scope.

---

## 2. One model, two steam-side conventions

|  | Subcritical (drum boiler) | Supercritical / ultra-supercritical (once-through) |
|---|---|---|
| Section boundary | Saturation temperature `T_sat(p_drum)` | Furnace-wall outlet temperature (from the owner) |
| Meaning of the evaporator | Economiser outlet → saturated steam | Economiser outlet → furnace-wall outlet (no phase change) |
| Specific inputs | `drum_p_bar`, `economizer_subcooling_k` | `boiler_p_bar`, `wall_outlet_t_c`, `economizer_outlet_t_c` |
| Salt (decomposition limit) | Solar salt, 600 °C | Advanced nitrate, 650 °C (**pilot-scale assumption**) |
| Typical steam | 16.7 MPa / 538 °C | 26.5 MPa / 600 °C |

Everything else — the duty equations, the along-path integration, the two nested bisections
on the salt side, storage sizing, the helium loop, the net-power basis, part load — is
**shared**. The class is detected from `turbine_class` or from pressure; once resolved, the
other variant's fields are cleared so no downstream code has to guess.

---

## 3. Getting started

```bash
python -m pip install -r requirements.txt     # CoolProp is the only requirement
python run.py                                 # default: 600 MW subcritical
python run.py --config config/supercritical_1000mw.toml
python run.py --fleet config/*.toml           # compare several units
python run.py --write-config my.toml          # export an annotated input template
python run.py --json | jq .technical.options[0].performance
```

From Python:

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

`assess()` is the single entry point shared by the CLI and the website, so the same input
cannot produce two different answers.

---

## 4. Inputs

All inputs live in `config/*.toml`, in six sections: `[plant] [reactor] [salt] [storage]
[sizing] [partload]`. Units: °C, bar(a), MW, kg/s.

`[plant]` falls into three groups:

- **both classes**: gross power, main steam and reheat data, feedwater, auxiliary power;
- **subcritical only**: `drum_p_bar`, `economizer_subcooling_k`;
- **supercritical only**: `boiler_p_bar`, `wall_outlet_t_c`, `economizer_outlet_t_c`.

**Leaving a field empty is not an error.** `c2n.config.resolve()` fills a typical value and
records a readable assumption, for example:

> Furnace-wall outlet temperature not given: using 450 °C (typical 430–470 °C). This value
> sets the evaporator/superheater boundary and the heat-transfer area, so it **must** be
> checked against the boiler heat-balance sheet.

So nameplate-only data still produces a first result — with every filled value flagged for
the owner to confirm.

---

## 5. Outputs

Written to `results/<case>/`:

| File | Content |
|---|---|
| `report.md` | Full report: boundary, sizing, storage, net power on a common basis, part load, checks, assumptions and limits |
| `report_brief.md` | One-page version for filing |
| `summary.json` | The full envelope: contract plus UI material (see §8) |
| `sections.csv` / `states.csv` / `part_load.csv` | Exchangers, 17 state points, part-load table |
| `scheme.png` | One-page schematic (needs matplotlib; skipped if absent) |

---

## 6. Method

| Step | Approach |
|---|---|
| Section duties | `Q = m·Δh`, enthalpies from CoolProp at `(p, T)` |
| Subcritical split | Economiser outlet = `T_sat(p_drum) − subcooling`; evaporator up to saturated steam |
| Supercritical split | Economiser and furnace-wall outlets from the owner; pseudo-critical temperature from the `cp` peak (1 K scan over 350–460 °C) |
| Heat-transfer capability | `kA = ∫dQ/ΔT` over 60 segments (endpoint LMTD is off by 20–40 % where `cp` swings) |
| Salt-side solve | Inner bisection on cold-tank temperature (to hit the design pinch), outer bisection on main-branch split (so main and reheat branches both meet it) |
| Storage sizing | `capacity = |Q_net| × hours`; salt mass `= capacity / (c̄p·ΔT)` |
| Helium loop | Isentropic circulator, core energy balance for flow, `Q_IHX = Q_core + P_circ` |
| Net power | `gross − turbine-island auxiliaries − circulators − salt pumps`, same basis before and after |
| Part load | Sliding pressure `p/p0=(m/m0)^k`, stage-group efficiency table, feedwater temperature table; main steam flow is solved |

**Part load introduces only three calibratable relations.** Everything else is an exact heat
balance through the same `duties_at()` used at the design point. The design point agrees with
the full TESPy model to better than 0.5 %; at low load the gap reaches 2–3 %, mostly from
auxiliary-power accounting (circulators constant, salt pumps proportional, turbine-island
auxiliaries split into fixed and variable parts).

---

## 7. Validation and warnings

| Level | Criterion | Example |
|---|---|---|
| **Hard error** (no result) | Physically unsolvable, or the class contradicts the pressure | Subcritical with 26.5 MPa; supercritical with 18.5 MPa; feedwater hotter than main steam; hot salt not above main steam |
| **Warning** (computed and flagged) | Solvable, but the result may not be trustworthy | Implied cycle efficiency outside the usual band; hot salt close to decomposition; implausible storage size; reactor heat above turbine heat demand |

Warnings go into `warnings` and appear in the terminal, at the top of the report and at the
top of the result page. Nothing is swallowed: owner input is often incomplete, and a flagged
result is more useful than an error dialog.

---

## 8. Service layer and data contract

```python
assess(case: dict) -> dict
```

Envelope:

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

- `technical` follows
  [`contracts/technical-result.schema.json`](contracts/technical-result.schema.json)
  and is the only coupling point for a future economics module;
- `details` carries UI material that does not belong in the contract;
- `provenance.input_hash` is the SHA-256 of the normalised input, so any published report can
  be reproduced.

---

## 9. Chinese / English

Every user-facing string goes through `c2n.i18n`. The language is per request
(`contextvars`), so one process can serve Chinese and English simultaneously, and numbers
and criteria are identical in both languages.

```python
from c2n.i18n import language, tr

with language("en"):
    print(tr("design.check.pinch.name"))     # Minimum pinch of the four exchangers…
print(tr("design.check.pinch.name"))         # 四台换热器最小端差（沿程）
```

The default stays Chinese, so the CLI and existing scripts behave exactly as before.
`tests/test_i18n.py` enforces two hard rules: no Chinese may leak into an English result
(except the owner-supplied unit name), and both languages must agree number for number.

---

## 10. Tests

```bash
python tests/run_tests.py            # 110 tests, about 25 s
python tests/run_tests.py config     # configuration layer (**no CoolProp needed**)
python tests/run_tests.py i18n       # bilingual consistency
python tests/run_tests.py reference  # numerical comparison with the full TESPy model
```

| Evidence | Result |
|---|---|
| Section-by-section comparison with an independent full TESPy model (10 stage groups, 8 extractions) | kA within 0.1 %, net power within 0.5 MW |
| Independent recomputation with the CoolProp IF97 backend | within 0.4 MW (property-implementation difference) |
| Reference cases: 600 MW subcritical → 531.6361 MW net; 1000 MW USC → 891.8948 MW net | pinned in `tools/offline_selfcheck.py` |
| Energy and mass closure, pinch targeting, monotonicity (area vs pinch, storage vs hours) | all pass |

`tools/offline_selfcheck.py` also runs a structural self-check **without CoolProp**, using an
approximate property stand-in, to verify the plumbing; with CoolProp installed the same script
becomes a numerical regression check.

---

## 11. Layout

```text
C2N_Unified/
├── run.py                  # CLI entry point
├── c2n/
│   ├── config.py           # one input family, class detection, validation, TOML I/O
│   ├── fluids.py           # molten salts: limits and labels (new salt = new entry)
│   ├── properties.py       # water/steam/helium (CoolProp), salt fits, pseudo-critical T
│   ├── steam_side.py       # two segmentation strategies behind one SteamSide
│   ├── salt_side.py        # salt-side solve (two bisections, along-path pinch)
│   ├── reactor.py          # helium loop, IHX, module-count solve
│   ├── offdesign.py        # 50–100 % part load
│   ├── design.py           # assembly, net-power basis, seven checks
│   ├── sizing.py           # kA → area → shell and tank dimensions
│   ├── states.py           # 17 state points
│   ├── report.py           # Markdown report and CSV
│   ├── diagram.py          # one-page schematic (optional)
│   ├── fleet.py            # batch comparison
│   ├── assess.py           # service layer: JSON in, contract JSON out
│   └── i18n.py             # bilingual message catalogue (per-request language)
├── config/                 # five case cards
├── contracts/              # JSON Schemas, examples and the validator
├── tests/                  # 110 tests
├── tools/                  # offline self-check, property stand-in, cross-implementation check
└── docs/model-card.md      # model card MC-C2N-001
```

---

## 12. Requirements

- Python **3.11+** (standard-library `tomllib`)
- **CoolProp** — the only third-party runtime dependency (`requirements.txt`)
- matplotlib only for the schematic; if it is missing, `run.py` skips the figure and the
  numerical results are unaffected

Offline, without CoolProp:

```bash
python tools/offline_selfcheck.py --fake     # plumbing only; numbers are not physical
```

---

## 13. Limitations

1. The turbine is a **black box**: only the four exchangers' steam-side conditions are used;
   flow capacity, last-stage wetness and differential expansion are not checked.
2. Heat-transfer areas come from empirical U values (±30 %) and do not replace exchanger
   selection.
3. Part load is an estimate, not a stage-by-stage flow calculation.
4. The store is modelled as fully mixed and adiabatic; no thermocline, no heat loss.
5. The supercritical route rests on a **650 °C advanced nitrate** at pilot scale; the result
   page and the report label that assumption forcibly.
6. No economics: capex, annual generation and payback are not modelled.
7. Pre-feasibility depth; not a design basis.

---

## License

MIT © 2026 zhangylXMU. See [LICENSE](LICENSE).
