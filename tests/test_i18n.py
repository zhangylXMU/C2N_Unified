"""双语测试：英文模式下不许漏中文，且两种语言的数字必须完全一致。

为什么值得单独测
----------------
"中英切换"最容易出的错不是翻得不好，而是：

1. 某个提示忘了走字典，英文界面里突然蹦出一句中文；
2. 数字在两种语言下不一样（比如格式化参数写错），于是同一份输入给出两个结论。

这两件事都能被这里挡住。
"""

from __future__ import annotations

import string
import tomllib
from pathlib import Path

from c2n.assess import assess, report_markdown
from c2n.i18n import EN, LANGUAGES, _CATALOG, contains_cjk, language, tr

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    "config/subcritical_600mw.toml",
    "config/supercritical_1000mw.toml",
    "config/reference_supercritical_600mw.toml",
]


# ---------------------------------------------------------------------------
# 字典本身
# ---------------------------------------------------------------------------
def test_every_entry_has_both_languages() -> None:
    for key, (zh, en) in _CATALOG.items():
        assert zh.strip(), f"{key}: 中文为空"
        assert en.strip(), f"{key}: 英文为空"


def test_placeholders_match_between_languages() -> None:
    """占位符集合必须一致，否则一种语言会 KeyError 或漏数字。"""

    def fields(template: str) -> set[str]:
        return {
            name
            for _, name, _, _ in string.Formatter().parse(template)
            if name is not None
        }

    for key, (zh, en) in _CATALOG.items():
        assert fields(zh) == fields(en), (
            f"{key}: 占位符不一致 {sorted(fields(zh))} vs {sorted(fields(en))}"
        )


def test_language_context_is_scoped() -> None:
    assert tr("section.superheater") == "过热器"
    with language(EN):
        assert tr("section.superheater") == "Superheater"
        with language("zh"):
            assert tr("section.superheater") == "过热器"
    assert tr("section.superheater") == "过热器"


def test_unknown_language_is_rejected() -> None:
    try:
        with language("fr"):
            pass
    except ValueError:
        return
    raise AssertionError("未知语言必须报错，不能静默回落")


def test_supported_languages() -> None:
    assert LANGUAGES == ("zh", "en")


# ---------------------------------------------------------------------------
# 端到端：英文信封里不许有中文（用户自己填的机组名除外）
# ---------------------------------------------------------------------------
def _walk(obj, path: str = ""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "input":  # 用户输入原样回显，内含中文机组名是正常的
                continue
            yield from _walk(value, f"{path}/{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from _walk(value, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def test_english_envelope_has_no_chinese() -> None:
    for rel in CASES:
        data = tomllib.loads((ROOT / rel).read_text(encoding="utf-8-sig"))
        # 机组名是用户数据，这里换成 ASCII，避免把"数据里有中文"误判成"界面漏翻译"
        data.setdefault("plant", {})["name"] = "Test unit"
        with language(EN):
            envelope = assess(data, case_id="i18n")
            markdown = report_markdown(envelope)
        leaks = [
            (path, text[:60])
            for path, text in _walk(envelope)
            if contains_cjk(text)
        ]
        assert not leaks, f"{rel} 英文结果里仍有中文：{leaks[:3]}"
        assert not contains_cjk(markdown), f"{rel} 英文报告里仍有中文"


def test_chinese_envelope_has_chinese() -> None:
    data = tomllib.loads(
        (ROOT / "config/subcritical_600mw.toml").read_text(encoding="utf-8-sig")
    )
    envelope = assess(data, case_id="i18n-zh")
    assert contains_cjk(envelope["technical"]["options"][0]["narrative"]["headline"])


def test_numbers_are_identical_across_languages() -> None:
    """同一条输入，两种语言必须给出同一组数字（只允许文案不同）。"""

    for rel in CASES:
        data = tomllib.loads((ROOT / rel).read_text(encoding="utf-8-sig"))
        zh = assess(data, case_id="same")
        with language(EN):
            en = assess(data, case_id="same")

        zh_opt = zh["technical"]["options"][0]
        en_opt = en["technical"]["options"][0]
        for path in ("performance", "intermediate_loop"):
            assert zh_opt[path] == en_opt[path], f"{rel}: {path} 两种语言不一致"

        # sizing 里的 binding_section 是段标识（SH/EV/EC/RH），本来就与语言无关
        zh_sizing = dict(zh_opt["sizing"])
        en_sizing = dict(en_opt["sizing"])
        assert zh_sizing["salt"]["binding_section"] == en_sizing["salt"]["binding_section"]
        zh_sizing["salt"] = {k: v for k, v in zh_sizing["salt"].items() if k != "binding_section"}
        en_sizing["salt"] = {k: v for k, v in en_sizing["salt"].items() if k != "binding_section"}
        assert zh_sizing == en_sizing, f"{rel}: sizing 两种语言数值不一致"

        for zh_row, en_row in zip(zh_opt["checks"], en_opt["checks"]):
            assert zh_row["id"] == en_row["id"]
            assert zh_row["ok"] == en_row["ok"]
            assert zh_row["value"] == en_row["value"]
            assert zh_row["limit"] == en_row["limit"]

        assert zh["details"]["part_load"] == en["details"]["part_load"]
        assert len(zh["details"]["states"]) == len(en["details"]["states"])
