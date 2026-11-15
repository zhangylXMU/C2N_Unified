# C2N boiler replacement — technical assessment: 1000 MW 超超临界机组

- Case ID: `demo-supercritical-en`
- Generated at: 2026-11-15T13:43:10+08:00
- Kernel: c2n-screening 2.0.0 (Model card MC-C2N-001)
- Input hash: `sha256:a7183ad2397c91fc4af8a660d3eb006020c56e177eacf7f49f22dd42d46e4905`

## Verdict

8 × 250 MWth modules (2000 MWth) cover the boiler-side heat of the 1000 MW ultra-supercritical unit; requires 4,071 t of advanced nitrate and 82,278 m² of heat-transfer area; net power 891.9 MW (same-basis change -43.1 MW)

- At rated load the store discharges 117 MW; 4 h needs 468 MWh
- Total heat duty of the four exchangers 2176.9 MW; binding pinch at Evaporator (furnace wall) (5.0 K)
- Sustainable load 94.6 %; continuous full output would need 9 modules (2250 MWth)

## Unit and boundary

- Unit: 1000 MW 超超临界机组 (ultra_supercritical)
- Gross 1000.0 MW, net 891.9 MW (same-basis change -43.1 MW)
- Main steam 265.0 bar / 600 °C
- Salt: advanced_nitrate; hot tank 620.0 °C / cold tank 321.3 °C

## The four exchangers and the IHX

| Equipment | Q MW | kA required, MW/K | kA selected, MW/K | Area m² | U W/m²K | Min ΔT along path, K |
|---|---:|---:|---:|---:|---:|---:|
| Superheater | 490.6 | 9.287 | 9.287 | 10,319 | 900 | 20.00 |
| Evaporator (furnace wall) | 1088.6 | 62.264 | 62.264 | 24,906 | 2500 | 5.00 |
| Economiser | 190.5 | 21.600 | 21.600 | 16,616 | 1300 | 5.96 |
| Reheater | 407.2 | 27.393 | 27.393 | 30,437 | 900 | 5.03 |
| Helium-to-salt IHX | 2059.9 | 36.397 | 36.397 | 45,497 | 800 | 18.68 |

## Thermal storage and salt inventory

- Storage capacity 468 MWh (4.0 h, discharging)
- advanced_nitrate active inventory 3,701 t; total tank volume 4,145 m³

## Part load (50–100 %)

| Load % | Main steam kg/s | Steam-side heat MW | Gross MW | Net MW | Storage ±MW | Efficiency % | Min salt °C |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 819.0 | 2176.9 | 1000.0 | 891.9 | -117.0 | 40.97 | 312.3 |
| 90 | 735.6 | 1973.0 | 900.0 | 794.7 | +86.9 | 40.28 | 310.6 |
| 80 | 653.4 | 1771.7 | 800.0 | 697.4 | +288.3 | 39.37 | 308.8 |
| 70 | 572.1 | 1571.0 | 700.0 | 600.2 | +488.9 | 38.21 | 307.1 |
| 60 | 494.1 | 1369.1 | 600.0 | 502.9 | +690.8 | 36.74 | 306.4 |
| 50 | 415.6 | 1164.1 | 500.0 | 405.7 | +895.8 | 34.85 | 305.7 |
| 40 | 337.3 | 957.9 | 400.0 | 308.5 | +1102.0 | 32.20 | 304.8 |
| 30 | 258.1 | 745.5 | 300.0 | 211.2 | +1314.4 | 28.34 | 303.8 |

> Part-load results are **estimates**: only three calibratable relations are introduced (sliding pressure, stage-group efficiency, feedwater temperature); everything else is an exact heat balance. At the design point the deviation from the full TESPy model is below 0.5 %; at low load it reaches 2–3 %, mostly from auxiliary-power accounting.

## Checks

| Item | Value | Criterion | Verdict |
|---|---:|---|---|
| Minimum pinch of the four exchangers (along the path) | 5.00 K @ Evaporator (furnace wall) | ≥ 5.0 K | pass |
| Cold-salt margin above freezing | 83.3 K | ≥ 20.0 K (i.e. ≥ 258 °C) | pass |
| Hot-salt decomposition limit | 620 °C | ≤ 650 °C (advanced nitrate) | pass |
| IHX cold-end approach | 18.7 K | ≥ 5.0 K | pass |
| Temperature difference along each exchanger | all positive | no temperature cross-over | pass |
| Energy balance at 100 % load | 2059.91 = 2176.90 -116.99 | IHX = steam-side heat + storage discharge | pass |
| Net power is positive | 891.89 MW | > 0 | pass |

## Assumptions and notes

- **Assumption**: transport properties are taken from solar salt and only the decomposition limit is raised to 650 °C, which allows a 620 °C hot tank and 600 °C main steam. The mixture is at pilot scale; its decomposition limit, long-term stability, corrosivity and price all need pilot data. Verify before quoting or procurement.
- The turbine is treated as a black box: only the steam-side conditions of the four exchangers are used as the boundary. Flow capacity, last-stage wetness and differential expansion are not checked.
- Part-load results are estimates (sliding pressure + stage-group efficiency correction + feedwater temperature table), not stage-by-stage flow calculations

### Notes triggered by this input

- Salt medium: Advanced nitrate (pilot scale, properties taken from solar salt). **Assumption**: transport properties are taken from solar salt and only the decomposition limit is raised to 650 °C, which allows a 620 °C hot tank and 600 °C main steam. The mixture is at pilot scale; its decomposition limit, long-term stability, corrosivity and price all need pilot data. Verify before quoting or procurement.

---

This report is a **preliminary assessment** (pre-feasibility depth). It is not a design basis and excludes economics.
