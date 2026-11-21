-- ============================================================
-- 三人小样 · Apache AGE 图投影脚本
-- 前置：已执行 schema.sql + 三人小样_seed.sql
-- 环境：客户机器 Docker / WSL Ubuntu（AGE 已可用）
-- 投影内容：3 学者 / 3 国家 / 11 机构 / 4 履历 / 6 事件 + 关联边
-- 生成日期：2026-11-21
-- ============================================================

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- 0) 重建图（重复执行安全）
SELECT drop_graph('mobility', true);
SELECT create_graph('mobility');

-- 1) 标签
SELECT create_vlabel('mobility','Scholar');
SELECT create_vlabel('mobility','Institution');
SELECT create_vlabel('mobility','Country');
SELECT create_vlabel('mobility','CareerFact');
SELECT create_vlabel('mobility','Event');
SELECT create_elabel('mobility','AFFILIATED');
SELECT create_elabel('mobility','LOCATED_IN');
SELECT create_elabel('mobility','EXPERIENCED');
SELECT create_elabel('mobility','FROM_INST');
SELECT create_elabel('mobility','TO_INST');
SELECT create_elabel('mobility','FROM_COUNTRY');
SELECT create_elabel('mobility','TO_COUNTRY');
SELECT create_elabel('mobility','CORROBORATED_BY');
SELECT create_elabel('mobility','HAS_FACT');

-- 2) 属性索引（AGE 1.x 通用写法）
CREATE INDEX ON "mobility"."Scholar" USING btree ((properties->>'sid'));
CREATE INDEX ON "mobility"."Event" USING btree ((properties->>'eid'));
CREATE INDEX ON "mobility"."AFFILIATED" USING gin (properties);

-- 3) 学者节点（sid 与关系库 scholar_id 一致）
SELECT * FROM cypher('mobility', $$
  UNWIND [
    {sid:1, name:'Becke',   orcid:'0000-0002-6668-7728', h17:0},
    {sid:2, name:'Perdew',  orcid:'0000-0003-4237-824X', h17:0},
    {sid:3, name:'Newman',  orcid:'0000-0002-0907-1660', h17:0}
  ] AS r
  CREATE (s:Scholar {sid:r.sid, name:r.name, orcid:r.orcid, h17:r.h17})
  RETURN s
$$) AS (v agtype);

-- 4) 国家节点
SELECT * FROM cypher('mobility', $$
  UNWIND [
    {code:'CA', name:'加拿大'},
    {code:'US', name:'美国'},
    {code:'GB', name:'英国'}
  ] AS r
  CREATE (c:Country {code:r.code, name:r.name})
  RETURN c
$$) AS (v agtype);

-- 5) 机构节点（坐标已从源[经度,纬度]对调为 lat/lon）
SELECT * FROM cypher('mobility', $$
  UNWIND [
    {iid:1,  name:'McMaster University',                          city:'Hamilton',     lat:43.25011,           lon:-79.84963,           country:'CA'},
    {iid:2,  name:'Dalhousie University',                         city:'Halifax',      lat:44.64269,           lon:-63.57688,           country:'CA'},
    {iid:3,  name:'Queen''s University',                          city:'Kingston',     lat:44.22976,           lon:-76.48098,           country:'CA'},
    {iid:4,  name:'Cornell University',                           city:'Ithaca',       lat:42.44063,           lon:-76.49661,           country:'US'},
    {iid:5,  name:'University of Toronto',                        city:'Toronto',      lat:43.70643,           lon:-79.39864,           country:'CA'},
    {iid:6,  name:'Rutgers, The State University of New Jersey',  city:'New Brunswick',lat:40.48622,           lon:-74.45182,           country:'US'},
    {iid:7,  name:'Tulane University',                            city:'New Orleans',  lat:29.95465,           lon:-90.07507,           country:'US'},
    {iid:8,  name:'Temple University',                            city:'Philadelphia', lat:39.95238,           lon:-75.16362,           country:'US'},
    {iid:9,  name:'University of Oxford',                         city:'Oxford',       lat:51.75222,           lon:-1.25596,            country:'GB'},
    {iid:10, name:'Santa Fe Institute',                           city:'Santa Fe',     lat:35.686981201171875, lon:-105.93779754638672, country:'US'},
    {iid:11, name:'University of Michigan',                       city:'Ann Arbor',    lat:42.27756,           lon:-83.74088,           country:'US'}
  ] AS r
  CREATE (i:Institution {iid:r.iid, name:r.name, city:r.city, lat:r.lat, lon:r.lon, country:r.country})
  RETURN i
$$) AS (v agtype);

-- 6) 机构 -> 国家 边
SELECT * FROM cypher('mobility', $$
  MATCH (i:Institution), (c:Country)
  WHERE i.country = c.code
  CREATE (i)-[:LOCATED_IN]->(c)
  RETURN i.name
$$) AS (v agtype);

-- 7) 年度任职轨迹边 AFFILIATED（机构变化点，来自 Excel 轨迹矩阵）
--    Becke: McMaster(1977-82) -> Dalhousie(1983-85) -> Queen's(1986-2006) -> Dalhousie(2007-)
--    Perdew: Cornell(1970-73) -> Toronto(1974) -> Rutgers(1976-78) -> Tulane(1979-2013)
--            -> Temple(2014-2023) -> Tulane(2024-)
--    Newman: Oxford(1991) -> Cornell(1992-96) -> Santa Fe(1997-2002) -> Michigan(2003-)
SELECT * FROM cypher('mobility', $$
  UNWIND [
    {sid:1, iid:1,  y:1977}, {sid:1, iid:2,  y:1983}, {sid:1, iid:3,  y:1986}, {sid:1, iid:2,  y:2007},
    {sid:2, iid:4,  y:1970}, {sid:2, iid:5,  y:1974}, {sid:2, iid:6,  y:1976}, {sid:2, iid:7,  y:1979},
    {sid:2, iid:8,  y:2014}, {sid:2, iid:7,  y:2024},
    {sid:3, iid:9,  y:1991}, {sid:3, iid:4,  y:1992}, {sid:3, iid:10, y:1997}, {sid:3, iid:11, y:2003}
  ] AS r
  MATCH (s:Scholar {sid:r.sid}), (i:Institution {iid:r.iid})
  CREATE (s)-[:AFFILIATED {year:r.y, role:'primary', share:1.0, is_imputed:false, rv:'A_base_060'}]->(i)
  RETURN s.name
$$) AS (v agtype);
-- 注：AFFILIATED 边只取"机构变化点"，完整 139 年面板仍在关系库；图仅存轨迹拐点。

-- 8) 外部履历节点（4 条，真实 URL 来自 B时间差诊断）
SELECT * FROM cypher('mobility', $$
  UNWIND [
    {fid:1, sid:2, y:2023, src:'award', url:'https://fi.edu/en/awards/laureates/john-p-perdew'},
    {fid:2, sid:3, y:1991, src:'cv',    url:'https://www-personal.umich.edu/~mejn/Newman_CV.pdf'},
    {fid:3, sid:3, y:1996, src:'cv',    url:'https://www-personal.umich.edu/~mejn/Newman_CV.pdf'},
    {fid:4, sid:3, y:2002, src:'cv',    url:'https://www-personal.umich.edu/~mejn/Newman_CV.pdf'}
  ] AS r
  MATCH (s:Scholar {sid:r.sid})
  CREATE (f:CareerFact {fid:r.fid, year:r.y, source_type:r.src, url:r.url})
  CREATE (s)-[:HAS_FACT]->(f)
  RETURN f
$$) AS (v agtype);

-- 9) 事件节点 + 起止边（6 条，与 mobility_event 逐条对应）
SELECT * FROM cypher('mobility', $$
  UNWIND [
    {eid:1, sid:1, type:'C1', py:1986, ay:null, lvl:'Possible'},
    {eid:2, sid:1, type:'C1', py:2007, ay:null, lvl:'Possible'},
    {eid:3, sid:2, type:'C1', py:2024, ay:2023, lvl:'Confirmed'},
    {eid:4, sid:3, type:'C2', py:1992, ay:1991, lvl:'Confirmed'},
    {eid:5, sid:3, type:'C1', py:1997, ay:1996, lvl:'Confirmed'},
    {eid:6, sid:3, type:'C1', py:2003, ay:2002, lvl:'Confirmed'}
  ] AS r
  MATCH (s:Scholar {sid:r.sid})
  CREATE (e:Event {eid:r.eid, type:r.type, paper_year:r.py, actual_year:r.ay,
                   level:r.lvl, rv:'A_base_060'})
  CREATE (s)-[:EXPERIENCED]->(e)
  RETURN e
$$) AS (v agtype);

-- 10) 事件 -> 起止机构（显式 eid→机构映射，避免 Cypher 模式内 CASE）
SELECT * FROM cypher('mobility', $$
  UNWIND [
    {e:1, f:1,  t:3}, {e:2, f:3,  t:2}, {e:3, f:8,  t:7},
    {e:4, f:9,  t:4}, {e:5, f:4,  t:10}, {e:6, f:10, t:11}
  ] AS r
  MATCH (ev:Event {eid:r.e}), (fi:Institution {iid:r.f}), (ti:Institution {iid:r.t})
  CREATE (ev)-[:FROM_INST]->(fi)
  CREATE (ev)-[:TO_INST]->(ti)
  RETURN ev.eid
$$) AS (v agtype);

-- 11) 事件 -> 起止国家（仅 C2 有跨国，其余起止同国；此处全部显式落边）
SELECT * FROM cypher('mobility', $$
  UNWIND [
    {e:1, f:'CA', t:'CA'}, {e:2, f:'CA', t:'CA'}, {e:3, f:'US', t:'US'},
    {e:4, f:'GB', t:'US'}, {e:5, f:'US', t:'US'}, {e:6, f:'US', t:'US'}
  ] AS r
  MATCH (ev:Event {eid:r.e}), (fc:Country {code:r.f}), (tc:Country {code:r.t})
  CREATE (ev)-[:FROM_COUNTRY]->(fc)
  CREATE (ev)-[:TO_COUNTRY]->(tc)
  RETURN ev.eid
$$) AS (v agtype);

-- 12) 事件 -> 履历证据（Confirmed 的 4 条）
SELECT * FROM cypher('mobility', $$
  UNWIND [{e:3, f:1}, {e:4, f:2}, {e:5, f:3}, {e:6, f:4}] AS r
  MATCH (ev:Event {eid:r.e}), (cf:CareerFact {fid:r.f})
  CREATE (ev)-[:CORROBORATED_BY]->(cf)
  RETURN ev.eid
$$) AS (v agtype);

-- ============================================================
-- 验证查询（跑通即表示图原型成功）
-- ============================================================

-- V1 学者节点数：预期 3
SELECT * FROM cypher('mobility', $$
  MATCH (s:Scholar) RETURN count(s)
$$) AS (n agtype);

-- V2 Newman 完整轨迹（含经纬度，可直接喂地图）
SELECT * FROM cypher('mobility', $$
  MATCH (s:Scholar {name:'Newman'})-[a:AFFILIATED]->(i:Institution)
  RETURN a.year AS year, i.name AS inst, i.city AS city, i.lat AS lat, i.lon AS lon
  ORDER BY a.year
$$) AS (year agtype, inst agtype, city agtype, lat agtype, lon agtype);
-- 预期 4 行：1991 Oxford / 1992 Cornell / 1997 Santa Fe / 2003 Michigan

-- V3 事件 + 起止机构 + 履历证据一跳取回
SELECT * FROM cypher('mobility', $$
  MATCH (s:Scholar)-[:EXPERIENCED]->(e:Event)
  MATCH (e)-[:FROM_INST]->(fi:Institution), (e)-[:TO_INST]->(ti:Institution)
  OPTIONAL MATCH (e)-[:CORROBORATED_BY]->(f:CareerFact)
  RETURN s.name AS who, e.type AS type, e.paper_year AS py,
         e.actual_year AS ay, e.level AS lvl,
         fi.name AS fromInst, ti.name AS toInst, f.url AS evidence
  ORDER BY py
$$) AS (who agtype, type agtype, py agtype, ay agtype, lvl agtype,
        fromInst agtype, toInst agtype, evidence agtype);
-- 预期 6 行；Becke 2 行 ay 为空、lvl=Possible、evidence 为空；其余 4 行有履历

-- V4 Newman 的 C2 国际迁移（1992 GB->US）
SELECT * FROM cypher('mobility', $$
  MATCH (s:Scholar {name:'Newman'})-[:EXPERIENCED]->(e:Event {type:'C2'})
  MATCH (e)-[:FROM_COUNTRY]->(fc:Country), (e)-[:TO_COUNTRY]->(tc:Country)
  RETURN s.name, e.paper_year, e.actual_year, fc.name, tc.name
$$) AS (who agtype, py agtype, ay agtype, fromC agtype, toC agtype);
-- 预期：Newman, 1992, 1991, 英国, 美国

-- V5 事件数：预期 6
SELECT * FROM cypher('mobility', $$
  MATCH (e:Event) RETURN count(e)
$$) AS (n agtype);

-- ============================================================
-- END
-- ============================================================
