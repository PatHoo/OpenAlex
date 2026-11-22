"""中国背景候选队列与 DID 的共享口径常量。

重要：OpenAlex/ORCID 没有可靠出生国字段。本模块实现的不是"已证实中国出生"，
而是"可复核的中国背景候选"：姓名拼音特征 + 中国内地/中国香港/中国台湾机构轨迹
+ 2017 年美国主机构面板。只有 high 置信度自动进入 DID 处理组代理变量，
medium/low 仅供人工复核；birth_country 除外部证据外保持 NULL。
"""
from __future__ import annotations

# 强拼音姓氏：所有格在英语语境中也有较高区分度，仍不能单独证明中国出生。
STRONG_PINYIN_SURNAMES = (
    "zhang", "wang", "li", "zhao", "chen", "liu", "yang", "huang", "zhou",
    "wu", "xu", "sun", "ma", "zhu", "hu", "guo", "he", "gao", "lin", "luo",
    "zheng", "liang", "xie", "song", "tang", "han", "cao", "feng", "dong",
    "xiao", "cheng", "cai", "peng", "pan", "tian", "yuan", "deng", "shen",
    "jiang", "du", "ye", "wei", "lu", "ding", "ren", "yao",
)

# 弱拼音/威妥玛与粤语拼写：用于召回，必须配合中国机构轨迹才升到 medium。
WEAK_PINYIN_SURNAMES = (
    "chou", "chow", "hsieh", "hsu", "tsai", "tzeng", "jeng", "lee", "lau",
    "yip", "yeung",
)

# 数据中的 HK/TW 代码按"中国香港/中国台湾地区关联"处理，不等同于出生国证据。
GREATER_CHINA_CODES = ("CN", "HK", "TW")

COHORT_RULE_VERSION = "CN_US_2017_v1"
DID_RULE_VERSION = "DID_2018_A1000_v1"
WORK_RULE_VERSION = "A_1000_v1"

PRE_YEARS = (2014, 2015, 2016, 2017)
POST_YEARS = (2018, 2019, 2020, 2021)
BASELINE_YEAR = 2017
POST_START_YEAR = 2018


def regex_for_surnames(names: tuple[str, ...] | list[str]) -> str:
    """生成供 PostgreSQL `~` 使用的姓名 token 正则（非字母边界）。"""
    ordered = sorted(set(x.lower() for x in names))
    return "(^|[^a-z])(" + "|".join(ordered) + ")([^a-z]|$)"


def classify_confidence(
    *,
    strong_pinyin: bool,
    weak_pinyin: bool,
    greater_china_signal: bool,
    birth_hint: bool,
    us_basis: str | None,
    us_primary_is_tie: bool,
) -> str | None:
    """队列置信度；与 wp13 SQL 的 CASE 表达式逐条对应。"""
    if not us_basis:
        return None
    if birth_hint:
        return "high"
    if strong_pinyin and greater_china_signal and us_basis == "exact2017" and not us_primary_is_tie:
        return "high"
    if (strong_pinyin or weak_pinyin) and greater_china_signal:
        return "medium"
    if strong_pinyin or weak_pinyin or greater_china_signal:
        return "low"
    return None
