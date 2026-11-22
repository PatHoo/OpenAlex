"""外部出生国证据的分级与定案规则（纯逻辑，不联网、不连库、不臆造事实）。

设计边界
--------
1. ORCID / OpenAlex 没有可靠出生国字段；本模块不把"算法推断"升级为出生国事实。
2. 每条证据必须带公开来源 URL 与原文摘录；来源分三级：official > encyclopedia > news。
3. 身份锚点先于内容：ORCID 履历 / 学科 / 机构 / 名单信息对不上，结论只能是 identity_unresolved。
4. verdict 四档：verified_cn_birth / probable_cn_birth / not_supported / identity_unresolved。
   - verified：明确出生地（官方来源 1 条，或 ≥2 个独立百科交叉）且有中国早期教育；
   - probable：中国本科/硕士教育或中国国籍声明，但无明确出生地原文；
   - not_supported：公开 CV 完整且无中国段，算法信号仅来自合作署名；
   - identity_unresolved：身份无法锚定（同名 / B 级消歧 / 无机构）。
5. 只有 verified / probable 才进入 DID treated；其余不进 treated，也不进 control
   （避免把候选污染对照组）。

对应 WP15 脚本：wp15_external_evidence.py。
"""
from __future__ import annotations

from dataclasses import dataclass

EXTERNAL_RULE_VERSION = "EXTERNAL_BIRTH_v1"

# 来源可信度：数字越大越可信。
SOURCE_TIERS = {
    "official": 3,        # 学部/院士官网、大学官方 CV、院系简介
    "encyclopedia": 2,    # 维基百科、百度百科等百科类
    "news": 1,            # 主流媒体报道
}

# 证据主张类型。
CLAIM_BIRTH_PLACE_CN = "birth_place_cn"
CLAIM_EDUCATION_CN = "education_cn"
CLAIM_NATIONALITY_CN = "nationality_cn"
CLAIM_CAREER_CN_EARLY = "career_cn_early"
CLAIM_NO_CN_IN_PUBLIC_CV = "no_cn_in_public_cv"
CLAIM_IDENTITY_MISMATCH = "identity_mismatch"

VALID_CLAIMS = frozenset({
    CLAIM_BIRTH_PLACE_CN, CLAIM_EDUCATION_CN, CLAIM_NATIONALITY_CN,
    CLAIM_CAREER_CN_EARLY, CLAIM_NO_CN_IN_PUBLIC_CV, CLAIM_IDENTITY_MISMATCH,
})

VALID_IDENTITY_CHECKS = frozenset({"matched", "partial", "unmatched"})

TREATED_VERDICTS = frozenset({"verified_cn_birth", "probable_cn_birth"})


@dataclass(frozen=True)
class EvidenceItem:
    """一条可核查的外部证据。所有字段都必须能落到审计表。"""

    scholar_id: int
    source_type: str          # official / encyclopedia / news
    source_url: str
    source_title: str
    quote_text: str           # 原文摘录（中文优先，保留原句）
    claim: str
    birth_country: str | None = None   # 仅 birth_place_cn 且明确中国时填 'CN'
    weight: int = 0

    @property
    def tier(self) -> int:
        return SOURCE_TIERS[self.source_type]

    def validate(self) -> None:
        if self.source_type not in SOURCE_TIERS:
            raise ValueError(f"unknown source_type: {self.source_type}")
        if self.claim not in VALID_CLAIMS:
            raise ValueError(f"unknown claim: {self.claim}")
        if not self.source_url.startswith(("http://", "https://")):
            raise ValueError(f"source_url must be absolute http(s): {self.source_url}")
        if not self.quote_text.strip():
            raise ValueError("quote_text must not be empty")
        if self.claim == CLAIM_BIRTH_PLACE_CN and self.birth_country != "CN":
            raise ValueError("birth_place_cn claim must set birth_country='CN'")


def decide_verdict(identity_check: str, items: list[EvidenceItem]) -> str:
    """按证据规则定案；输入已通过 validate()。

    规则顺序（先身份，后内容）：
    1. identity_check='unmatched' 或含身份反证 → identity_unresolved；
    2. 官方出生地 1 条（identity 至少 partial），或 ≥2 个独立来源出生地 + 中国教育 → verified；
    3. 有中国教育/国籍/早期职业信号 → probable；
    4. 有完整公开 CV 且其中无中国段 → not_supported；
    5. 其余 → identity_unresolved（不臆造）。
    """
    if identity_check not in VALID_IDENTITY_CHECKS:
        raise ValueError(f"unknown identity_check: {identity_check}")
    if identity_check == "unmatched":
        return "identity_unresolved"
    if any(i.claim == CLAIM_IDENTITY_MISMATCH for i in items):
        return "identity_unresolved"

    birth_sources = {i.source_url for i in items if i.claim == CLAIM_BIRTH_PLACE_CN}
    official_birth = any(
        i.claim == CLAIM_BIRTH_PLACE_CN and i.tier == SOURCE_TIERS["official"]
        and identity_check in ("matched", "partial")
        for i in items
    )
    cn_education = any(
        i.claim in (CLAIM_EDUCATION_CN, CLAIM_CAREER_CN_EARLY) for i in items
    )
    cn_nationality = any(i.claim == CLAIM_NATIONALITY_CN for i in items)

    if official_birth or (len(birth_sources) >= 2 and cn_education):
        return "verified_cn_birth"
    if cn_education and identity_check in ("matched", "partial"):
        return "probable_cn_birth"
    if cn_nationality and identity_check == "matched":
        return "probable_cn_birth"
    if any(i.claim == CLAIM_NO_CN_IN_PUBLIC_CV for i in items):
        return "not_supported"
    return "identity_unresolved"


def is_treated(verdict: str) -> bool:
    return verdict in TREATED_VERDICTS
