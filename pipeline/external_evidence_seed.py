"""外部出生国证据种子：仅收录公开可核查来源，逐条带 URL 与原文摘录。

采集原则（见 external_evidence_rules.py）：
- 只用机构官网、官方 CV、维基百科/百度百科、主流媒体；不采论坛/营销号/AI 生成页。
- 每条证据必须能回答"谁说的、在哪说的、原文是什么"，落库后可由第三方复核。
- 身份锚点用 ORCID 履历 + 学科 + 机构三重比对；对不上即 identity_unresolved，不臆断。
- 本文件是"证据清单"而非"结论断言"：verdict 由 external_evidence_rules.decide_verdict
  按规则计算，不在这里硬编码。

2026-11-22 采集。7 名算法 high 候选逐人核对：
- 4 人 verified（官方或交叉来源明确写中国出生地）：Yang Peidong / Zhu Jian-Kang /
  Hu Frank B. / Peng Fang Z.
- 1 人 probable（官方 CV 有中国本科+硕士，但无明确出生地原文）：Sun Shouheng
- 1 人 not_supported（算法假阳性：Technion 博士、全美职业，无中国段）：Wang Joseph
- 1 人 identity_unresolved（B 级消歧、机构空白、Jie Liu 同名众多）：Liu Jie
"""
from __future__ import annotations

from dataclasses import dataclass

from external_evidence_rules import (
    CLAIM_BIRTH_PLACE_CN,
    CLAIM_EDUCATION_CN,
    CLAIM_IDENTITY_MISMATCH,
    CLAIM_NO_CN_IN_PUBLIC_CV,
    EvidenceItem,
)


@dataclass(frozen=True)
class Subject:
    scholar_id: int
    display_name: str
    orcid: str
    discipline: str
    inst_2017: str
    identity_check: str          # matched / partial / unmatched
    identity_note: str           # 身份锚点核对依据（可审计）
    review_note: str             # 定案说明
    items: tuple[EvidenceItem, ...]


def _e(scholar_id: int, source_type: str, url: str, title: str,
       quote: str, claim: str, weight: int, birth_country: str | None = None) -> EvidenceItem:
    return EvidenceItem(
        scholar_id=scholar_id, source_type=source_type, source_url=url,
        source_title=title, quote_text=quote, claim=claim,
        birth_country=birth_country, weight=weight,
    )


SUBJECTS: tuple[Subject, ...] = (
    Subject(
        scholar_id=326,
        display_name="Yang, Peidong",
        orcid="0000-0003-4799-1684",
        discipline="Nanoscience & Nanotechnology",
        inst_2017="University of California at Berkeley",
        identity_check="matched",
        identity_note=("ORCID 0000-0003-4799-1684 载 Peidong Yang 自 2004 任职 UC Berkeley；"
                       "库中学科=nanoscience、2017 主机构=UC Berkeley、firstyr=1994，一致。"),
        review_note="中国科学院学部官网明确写 1971 年出生于江苏苏州市，官方来源，verified。",
        items=(
            _e(326, "official",
               "https://www.casad.cas.cn/ysxx2022/wjys/202201/t20220126_4823719.html",
               "中国科学院学部与院士 - 杨培东（外籍院士）",
               "杨培东（Peidong Yang），美国国籍……1971年出生于江苏苏州市。1993年获得中国科学技术大学应用化学学士学位。",
               CLAIM_BIRTH_PLACE_CN, 60, "CN"),
            _e(326, "encyclopedia",
               "https://www.fbrowser.org/wikipedia/zh-hans/%E6%9D%A8%E5%9F%B9%E4%B8%9C",
               "维基百科（中文镜像）- 杨培东",
               "杨培东（1971年8月—），江苏苏州人……1971年出生在苏州相城区元和镇。1988年毕业于木渎中学，1993年获中国科学技术大学化学学士学位。",
               CLAIM_BIRTH_PLACE_CN, 40, "CN"),
        ),
    ),
    Subject(
        scholar_id=266,
        display_name="Zhu, Jian-Kang",
        orcid="0000-0001-5134-731X",
        discipline="Plant Biology & Botany",
        inst_2017="Chinese Academy of Sciences",
        identity_check="partial",
        identity_note=("ORCID 0000-0001-5134-731X 仅有姓名 Jian-Kang，无履历；"
                       "库中 2017 主机构=中国科学院、学科=植物学，与该学者公开任职一致，判 partial。"),
        review_note="西北农林科技大学（.edu.cn 官方）报告简介明确写 1967 年生于安徽阜南；百度百科交叉印证，verified。",
        items=(
            _e(266, "official",
               "https://csbaa.nwafu.edu.cn/tzgg/373307.htm",
               "西北农林科技大学 作物抗逆与高效生产全国重点实验室 - 报告人简介",
               "朱健康，男，1967年生于中国安徽省阜南县。国际著名植物生物学家……美国科学院院士。",
               CLAIM_BIRTH_PLACE_CN, 60, "CN"),
            _e(266, "encyclopedia",
               "https://baike.baidu.com/item/%E6%9C%B1%E5%81%A5%E5%BA%B7",
               "百度百科 - 朱健康",
               "朱健康，男，美国国籍，1967年生于中国安徽……出生地 安徽省阜南县。1987年，毕业于北京农业大学。",
               CLAIM_BIRTH_PLACE_CN, 40, "CN"),
            _e(266, "encyclopedia",
               "https://baike.baidu.com/item/%E6%9C%B1%E5%81%A5%E5%BA%B7",
               "百度百科 - 朱健康（教育经历）",
               "1983年以远超当年北大录取线的高分考上北京农业大学（现中国农业大学）土壤农业化学系。",
               CLAIM_EDUCATION_CN, 25),
        ),
    ),
    Subject(
        scholar_id=121,
        display_name="Hu, Frank B.",
        orcid="0000-0002-8233-6274",
        discipline="Endocrinology & Metabolism",
        inst_2017="Harvard University",
        identity_check="matched",
        identity_note=("ORCID 0000-0002-8233-6274 载 Frank Hu 任职 Harvard Medical School / "
                       "Harvard T.H. Chan School of Public Health（2017）；库中机构=Harvard、"
                       "学科=内分泌代谢，一致。"),
        review_note="维基百科、Wikiwand、百度百科交叉写 1966 年生于湖北洪湖，且有中国本科教育，verified。",
        items=(
            _e(121, "encyclopedia",
               "https://ergngevd.top/wiki/Hu,_Frank",
               "Wikipedia - Frank Hu",
               "Frank B. Hu (Chinese: 胡丙长; pinyin: Hú Bǐngcháng; born 1966) is a Chinese American nutrition and diabetes researcher. ... A native of Hubei Province, China, Hu received his M.D. from the Tongji Medical University in Wuhan in 1988.",
               CLAIM_BIRTH_PLACE_CN, 40, "CN"),
            _e(121, "encyclopedia",
               "https://www.wikiwand.com/zh-my/articles/%E8%83%A1%E4%B8%99%E9%95%BF",
               "Wikiwand（维基百科镜像）- 胡丙长",
               "胡丙长于1966年10月出生于中国湖北省洪湖县（现洪湖市），1988年毕业于华中科技大学同济医学院。",
               CLAIM_BIRTH_PLACE_CN, 40, "CN"),
            _e(121, "encyclopedia",
               "https://baike.baidu.com/item/胡丙长/9322670",
               "百度百科 - 胡丙长",
               "胡丙长（Frank B. Hu），男，1966年10月出生，祖籍湖北洪湖……1988年，胡丙长获得同济医科大学公共卫生学院学士学位。",
               CLAIM_EDUCATION_CN, 25),
        ),
    ),
    Subject(
        scholar_id=802,
        display_name="Peng, Fang Z.",
        orcid="0000-0003-0827-333X",
        discipline="Electrical & Electronic Engineering",
        inst_2017="Michigan State University",
        identity_check="matched",
        identity_note=("ORCID 0000-0003-0827-333X 载 Fang Zheng 任职 Michigan State University（2000）、"
                       "Florida State（2018）、Pitt（2024）；库中机构=MSU、学科=电子电气，一致。"
                       "已排除同名干扰：SciProfiles 的 Fang Peng（1992 河北、山东大学）为另一人。"),
        review_note="百度百科写 1963 年出生于湖北；匹兹堡大学官方简介载武汉大学本科。出生地来源 1 个 + 官方教育，按规则判 probable（仍可入 treated）。",
        items=(
            _e(802, "encyclopedia",
               "https://baike.baidu.com/item/%E5%BD%AD%E6%96%B9/65412219",
               "百度百科 - 彭方正（Fang Zheng Peng）",
               "彭方正(Fang Zheng Peng),1963年出生于湖北……1983年本科毕业于原武汉水利电力学院电力工程系（现武汉大学电气与自动化学院）。",
               CLAIM_BIRTH_PLACE_CN, 40, "CN"),
            _e(802, "official",
               "https://engineering.pitt.edu/subsites/centers/energy/people/board/fang-peng",
               "University of Pittsburgh Swanson School of Engineering - Fang Peng",
               "Fang Z. Peng received the B.S. degree from Wuhan University, China in 1983 and the MS and Ph.D. Degrees from Nagaoka University of Technology, Japan in 1987 and 1990. ... since 2000 with academia: at Michigan State University (MSU), Florida State University (FSU) and now the University of Pittsburgh.",
               CLAIM_EDUCATION_CN, 45),
            # 同名排除（SciProfiles 的 Fang Peng，1992 河北、山东大学，为另一人）已写入
            # identity_note；不作为本研究对象的证据行，避免被规则判成本人身份反证。
        ),
    ),
    Subject(
        scholar_id=840,
        display_name="Sun, Shouheng",
        orcid="0000-0002-4051-0430",
        discipline="Nanoscience & Nanotechnology",
        inst_2017="Brown University",
        identity_check="matched",
        identity_note=("ORCID 0000-0002-4051-0430 载 Shouheng Sun 任职 Brown University；"
                       "库中机构=Brown、学科=纳米科学，一致。"),
        review_note="布朗大学官方 CV 载四川大学学士（1984）、南京大学硕士（1987）与讲师经历（1987-1992），但无明确出生地原文，按规则判 probable。",
        items=(
            _e(840, "official",
               "https://vivo.brown.edu/docs/s/ssun_cv.pdf",
               "Brown University VIVO - Shouheng Sun CV",
               "A. Education: Ph.D., Chemistry, Brown University, Providence, RI, 1996. M.Sc., Chemistry, Nanjing University, Nanjing, China, 1987. B.Sc., Chemistry, Sichuan University, Chengdu, China, 1984. ... Lecturer, 1987 - 1992, Coordination Chemistry Institute/Department of Chemistry, Nanjing University.",
               CLAIM_EDUCATION_CN, 45),
            _e(840, "encyclopedia",
               "https://www.wikiwand.com/en/Shouheng_Sun",
               "Wikiwand（维基百科镜像）- Shouheng Sun",
               "Shouheng Sun (Chinese: 孙守恒) is a Chinese-American chemist. ... Sun attended Sichuan University in Chengdu, graduating with a Bachelor of Science in chemistry in 1984. He completed a Master of Science in chemistry at Nanjing University.",
               CLAIM_EDUCATION_CN, 30),
        ),
    ),
    Subject(
        scholar_id=114,
        display_name="Wang, Joseph",
        orcid="0000-0002-4921-9674",
        discipline="Analytical Chemistry",
        inst_2017="University of California at San Diego",
        identity_check="matched",
        identity_note=("身份已核实为 UCSD 纳米工程 Joseph Wang：UCSD 官网 Bio-Sketch 与维基百科履历"
                       "（Technion 1978 DSc → Wisconsin → NMSU → ASU → UCSD 2008）与库中"
                       "分析化学、UCSD、firstyr=1977、高引完全一致。身份对得上，但证据不支持中国出生。"),
        review_note="算法假阳性：pinyin_strong(wang) + 合作署名中国信号误触发 high。公开履历无中国教育/任职，判 not_supported，从 treated 移除。",
        items=(
            _e(114, "official",
               "https://jacobs.ucsd.edu/people/profile/joseph-wang",
               "UC San Diego Jacobs School of Engineering - Joseph Wang",
               "Dr. Wang earned a D.Sc. from the Israel Institute of Technology (Haifa) in 1978. From 1978 to 1980 he served as a research associate at the University of Wisconsin (Madison), and between 1980 and 2004 he was a member of the Chemistry department at New Mexico State University ... Between 2004 and 2008 ... Arizona State University ... He joined UC San Diego's Jacobs School of Engineering in 2008.",
               CLAIM_NO_CN_IN_PUBLIC_CV, 50),
            _e(114, "encyclopedia",
               "http://wikipedia.com/w/index.php?title=Joseph_Wang&useparsoid=0",
               "Wikipedia - Joseph Wang (UCSD)",
               "Joseph Wang (born 1948) is an American biomedical engineer and inventor. ... Wang was awarded a D.Sc. from the Technion in 1978 and subsequently worked as a postdoctoral research associate at the University of Wisconsin, Madison until 1980.",
               CLAIM_NO_CN_IN_PUBLIC_CV, 35),
        ),
    ),
    Subject(
        scholar_id=358,
        display_name="Liu, Jie",
        orcid="0000-0003-0451-6111",
        discipline="Toxicology",
        inst_2017="(空)",
        identity_check="unmatched",
        identity_note=("消歧 match_level=B；source_scholar.inst_name 为空；ORCID 0000-0003-0451-6111 "
                       "载 Duke 1999 / Harvard 1991，但 Jie Liu 同名极多，无法确认公开传记属于库中同一人。"
                       "身份锚点不成立 → identity_unresolved，不臆造出生国。"),
        review_note="B 级消歧 + 机构空白 + 同名风险，无法锚定；保留 low 置信度候补，需人工履历核验后再定。",
        items=(
            _e(358, "encyclopedia",
               "https://baike.baidu.com/item/%E5%88%98%E6%9D%B0",
               "百度百科 - 刘杰（同名条目，仅作身份歧义说明）",
               "“刘杰”为常见中文姓名，百度百科存在多条同名人物条目；无法据此判定库中毒理学 Jie Liu 的出生地。",
               CLAIM_IDENTITY_MISMATCH, 0),
        ),
    ),
)


def all_items() -> list[EvidenceItem]:
    out = [item for subj in SUBJECTS for item in subj.items]
    for item in out:
        item.validate()
    return out
