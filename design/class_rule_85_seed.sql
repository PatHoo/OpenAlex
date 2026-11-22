-- 8.5.2 八类学者流动类型的 D/I/S -> M1..M8 权威映射
-- 来源：reference/科学家空间流动数据库建立操作1030.docx 8.5.2 节原文
--   D=国内迁移(C1 Confirmed/Probable)、I=国际迁移(C2 Confirmed/Probable)、S=跨国次级隶属
-- 说明：schema.sql 原只写入 (1,1,1)->M8 一条，且 class_rule 主键是 (d,i,s)，
--       故重建为 8 条全组合，source_section 统一标 '8.5.2' 便于追溯。
--       mobility_class 未对 class_rule 建外键，重建无 FK 风险。
-- 幂等：全表删除后插回固定 8 条。
BEGIN;
DELETE FROM class_rule;
INSERT INTO class_rule(d_flag,i_flag,s_flag,class_code,label_zh,source_section) VALUES
 (0,0,0,'M1','非流动者','8.5.2'),
 (1,0,0,'M2','纯国内迁移者','8.5.2'),
 (0,1,0,'M3','纯国际迁移者','8.5.2'),
 (0,0,1,'M4','纯跨国兼职或多重隶属者','8.5.2'),
 (1,1,0,'M5','国内与国际迁移混合者','8.5.2'),
 (1,0,1,'M6','国内迁移与跨国兼职混合者','8.5.2'),
 (0,1,1,'M7','国际迁移与跨国兼职混合者','8.5.2'),
 (1,1,1,'M8','国内迁移、国际迁移与跨国兼职混合者','8.5.2');
COMMIT;

SELECT d_flag,i_flag,s_flag,class_code,label_zh,source_section
FROM class_rule ORDER BY d_flag,i_flag,s_flag;
