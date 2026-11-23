* OpenAlex 科学家流动数据库 — SPSS 语法模板（第二期：本地软件协同）
* 读取标准化 CSV，输出频数表与交叉表；请在 SPSS Statistics 中打开运行。
GET DATA /TYPE=TXT
  /FILE='/mnt/c/Users/quith/Desktop/OpenAlex/deliver/local_tools/data/did_panel.csv'
  /DELIMITERS=","
  /QUALIFIER='"'
  /ARRANGEMENT=DELIMITED
  /FIRSTCASE=2
  /VARIABLES=
    scholar_id F8.0
    year F4.0
    treated F1.0
    post F1.0
    outcome_moves F8.0
    outcome_intl_moves F8.0
    outcome_cn_link F8.0
    outcome_log_works F12.4.
CACHE.
EXECUTE.

FREQUENCIES VARIABLES=treated post /ORDER=ANALYSIS.
CROSSTABS TABLES=outcome_moves BY treated
  /CELLS=COUNT ROW COLUMN /STATISTICS=CHISQ.
MEANS TABLES=outcome_moves outcome_intl_moves BY treated /CELLS=MEAN COUNT STDDEV.
