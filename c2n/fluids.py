"""中间回路流体：熔盐的**温度限值**与标签。

为什么单独成文件
----------------
亚临界与超临界两个版本的唯一"物理性"差别，除了蒸汽侧分段基准，就是**盐的
分解温度**：

* 太阳盐（60 wt% NaNO3 + 40 wt% KNO3）：约 600 °C 开始分解，热罐只能 565 °C，
  因此主蒸汽上限约 538 °C —— **超临界在太阳盐上不可达**；
* 新型硝酸盐（本工具按"物性同太阳盐、分解温度 650 °C"处理）：热罐可 620 °C，
  主蒸汽 600 °C 才成立。但它是**中试产品**，没有商业化供货与长期运行数据。

旧代码把这两个数字硬编码在三个地方（``config`` 的字符串、``design.check`` 的
``600.0``、``salt_side.SALT_FREEZING_C``），于是"换一种盐"就变成改三处代码。
现在它们是流体的属性：换盐 = 加一个条目，而不是改求解器。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SaltFluid:
    """一种熔盐的标识、温度限值与对外说明。"""

    key: str
    """配置里用的标识（``[salt].fluid``）。"""

    label: str
    """完整名称（中文），用于报告标题与表头。"""

    label_en: str
    """完整名称（英文）。"""

    short: str
    """简称，用于状态点表与图面（"太阳盐" / "新盐"）。"""

    short_en: str
    """简称（英文）。"""

    t_freeze_c: float
    """凝固点 °C。冷罐温度的设计下界 = 该值 + ``freezing_margin_k``。"""

    t_decompose_c: float
    """分解温度上限 °C。超过它盐开始变质，方案不成立。"""

    commercial: bool
    """是否已商业化供货。``False`` 表示物性/限值属于**假设**，必须在报告里标注。"""

    note: str
    """一句话说明（中文），直接进报告与网站提示。"""

    note_en: str
    """一句话说明（英文）。"""

    #: 焓与密度拟合式的系数（见 ``c2n.properties``）。两种盐目前共用同一套拟合。
    cp_a: float = 1443.0
    cp_b: float = 0.172
    rho_a: float = 2090.0
    rho_b: float = -0.636

    @property
    def t_min_c(self) -> float:
        """工程上允许的最低冷罐温度（凝固点 + 20 K 裕量在 ``SaltConfig`` 里另加）。"""

        return self.t_freeze_c

    # ---- 按当前语言取文案 ---------------------------------------------------
    @property
    def display_label(self) -> str:
        from .i18n import EN, get_language

        return self.label_en if get_language() == EN else self.label

    @property
    def display_short(self) -> str:
        from .i18n import EN, get_language

        return self.short_en if get_language() == EN else self.short

    @property
    def display_note(self) -> str:
        from .i18n import EN, get_language

        return self.note_en if get_language() == EN else self.note


SOLAR_SALT = SaltFluid(
    key="solar_salt",
    label="太阳盐（60 % NaNO3 / 40 % KNO3）",
    label_en="Solar salt (60 wt% NaNO3 / 40 wt% KNO3)",
    short="太阳盐",
    short_en="solar salt",
    t_freeze_c=238.0,
    t_decompose_c=600.0,
    commercial=True,
    note="商用成熟配方；热罐 565 °C，主蒸汽上限约 538 °C，超临界不可达。",
    note_en=(
        "Commercial, mature mixture. Hot tank at 565 °C, main steam limited to "
        "about 538 °C; supercritical steam is out of reach."
    ),
)

ADVANCED_NITRATE = SaltFluid(
    key="advanced_nitrate",
    label="新型硝酸盐（中试，物性按太阳盐取用）",
    label_en="Advanced nitrate (pilot scale, properties taken from solar salt)",
    short="新盐",
    short_en="advanced nitrate",
    t_freeze_c=238.0,
    t_decompose_c=650.0,
    commercial=False,
    note=(
        "**假设**：热物性与太阳盐相同，仅分解温度提高到 650 °C，因此热罐可 620 °C、"
        "主蒸汽可 600 °C。该配方尚在中试，分解温度、长期稳定性、腐蚀性与价格"
        "均需中试数据支撑，报价与供货前必须核实。"
    ),
    note_en=(
        "**Assumption**: transport properties are taken from solar salt and only "
        "the decomposition limit is raised to 650 °C, which allows a 620 °C hot "
        "tank and 600 °C main steam. The mixture is at pilot scale; its "
        "decomposition limit, long-term stability, corrosivity and price all need "
        "pilot data. Verify before quoting or procurement."
    ),
)


SALT_FLUIDS: dict[str, SaltFluid] = {
    f.key: f for f in (SOLAR_SALT, ADVANCED_NITRATE)
}

#: 与 ``[salt].fluid`` 的取值配合使用；``auto`` 由机组类型推断
AUTO = "auto"


def get_salt(key: str) -> SaltFluid:
    """按标识取流体；未知标识给出可读的候选列表。"""

    try:
        return SALT_FLUIDS[key]
    except KeyError:
        raise KeyError(
            f"unknown salt fluid {key!r}; expected 'auto' or one of "
            f"{sorted(SALT_FLUIDS)}"
        ) from None


def default_salt_for(turbine_class: str) -> SaltFluid:
    """按机组类型推荐盐种：亚临界用太阳盐，超临界只能用高分解温度盐。"""

    if turbine_class == "subcritical":
        return SOLAR_SALT
    return ADVANCED_NITRATE
