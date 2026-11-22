# 资料摘要与数据来源（供整理稿引用）

本文件记录验收质疑清单所依据的全部数字与事实出处，确保每个引用值可追溯到源文件的具体 sheet / 段落。资料均来自当前工作区的 `reference/` 目录（原归档路径 `C:/Users/Administrator/Desktop/AIGC/OpenAlex/` 仅作历史溯源，不是可执行路径）。

## 1. （需求清单）.doc
- 6 大需求模块：数据获取清洗校正、实验数据分析（含可视化、DID）、文献、论文写作、本地化部署与交付（第 6、7 行为空）。
- 分 4 期付款；工期标"双方确认"。
- 验收样例：获取约 20 万高引学者空间迁移轨迹、筛"中国出生、现阶段在美国居留"学者、用 DID 检验 2017-2018 中美科技战在美学者流动影响。

## 2. 科学家空间流动数据库建立操作1030.docx（2026-10-31 修订稿）
- 研究对象：Elsevier 2017 全球高被引 105,027 名（1.1 节）。
- 数据源调整：从 Scopus 转向 OpenAlex 为核心 + ROR + ORCID + Crossref/DataCite；Scopus 降为"非依赖性参考来源"（1.2 节）。
- 七工作包 WP0–WP6（二节）。
- C1–C5 事件定义（8.1.2）：C1 国内机构迁移；C2 国际迁移（优先于 C1）；C3 新增次级国家；C4 原主国家降级次级并持续（pending/confirmed/not_sustained）；C5 机构改名不计迁移。
- 验证三指标（8.2.1）：连续两个日历年主导、署名占比≥60%、累计≥3 篇不同有效论文 → Probable；不足 → Possible；外部履历确认 → Confirmed；访问/兼职/数据错误 → Rejected。
- 履历年份允许 ±2 年差异，匹配后以履历年覆盖论文年（8.2.2）。
- 禁用 LOCF 填充年作事件或持续性证据（8.1.2 规则 8）。
- D/I/S 三维 → M1–M8 八类学者（8.5）。

## 3. Table-S1-career-2017.xlsx
- Key 表：43 字段说明。
- Career 表：105,027 行 × 42 列；字段含 authfull、inst_name、cntry、np6017、firstyr、lastyr、h17、c 等。

## 4. 1030版_新三学者基线与增量对照数据.xlsx（25 个 sheet）
- 说明与方法归属：A=原名单与新三人（新抓取）；方案基线 60% 归 A；阈值 50%/70% 归 B（独立重算不覆盖 A）；时间差诊断/证据卡归 B 复用 A 证据；重要限制=A/B 两人分类不确定、ROR/原文恢复不完整、未用户审计。
- A身份映射：Becke S000016（107 篇，1977-2024）、Perdew S000017（535 篇，1970-2026）、Newman S000028（371 抓取/370 去重，1991-2026）；均 A 级、均有 ORCID。`original_row` 分别为 17/18/29，与 SQL 种子中 `source_scholar.src_id` 一致；S 编号与 original_row 相差 1，不是数据错误。
- A生涯汇总：Becke D=1/I=0/S=1，观测 M6、正式 mobility_class 空、分类不确定，7 个未观测年、1 个 Possible；Perdew D=1/I=0/S=1，观测 M6、正式类空、分类不确定，3 个未观测年、3 个并列年、1 个 Possible；Newman D=1/I=1/S=1，M8、可分类（观测期内）、跨国迁移型、0 缺失、0 并列、0 Possible。
- A实施口径：R5 预印本跨版本未证实同作品不自动合并、风险另报；R6 同 DOI 保留结构化署名更丰富记录；R9 并列保留不强选；R10 S_tie_policy 唯一主国家且有他国有效机构署名才给确证 1；R11 分母与并列口径"非用户原文已明确的参数"，另列敏感性不改原阈值；R12 now_utc=2026-11-01T02:12:41Z。
- A基线请求清单：64 条请求记录（65 行含表头），状态为 200×62 / 401×1 / 404×1（原摘要误记为 429），含 sha256、时间戳（2026-11-01T02:07:16Z–02:15:00Z，原摘要误记为 02:10 截止）；后端根目录 `/srv/catsco-agent/work/artifact-user-ledger/tmp/1030-fresh/`，但清单未给出逐条响应文件路径。已由 `design/api_request_seed.sql` 全量迁入 `api_request`。
- A补查Crossref：156 条记录（157 行含表头），多条 matched_author_no_affiliation / target_author_not_matched / http_error（429×9、301×4、404×26）；有 `raw_path` 可作 `backend_path`，无时间戳/sha256/字节数。已按用户确认的 DOI 推导规则（`https://api.crossref.org/works/{percent-encoded-doi}`）由 `design/api_request_crossref_seed.sql` 迁入 `api_request`。
- A最终有效迁移时间表：6 条迁移（7 行含表头）。Becke 2 条 C1 用 signature_proxy、actual_migration_year 空、gap=1；Perdew 1 条 C1（2024 署名 / 2023 实际，external_actual）；Newman 1 条 C2（1992/1991）+ 2 条 C1（1997/1996、2003/2002），均 external_actual、gap=1。
- A事件审计 / B逐事件阈值对照：6 个有效事件的 `support_work_ids`、`validation_years`、`auto_grade`、`external_status`、`external_evidence` 与 B50/B70 敏感性结果，已由 `design/event_evidence_seed.sql` 迁入 `event_evidence`（6 事件 × 5 判据 = 30 行）。逐事件份额值原表未给出，`share_threshold.measured_value` 留空。
- B时间差诊断：4 条，差值均 +1 年；来源 fi.edu 获奖页、umich 个人主页；标注"B 诊断复用 A 证据"。
- A不确定转换：6 条（7 行含表头），均 Perdew，原因"相邻实观测主状态有并列，不强定迁移方向"（1974-75、1975-76、1977-78、1978-79、2012-13、2013-14）。
- A可能机构名称变化观察：112 条（113 行含表头）。
- A次级联系持续终止：19 条（20 行含表头），end_status=not_secondary_observed。
- ADOI去重记录：1 条（2 行含表头），Newman 同 DOI。
- 规则测试：19 项（20 行含表头），含 test_c1 / test_c2_suppresses_c1 / test_c3 / test_c4 / test_c5 / test_eight_classes / test_threshold_boundary 等，状态 passed。已由 `design/rule_test_seed.sql` 迁入 `rule_test`，原始 JSON 保留在 `detail`。
- B生涯对照：6 条（7 行含表头），B_占比敏感性_0.5 与 _0.7 各 3 条；0.7 下 Becke 国内迁移由 2 降为 1、Possible 由 1 升为 2。
- A轨迹_Newman：主国家 1991 GB → 1992 US；主机构 Oxford→Cornell(1992)→Santa Fe(1997)→Michigan(2003)；含城市与经纬度；最终事件 1992:C2 / 1997:C1 / 1998:C3 / 2003:C1 / 2016:C3。

## 5. 三份 PDF 报告（扫描件，无文字层，按页视觉读取）
- 1030版测试报告.pdf（2 页）：标题"1030新三人·测试报告（结果与证据边界）"；19 项规则测试通过、C5 错误映射已修复；结论"只完成小样本，不可宣称全方案通过"。
- 1030版满足度说明.pdf（2 页）：标题"1030新三人·满足度说明（结果与证据边界）"；逐条比对需求与已完成项，明确未完成全量与其他模块；证据边界声明。
- 1030版逐人轨迹核查报告.pdf（3 页）：标题"1030新三人·逐人轨迹核查报告（结果与证据边界）"；逐人核查 Becke/Perdew/Newman 的轨迹与外部证据，列出不确定项与限制。

## 6. 2026-11-22 本机 1,000 人试跑落库（本次新增施工）
> 来源：`pipeline/README.md`、`README.md`「1,000 人试跑落库」节、`.workbuddy/memory/2026-11-22.md` 工作日志，均为本机原生 PostgreSQL 17（端口 5432、`openalex` 库）实查结果；非甲方提供的 1030 版资料。

- 工作包 WP0–WP8 全部跑通：名单导入 → OpenAlex 抓取 → 作品落库 → 消歧定案 → 署名落库 → 机构/ROR → 机构回填。
- 抓取：1,000/1,000 学者完成（src_id 2–1001 连续），作品 922,627 篇，API 请求 10,760 次（10,759×200 + 1 条旧 key 遗留 429）。
- 落库（约 1.36 GB）：`work` 865,111（按 openalex_id 去重）；`scholar` 896（3 种子 + 893 新）；`identity_map` 1,700（openalex 896 = A718/B178，orcid 804）；`name_variant` 893；`authorship` 846,012（覆盖全部 896 学者）。
- 机构：`country` 203；`institution` 31,388（全部带 ROR）；`institution_alias` 1,815,799（全 confirmed）；`authorship.matched_institution_id` 已回填 646,069 条（占全部署名 76.37%、占有署名串的 674,046 条的 95.85%），孤儿外键 0。
- 消歧口径：严格 A 级 + 每组重复行只建 h17 最高主行 + A/B 都建映射；dry-run 分级 A=710/B=178/HOLD=98/DUP=11/SEED=3。HOLD（98 人）与 B 级（178 人）留待人工复核。
- 数据质量提示：Druker（src 466）有 25,313 篇署名，源于 OpenAlex 该身份规模（抓取阶段即如此），非落库重复；有署名串但未匹配到机构的署名 27,977 条。

**证据边界（写入质疑清单的关键）**：本次落库只完成「数据地基」层——作品、学者、署名、机构已入库；**年度面板、年度主机构/主国家、C1–C5 候选事件、M1–M8 分类、中国学者筛选、DID 均尚未在 1,000 人上运行**。因此不能据此宣称「1,000 人迁移轨迹库」已完成，更不能外推为全量交付。

## 统计口径说明
- 所有行数均按 openpyxl `max_row` 读取，记录数 = max_row − 1（扣除表头）。
- 本整理稿未做分组聚合计算，数字直接取自上述读取结果，未心算、未估算。
- 第 6 节数字取自本机数据库实查与工作日志，已在 `pipeline/README.md` 与根 `README.md` 交叉核对。
