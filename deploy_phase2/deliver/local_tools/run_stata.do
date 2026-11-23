* OpenAlex 科学家流动数据库 — Stata do-file 模板（第二期：本地软件协同）
* 复算 DID 面板描述统计与基准回归。样本为 1,000 人试跑，结论仅供流程验证。
import delimited "/mnt/c/Users/quith/Desktop/OpenAlex/deliver/local_tools/data/did_panel.csv", clear varnames(1) encoding(utf8)
describe
summarize outcome_moves outcome_intl_moves outcome_cn_link outcome_log_works
tabulate treated post
regress outcome_moves i.treated##i.post, vce(cluster scholar_id)
regress outcome_intl_moves i.treated##i.post, vce(cluster scholar_id)
import delimited "/mnt/c/Users/quith/Desktop/OpenAlex/deliver/local_tools/data/did_robust_annual_effect.csv", clear varnames(1) encoding(utf8)
twoway (line coefficient year, sort) (rline ci_low ci_high year, sort), \
  title("Event study: moves_any") yline(0) xtitle("Year") legend(off)
