"""状态点（节点）表。

拓扑图上每个编号节点对应这里的一条记录：工质、质量流量、压力、温度、比焓。
编号规则：

* ``H*`` 一回路氦气（堆芯出口 / IHX 出口 / 风机出口）
* ``S*`` 熔盐回路（充热 + 放热两条支路）
* ``W*`` 水/蒸汽（四台换热器的汽水侧）

所有数值都来自本次设计结果，与 ``scheme.png`` 上的编号一一对应。节点名与说明
随界面语言（:mod:`c2n.i18n`），编号与数值不变。
"""

from __future__ import annotations

from dataclasses import dataclass

from .design import DesignResult
from .i18n import tr
from .properties import h_helium, salt_h


@dataclass(frozen=True)
class StatePoint:
    id: str
    name: str
    fluid: str
    m_kgs: float
    p_bar: float
    t_c: float
    h_kj_kg: float
    note: str = ""

    def as_row(self) -> dict:
        return {
            "id": self.id,
            "node": self.name,
            "fluid": self.fluid,
            "m_kgs": round(self.m_kgs, 3),
            "p_bar": round(self.p_bar, 3),
            "T_degC": round(self.t_c, 2),
            "h_kJkg": round(self.h_kj_kg, 2),
            "note": self.note,
        }


def build_states(result: DesignResult) -> list[StatePoint]:
    """按拓扑图的编号顺序生成节点表。"""

    r = result.reactor
    steam = result.steam
    loop = result.salt_loop
    p_salt = result.cfg.salt.p_bar
    salt = result.salt_short
    helium = tr("state.fluid.helium")
    water = tr("state.fluid.water")
    vapour = tr("state.fluid.steam")
    states: list[StatePoint] = []

    # ---- 一回路氦气 ----
    states.append(StatePoint(
        "H1", tr("state.H1"), helium, r.m_helium_kgs, r.p_core_out_bar, r.t_hot_c,
        h_helium(r.p_core_out_bar, r.t_hot_c),
    ))
    states.append(StatePoint(
        "H2", tr("state.H2"), helium, r.m_helium_kgs,
        r.p_ihx_out_bar, r.t_ihx_out_c,
        h_helium(r.p_ihx_out_bar, r.t_ihx_out_c),
        tr("state.note.circulator_ratio", v=r.circulator_pressure_ratio),
    ))
    states.append(StatePoint(
        "H3", tr("state.H3"), helium, r.m_helium_kgs,
        r.p_cold_bar, r.t_core_in_c, r.h_core_in,
        tr("state.note.circulator_work", v=r.p_circulator_mw),
    ))

    # ---- 熔盐：充热回路（IHX 侧）----
    states.append(StatePoint(
        "S1", tr("state.S1"), salt, loop.m_charge_kgs, p_salt,
        loop.t_hot_c, salt_h(loop.t_hot_c), tr("state.note.charging"),
    ))
    states.append(StatePoint(
        "S2", tr("state.S2"), salt, loop.m_discharge_kgs, p_salt,
        loop.t_hot_c, salt_h(loop.t_hot_c), tr("state.note.discharge_start"),
    ))
    states.append(StatePoint(
        "S3", tr("state.S3"), salt, loop.m_main_kgs, p_salt,
        loop.t_sh_out_c, salt_h(loop.t_sh_out_c), tr("state.note.main_branch"),
    ))
    states.append(StatePoint(
        "S4", tr("state.S4"), salt, loop.m_main_kgs, p_salt,
        loop.t_ev_out_c, salt_h(loop.t_ev_out_c), tr("state.note.main_branch"),
    ))
    states.append(StatePoint(
        "S5", tr("state.S5"), salt, loop.m_main_kgs, p_salt,
        loop.t_econ_out_c, salt_h(loop.t_econ_out_c), tr("state.note.main_return"),
    ))
    states.append(StatePoint(
        "S6", tr("state.S6"), salt, loop.m_reheat_kgs, p_salt,
        loop.t_rh_out_c, salt_h(loop.t_rh_out_c), tr("state.note.reheat_return"),
    ))
    states.append(StatePoint(
        "S7", tr("state.S7"), salt, loop.m_discharge_kgs, p_salt,
        loop.t_cold_c, salt_h(loop.t_cold_c),
        tr("state.note.split", m1=loop.m_main_kgs, m2=loop.m_reheat_kgs),
    ))
    states.append(StatePoint(
        "S8", tr("state.S8"), salt, loop.m_charge_kgs, p_salt,
        loop.t_cold_c, salt_h(loop.t_cold_c), tr("state.note.charging_start"),
    ))

    # ---- 水/蒸汽：四台换热器的汽水侧 ----
    if steam.supercritical:
        t_pc = steam.t_pc_c
        note_w2 = (
            tr("state.note.below_pc", t=t_pc, dt=t_pc - steam.t_econ_out_c)
            if t_pc is not None
            else ""
        )
        name_w3 = tr("state.W3_sc")
        note_w3 = tr("state.note.no_phase_change")
        fluid_w3 = tr("state.fluid.supercritical_water")
    else:
        t_sat = steam.t_sat_c
        note_w2 = (
            tr("state.note.subcooling", v=t_sat - steam.t_econ_out_c)
            if t_sat is not None
            else ""
        )
        name_w3 = tr("state.W3_sat")
        note_w3 = tr("state.note.saturated_vapour")
        fluid_w3 = vapour

    states.append(StatePoint(
        "W1", tr("state.W1"), water, steam.m_main_kgs,
        steam.p_fw_bar, steam.t_fw_c, steam.h_fw,
    ))
    states.append(StatePoint(
        "W2", tr("state.W2"), water, steam.m_main_kgs,
        steam.p_boiler_bar, steam.t_econ_out_c, steam.h_econ_out, note_w2,
    ))
    states.append(StatePoint(
        "W3", name_w3, fluid_w3, steam.m_main_kgs,
        steam.p_boiler_bar, steam.t_evap_out_c, steam.h_evap_out, note_w3,
    ))
    states.append(StatePoint(
        "W4", tr("state.W4"), vapour, steam.m_main_kgs,
        steam.p_main_bar, steam.t_main_c, steam.h_main,
    ))
    states.append(StatePoint(
        "W5", tr("state.W5"), vapour, steam.m_rh_kgs,
        steam.p_cr_bar, steam.t_cr_c, steam.h_cr,
    ))
    states.append(StatePoint(
        "W6", tr("state.W6"), vapour, steam.m_rh_kgs,
        steam.p_hr_bar, steam.t_hr_c, steam.h_hr,
    ))
    return states
