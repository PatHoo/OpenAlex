# 本地软件协同数据模板（第二期）

本目录由 `pipeline/export_local_tools.py` 生成，供 SPSS / Stata / ArcGIS 本地调度使用。
数据为 1,000 人试跑快照，不代表全量交付。脚本仅生成标准输入与调用模板，不会自动执行本机软件，也不伪造软件输出。

- `data/did_panel.csv`：DID 组-年面板
- `data/did_robust_estimate.csv` / `did_robust_annual_effect.csv`：聚类稳健估计与逐年效应
- `data/mobility_event_sample.csv`：事件样本（年份多为署名代理年）
- `data/mobility_class_summary.csv`：M1–M8 分类分布
- `data/institution_points.csv` / `country_flows.csv`：机构点与国家流向
- `run_spss.sps` / `run_stata.do` / `arcgis_publish.py`：本地脚本模板
