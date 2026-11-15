"""设备尺寸估算：换热器外形 与 储罐尺寸。

kA 是给工程师看的量，业主看的是两个数：

1. **要多少换热面积、换热器多大** —— 由 ``kA / U`` 得面积，再按壳管式换热器的
   常规布置折算成管数、管束直径、壳体内径、壳体高度与台数；
2. **要多少熔盐、罐多大** —— 由储热容量与盐的焓差得质量，再按立式圆筒罐
   （给定高径比与气相空间）折算成直径与高度。

这些尺寸只是**量级估算**（±30 %），用来判断"厂房放不放得下""盐够不够运"，
不能替代设备选型计算。所有假设都在报告与 README 里逐条列出。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import PlantConfig, SizingConfig


@dataclass(frozen=True)
class BundleGeometry:
    """一台（或一组并联的）壳管式换热器的外形。"""

    name: str
    area_m2: float
    n_tubes: int
    n_shells: int
    n_tubes_per_shell: int
    tube_length_m: float
    bundle_diameter_m: float
    shell_id_m: float
    shell_height_m: float

    @property
    def shell_volume_m3(self) -> float:
        return math.pi * (0.5 * self.shell_id_m) ** 2 * self.shell_height_m * self.n_shells

    def as_row(self) -> dict:
        return {
            "name": self.name,
            "area_m2": self.area_m2,
            "n_tubes": self.n_tubes,
            "n_shells": self.n_shells,
            "n_tubes_per_shell": self.n_tubes_per_shell,
            "tube_length_m": self.tube_length_m,
            "bundle_diameter_m": self.bundle_diameter_m,
            "shell_id_m": self.shell_id_m,
            "shell_height_m": self.shell_height_m,
        }


@dataclass(frozen=True)
class TankGeometry:
    """一个立式圆筒储罐。"""

    name: str
    mass_t: float
    density_kg_m3: float
    active_volume_m3: float
    tank_volume_m3: float
    diameter_m: float
    height_m: float

    @property
    def footprint_m2(self) -> float:
        """单罐占地（π D²/4）。"""

        return math.pi * (0.5 * self.diameter_m) ** 2

    def as_row(self) -> dict:
        return {
            "name": self.name,
            "footprint_m2": self.footprint_m2,
            "mass_t": self.mass_t,
            "density_kg_m3": self.density_kg_m3,
            "active_volume_m3": self.active_volume_m3,
            "tank_volume_m3": self.tank_volume_m3,
            "diameter_m": self.diameter_m,
            "height_m": self.height_m,
        }


@dataclass(frozen=True)
class Sizing:
    """两个问题的答案。"""

    exchangers: dict[str, BundleGeometry]
    ihx: BundleGeometry
    salt_active_t: float
    salt_inventory_extra_t: float
    hot_tank: TankGeometry
    cold_tank: TankGeometry
    tube_length_m: float
    pitch_mm: float
    tube_od_mm: float
    tube_wall_mm: float
    shell_clearance_m: float
    max_shell_id_m: float
    h_over_d: float
    ullage: float
    inventory_margin: float

    @property
    def salt_total_t(self) -> float:
        return self.salt_active_t + self.salt_inventory_extra_t

    @property
    def area_total_m2(self) -> float:
        return sum(b.area_m2 for b in self.exchangers.values())

    @property
    def shell_count_total(self) -> int:
        return sum(b.n_shells for b in self.exchangers.values())


# ---------------------------------------------------------------------------
# 换热器
# ---------------------------------------------------------------------------
def _bundle(area_m2: float, d_o_m: float, length_m: float, pitch_m: float):
    """管数、管束直径、壳体内径（单台壳）。"""

    n_tubes = area_m2 / (math.pi * d_o_m * length_m)
    # 正三角形排列，每根管占 (√3/2)p² 的截面
    bundle_d = pitch_m * math.sqrt(4.0 * 0.8660 * n_tubes / math.pi)
    return n_tubes, bundle_d


def tube_bundle(
    name: str,
    area_m2: float,
    cfg: SizingConfig,
) -> BundleGeometry:
    """按壳管式布置，把面积折算成外形；壳径超限时并联多台。"""

    d_o_m = cfg.tube_od_mm * 1e-3
    length_m = cfg.tube_length_m
    pitch_m = cfg.pitch_ratio * d_o_m

    n_shells = 1
    while True:
        n_tubes, bundle_d = _bundle(area_m2 / n_shells, d_o_m, length_m, pitch_m)
        shell_id = bundle_d + 2.0 * cfg.shell_clearance_m
        if shell_id <= cfg.max_shell_id_m or n_shells >= 12:
            break
        n_shells += 1

    return BundleGeometry(
        name=name,
        area_m2=area_m2,
        n_tubes=int(round(n_tubes)) * n_shells,
        n_shells=n_shells,
        n_tubes_per_shell=int(round(n_tubes)),
        tube_length_m=length_m,
        bundle_diameter_m=bundle_d,
        shell_id_m=shell_id,
        shell_height_m=length_m + 1.2,
    )


# ---------------------------------------------------------------------------
# 储罐
# ---------------------------------------------------------------------------
def vertical_tank(
    name: str,
    mass_t: float,
    density_kg_m3: float,
    cfg: SizingConfig,
) -> TankGeometry:
    """立式圆筒罐：给定高径比与气相空间，反算直径与高度。"""

    active = mass_t * 1000.0 / density_kg_m3
    volume = active / (1.0 - cfg.ullage)
    # V = π D²/4 × (H/D · D) = π (H/D) D³ / 4
    diameter = (4.0 * volume / (math.pi * cfg.tank_height_to_diameter)) ** (1.0 / 3.0)
    return TankGeometry(
        name=name,
        mass_t=mass_t,
        density_kg_m3=density_kg_m3,
        active_volume_m3=active,
        tank_volume_m3=volume,
        diameter_m=diameter,
        height_m=cfg.tank_height_to_diameter * diameter,
    )


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def build_sizing(
    cfg: PlantConfig,
    *,
    section_areas: dict[str, float],
    ihx_area_m2: float,
    salt_active_t: float,
    salt_hot_density: float,
    salt_cold_density: float,
) -> Sizing:
    sc = cfg.sizing
    exchangers = {
        name: tube_bundle(name, area, sc) for name, area in section_areas.items()
    }
    ihx = tube_bundle("IHX", ihx_area_m2, sc)
    return Sizing(
        exchangers=exchangers,
        ihx=ihx,
        salt_active_t=salt_active_t,
        salt_inventory_extra_t=salt_active_t * sc.salt_inventory_margin,
        hot_tank=vertical_tank("热罐", salt_active_t, salt_hot_density, sc),
        cold_tank=vertical_tank("冷罐", salt_active_t, salt_cold_density, sc),
        tube_length_m=sc.tube_length_m,
        pitch_mm=sc.pitch_ratio * sc.tube_od_mm,
        tube_od_mm=sc.tube_od_mm,
        tube_wall_mm=sc.tube_wall_mm,
        shell_clearance_m=sc.shell_clearance_m,
        max_shell_id_m=sc.max_shell_id_m,
        h_over_d=sc.tank_height_to_diameter,
        ullage=sc.ullage,
        inventory_margin=sc.salt_inventory_margin,
    )
