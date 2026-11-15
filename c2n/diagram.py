"""系统拓扑图：闭合熔盐回路 + 编号节点 + 节点热工参数。

与 `C2N_HTGR_Salt` 的拓扑图口径一致：每条工质流线上有一个编号圆标
（H*/S*/W*），旁边给出该节点的 ``m / p / T / h``。编号与 `states.csv`、
`report.md` 的状态点表一一对应。

熔盐回路画成**闭合回路**：

    冷罐 -> IHX -> 热罐 -> 热盐总管 -> {过热器 -> 蒸发器 -> 省煤器 | 再热器}
         -> 冷盐总管 -> 冷罐

右侧仅用一条虚线边界表示"汽轮机侧不改动"，四台换热器的汽水侧状态即其边界。

标签避免遮挡：每个标签按候选位置依次尝试，取第一个不与设备/已放置标签重叠的
位置，并用细引线连回节点。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Ellipse, FancyBboxPatch, Polygon, Rectangle

from .design import DesignResult
from .states import StatePoint, build_states

plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

C_HE = "#c55a11"
C_SALT = "#2e7d32"
C_STEAM = "#1f5fa8"
C_TANK = "#f1f8ec"
TEXT = "#1f2a36"
MUTED = "#5a6675"

#: 标签外框尺寸（数据单位）；宽度按文本长度估算，避免碰撞检测低估
LABEL_H = 0.36


def _text_width(*lines: str) -> float:
    """估算标签宽度（数据单位）：CJK 字符按 2 个拉丁字符计。"""

    widest = 0
    for line in lines:
        units = sum(2 if ord(ch) > 0x2000 else 1 for ch in line)
        widest = max(widest, units)
    return 0.030 * widest + 0.22


@dataclass(frozen=True)
class _Node:
    sp: str
    x: float
    y: float


_NODES = {
    "H1": _Node("H1", 2.55, 7.35),
    "H2": _Node("H2", 3.02, 5.95),
    "H3": _Node("H3", 2.38, 5.95),
    "S1": _Node("S1", 4.72, 6.15),
    "S2": _Node("S2", 6.80, 8.65),
    "S3": _Node("S3", 6.95, 6.35),
    "S4": _Node("S4", 6.95, 4.65),
    "S5": _Node("S5", 6.95, 3.25),
    "S6": _Node("S6", 6.80, 5.40),
    "S7": _Node("S7", 6.70, 2.68),
    "S8": _Node("S8", 4.35, 3.60),
    "W1": _Node("W1", 11.10, 4.10),
    "W2": _Node("W2", 10.30, 4.85),
    "W3": _Node("W3", 10.30, 6.50),
    "W4": _Node("W4", 11.10, 7.30),
    "W5": _Node("W5", 11.10, 9.10),
    "W6": _Node("W6", 11.10, 8.70),
}


# ---------------------------------------------------------------------------
# 基元
# ---------------------------------------------------------------------------
def _box(ax, x, y, w, h, title, body="", edge=C_STEAM, face="#ffffff", lw=1.2,
         obstacles=None):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.0,rounding_size=0.10",
        facecolor=face, edgecolor=edge, linewidth=lw, zorder=3,
    ))
    ax.text(x + w / 2, y + h * (0.62 if body else 0.5), title, ha="center",
            va="center", fontsize=7.8, fontweight="bold", color=TEXT, zorder=4)
    if body:
        ax.text(x + w / 2, y + h * 0.26, body, ha="center", va="center",
                fontsize=6.0, color=MUTED, linespacing=1.35, zorder=4)
    if obstacles is not None:
        obstacles.append((x, y, w, h))


def _tank(ax, cx, cy, w, h, title, temp, obstacles=None):
    ax.add_patch(Rectangle((cx - w / 2, cy - h / 2 + 0.10 * h), w, h - 0.20 * h,
                           facecolor=C_TANK, edgecolor="none", zorder=3))
    for dy in (+1, -1):
        ax.add_patch(Ellipse((cx, cy + dy * (h / 2 - 0.10 * h)), w, 0.20 * h,
                             facecolor=C_TANK, edgecolor=C_SALT, linewidth=1.1,
                             zorder=4))
    ax.plot([cx - w / 2, cx - w / 2],
            [cy - h / 2 + 0.10 * h, cy + h / 2 - 0.10 * h],
            color=C_SALT, lw=1.1, zorder=3)
    ax.plot([cx + w / 2, cx + w / 2],
            [cy - h / 2 + 0.10 * h, cy + h / 2 - 0.10 * h],
            color=C_SALT, lw=1.1, zorder=3)
    ax.text(cx, cy + 0.28 * h, title, ha="center", va="center",
            fontsize=7.2, fontweight="bold", color=TEXT, zorder=5)
    ax.text(cx, cy - 0.20 * h, temp, ha="center", va="center",
            fontsize=6.6, color=MUTED, zorder=5)
    if obstacles is not None:
        obstacles.append((cx - w / 2, cy - h / 2, w, h))


def _pipe(ax, points, color, lw=2.0, head=True):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    ax.plot(xs, ys, color="white", lw=lw + 3.2, solid_capstyle="round",
            zorder=2, alpha=0.9)
    ax.plot(xs, ys, color=color, lw=lw, solid_capstyle="round", zorder=3)
    if head:
        ax.add_patch(Polygon([
            (xs[-1], ys[-1]),
            (xs[-1] - 0.20, ys[-1] + 0.09),
            (xs[-1] - 0.20, ys[-1] - 0.09),
        ], closed=True, facecolor=color, edgecolor="none", zorder=4))


def _overlaps(box, others, pad=0.05):
    x, y, w, h = box
    for ox, oy, ow, oh in others:
        if (x < ox + ow + pad and ox < x + w + pad
                and y < oy + oh + pad and oy < y + h + pad):
            return True
    return False


def _label_box(x, y, ha, va, w=1.0, h=LABEL_H):
    left = x if ha == "left" else (x - w if ha == "right" else x - w / 2)
    bottom = y if va == "bottom" else (y - h if va == "top" else y - h / 2)
    return left, bottom, w, h


def _place_label(node: _Node, obstacles, bounds, width):
    """在节点周围找一个不遮挡的位置。返回 (x, y, ha, va)。"""

    x0, x1, y0, y1 = bounds
    candidates = []
    for dist in (0.45, 0.70, 0.95, 1.25, 1.60, 2.0):
        candidates += [
            (node.x + dist, node.y, "left", "center"),
            (node.x - dist, node.y, "right", "center"),
            (node.x, node.y + dist * 0.75, "center", "bottom"),
            (node.x, node.y - dist * 0.75, "center", "top"),
        ]
        candidates += [
            (node.x + dist, node.y + dist * 0.6, "left", "bottom"),
            (node.x - dist, node.y + dist * 0.6, "right", "bottom"),
            (node.x + dist, node.y - dist * 0.6, "left", "top"),
            (node.x - dist, node.y - dist * 0.6, "right", "top"),
        ]
    for lx, ly, ha, va in candidates:
        box = _label_box(lx, ly, ha, va, width)
        if box[0] < x0 or box[0] + box[2] > x1:
            continue
        if box[1] < y0 or box[1] + box[3] > y1:
            continue
        if not _overlaps(box, obstacles):
            return lx, ly, ha, va
    return node.x, node.y + 0.6, "center", "bottom"


def _state(ax, sp: StatePoint, node: _Node, color: str, obstacles, bounds):
    text = (f"{sp.id} {sp.name}\n"
            f"m {sp.m_kgs:.0f} · p {sp.p_bar:.2f} bar\n"
            f"T {sp.t_c:.1f} °C · h {sp.h_kj_kg:.0f}")
    width = _text_width(*text.split("\n"))
    lx, ly, ha, va = _place_label(node, obstacles, bounds, width)
    ax.add_patch(Circle((node.x, node.y), 0.13, facecolor=color,
                        edgecolor="white", linewidth=0.8, zorder=6))
    ax.text(node.x, node.y, sp.id, ha="center", va="center", fontsize=4.8,
            fontweight="bold", color="white", zorder=7)
    ax.annotate(
        text, xy=(node.x, node.y), xytext=(lx, ly),
        textcoords="data", ha=ha, va=va,
        fontsize=4.3, color=TEXT, linespacing=1.22, zorder=8,
        bbox=dict(boxstyle="round,pad=0.20", fc="white", ec=color,
                  lw=0.5, alpha=0.97),
        arrowprops=dict(arrowstyle="-", color=color, lw=0.45,
                        shrinkA=2, shrinkB=2),
    )
    obstacles.append(_label_box(lx, ly, ha, va, width))


# ---------------------------------------------------------------------------
# 主图
# ---------------------------------------------------------------------------
def draw(result: DesignResult, path: str | Path, dpi: int = 220) -> Path:
    cfg = result.cfg
    loop = result.salt_loop
    r = cfg.reactor
    states = {sp.id: sp for sp in build_states(result)}
    obstacles: list[tuple[float, float, float, float]] = []
    bounds = (0.30, 12.40, 0.60, 11.00)

    fig, ax = plt.subplots(figsize=(14.2, 9.6))
    ax.set_xlim(0, 13.6)
    ax.set_ylim(0, 11.6)
    ax.axis("off")

    ax.text(0.35, 11.22, f"C2N 锅炉侧替换方案（{cfg.plant.grade_label}）—— {cfg.plant.name}",
            fontsize=14.0, fontweight="bold", color=TEXT)
    ax.text(0.35, 10.84,
            "汽轮机侧不改动；只替换四台换热器并新增高温气冷堆与熔盐双罐储热",
            fontsize=8.2, color=MUTED)

    # ---------------- 一回路 ----------------
    _box(ax, 0.30, 5.20, 1.80, 2.60,
         f"高温气冷堆\n{r.n_modules} × {r.module_power_mwth:.0f} MWth",
         f"{r.t_hot_c:.0f} → {r.t_ihx_out_c:.0f} °C\n{r.p_cold_bar:.0f} bar\n"
         f"风机 {result.reactor.p_circulator_mw:.1f} MW",
         edge=C_HE, face="#fdf2ea", obstacles=obstacles)
    _box(ax, 3.10, 4.95, 1.35, 2.40, "IHX",
         f"{result.reactor.q_ihx_mw:.0f} MW\nkA {result.ihx.ka_mw_k:.2f} · {result.ihx.area_m2 / 1000:.1f}k m²",
         edge=C_HE, face="#fff7ef", obstacles=obstacles)

    ax.add_patch(Circle((2.72, 5.95), 0.16, facecolor="#fff7ef", edgecolor=C_HE,
                        linewidth=1.0, zorder=4))
    ax.text(2.72, 5.95, "C", ha="center", va="center", fontsize=6.2,
            fontweight="bold", color=C_HE, zorder=5)
    obstacles.append((2.56, 5.79, 0.32, 0.32))

    _pipe(ax, [(2.10, 7.35), (3.10, 7.35)], C_HE, 1.8)      # 堆 -> IHX（热）
    _pipe(ax, [(3.10, 5.95), (2.10, 5.95)], C_HE, 1.8)      # IHX -> 风机 -> 堆（冷）

    # ---------------- 储罐 ----------------
    _tank(ax, 5.90, 8.65, 1.30, 1.80, "热罐", f"{loop.t_hot_c:.0f} °C", obstacles)
    _tank(ax, 5.90, 2.30, 1.30, 1.80, "冷罐", f"{loop.t_cold_c:.1f} °C", obstacles)

    # ---------------- 四台换热器 ----------------
    column_x, column_w = 7.30, 2.60
    for name, (y, h) in {
        "再热器": (8.55, 1.10),
        "过热器": (6.75, 1.10),
        "蒸发器": (5.15, 1.10),
        "省煤器": (3.55, 1.10),
    }.items():
        sec = loop.sections[name]
        _box(ax, column_x, y, column_w, h, name,
             f"{sec.q_mw:.0f} MW · kA {sec.ka_mw_k:.2f} MW/K\n"
             f"面积 {sec.area_m2:,.0f} m² · 最小 ΔT {sec.dt_min_k:.1f} K",
             edge=C_SALT, face="#f4faf1", lw=1.3, obstacles=obstacles)

    # ---------------- 熔盐回路（闭合）----------------
    _pipe(ax, [(4.45, 6.15), (4.70, 6.15), (4.70, 8.65), (5.25, 8.65)], C_SALT, 2.2)
    _pipe(ax, [(6.55, 8.65), (7.05, 8.65)], C_SALT, 2.2, head=False)
    _pipe(ax, [(7.05, 8.65), (7.05, 9.10), (7.30, 9.10)], C_SALT, 2.2)   # -> 再热器
    _pipe(ax, [(7.05, 8.65), (7.05, 7.30), (7.30, 7.30)], C_SALT, 2.2)   # -> 过热器
    _pipe(ax, [(7.30, 6.90), (6.95, 6.90), (6.95, 5.70), (7.30, 5.70)], C_SALT, 2.2)
    _pipe(ax, [(7.30, 5.20), (6.95, 5.20), (6.95, 4.10), (7.30, 4.10)], C_SALT, 2.2)
    _pipe(ax, [(7.30, 3.60), (6.95, 3.60), (6.95, 2.85), (6.55, 2.85)], C_SALT, 2.2)
    _pipe(ax, [(7.30, 8.70), (6.80, 8.70), (6.80, 2.45), (6.55, 2.45)], C_SALT, 2.2)
    _pipe(ax, [(5.25, 2.30), (4.35, 2.30), (4.35, 4.95)], C_SALT, 2.2)   # 冷罐 -> IHX

    # ---------------- 汽水侧 ----------------
    _pipe(ax, [(12.60, 4.10), (9.90, 4.10)], C_STEAM, 2.4)   # 给水
    _pipe(ax, [(9.90, 4.35), (10.30, 4.35), (10.30, 5.45), (9.90, 5.45)], C_STEAM, 2.0)
    _pipe(ax, [(9.90, 5.90), (10.30, 5.90), (10.30, 7.05), (9.90, 7.05)], C_STEAM, 2.0)
    _pipe(ax, [(9.90, 7.30), (12.60, 7.30)], C_STEAM, 2.6)   # 主蒸汽
    _pipe(ax, [(12.60, 9.10), (9.90, 9.10)], C_STEAM, 2.2)   # 冷再热
    _pipe(ax, [(9.90, 8.70), (12.60, 8.70)], C_STEAM, 2.2)   # 热再热

    # ---------------- 节点 ----------------
    colors = {"H": C_HE, "S": C_SALT, "W": C_STEAM}
    # 先把所有节点圆标登记为障碍，避免标签盖住别的节点
    for sp in states.values():
        node = _NODES[sp.id]
        obstacles.append((node.x - 0.18, node.y - 0.18, 0.36, 0.36))
    for sp in states.values():
        _state(ax, sp, _NODES[sp.id], colors[sp.id[0]], obstacles, bounds)

    # ---------------- 汽轮机侧边界 ----------------
    ax.add_patch(FancyBboxPatch(
        (12.60, 0.75), 0.80, 9.30,
        boxstyle="round,pad=0.0,rounding_size=0.10",
        facecolor="#eef4fb", edgecolor=C_STEAM, linewidth=1.3,
        linestyle=(0, (5, 3)), zorder=1,
    ))
    ax.text(13.00, 5.40, "汽轮机侧  既有，不改动", ha="center", va="center",
            fontsize=7.8, fontweight="bold", color=C_STEAM, rotation=90,
            zorder=2)

    # ---------------- 图例 / 结论 ----------------
    for i, (color, label) in enumerate([
        (C_HE, "氦气"), (C_SALT, "熔盐"), (C_STEAM, "水 / 蒸汽"),
    ]):
        y = 3.90 - i * 0.32
        ax.plot([0.45, 0.85], [y, y], color=color, lw=2.4, solid_capstyle="round")
        ax.text(0.95, y, label, fontsize=7.0, va="center", color=TEXT)
    ax.text(0.45, 2.85,
            "熔盐回路闭合：冷罐 → IHX → 热罐 → 热盐总管\n"
            "→（过热器→蒸发器→省煤器 | 再热器）→ 冷盐总管 → 冷罐",
            fontsize=6.4, color=MUTED, va="top", linespacing=1.5)

    verdict = "方案成立" if result.feasible else "存在不通过项"
    ax.text(0.35, 0.30,
            f"{verdict} · 最小端差 {loop.min_pinch_k:.2f} K（瓶颈 {loop.binding}）· "
            f"储罐{result.mode} {abs(result.q_net_mw):.1f} MW · "
            f"净电 {result.net_power_mw:.1f} MW · 汽机侧净循环效率 {result.eta_cycle_pct:.2f} %",
            fontsize=7.8, color=TEXT,
            bbox=dict(boxstyle="round,pad=0.35", fc="#fbfcfe", ec="#c9d2dc", lw=0.8))
    ax.text(0.35, 0.80,
            f"储热 {result.storage.capacity_mwh:.0f} MWh / {result.storage.hours:.0f} h · "
            f"{result.salt_short} {result.sizing.salt_total_t:,.0f} t（含回路）· "
            f"热罐 Ø{result.sizing.hot_tank.diameter_m:.1f}×"
            f"{result.sizing.hot_tank.height_m:.1f} m · "
            f"冷罐 Ø{result.sizing.cold_tank.diameter_m:.1f}×"
            f"{result.sizing.cold_tank.height_m:.1f} m · "
            f"换热面 {result.sizing.area_total_m2 + result.sizing.ihx.area_m2:,.0f} m²",
            fontsize=6.6, color=C_SALT)

    fig.tight_layout()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


