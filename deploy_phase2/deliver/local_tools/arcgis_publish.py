"""OpenAlex 科学家流动数据库 — ArcGIS Pro / ArcPy 模板（第二期：本地软件协同）。

在已配置 ArcPy 的 Python 环境中运行。只生成本地要素类，不依赖在线底图服务。
坐标为 WGS84；如需与国内合规底图叠加，请在 ArcGIS 中执行坐标转换为 GCJ-02。
"""
from pathlib import Path
import arcpy

DATA = Path(r"/mnt/c/Users/quith/Desktop/OpenAlex/deliver/local_tools/data")
OUT_GDB = DATA.parent / "openalex_local.gdb"

if not arcpy.Exists(str(OUT_GDB)):
    arcpy.management.CreateFileGDB(str(DATA.parent), "openalex_local.gdb")

inst = DATA / "institution_points.csv"
if inst.exists():
    arcpy.management.CopyFeatures(
        str(inst), str(OUT_GDB / "institution_points")
    )
    arcpy.management.DefineProjection(str(OUT_GDB / "institution_points"), 4326)

flow = DATA / "country_flows.csv"
if flow.exists():
    arcpy.management.CopyRows(str(flow), str(OUT_GDB / "country_flows"))

print("ArcGIS local feature classes ready:", OUT_GDB)
