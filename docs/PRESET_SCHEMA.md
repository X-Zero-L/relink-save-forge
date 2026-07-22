# 预设结构

Relink Save Forge 使用两层 JSON：配装数据描述允许写入的装备内容，pack manifest 描述一组可执行的离线转换。两者都不包含用户存档、SteamID 或本机路径。

## 因子预设

### 标准合法 Lv15（schema v2）

`presets/sigil-preset-schema-v2.json` 描述正常版 29 人配装：

- `schema_version=2`、`game_data_version="Relink 2.0.2"`；
- `legality_mode="database_rows_only"`：每个外壳、主词条和副词条组合必须来自 `catalogs/sigil-legal-pairs-2.0.2.json` 的真实 `gem` 数据库行；
- `outer_level=15`、`lane_level_max=15`：所有非空内部 lane 都精确为 Lv15，不允许 99；
- `skill_cap_catalog`：引用 `catalogs/skill-level-caps-2.0.2.json`，按角色聚合同一技能的所有 lane，并要求总等级不超过对应 `max_total_level`；
- `characters[].sigils`：29 名角色各 12 枚；单词条因子的 `secondary` 为 `null`，写盘时必须成为空哈希和等级 0；
- `database_key`、`outer_id/hash`、`primary`、`secondary` 必须与合法组合清单逐项一致；
- `build_sha256` 固定完整 29×12 规范化构建，防止只换显示外壳而保留旧内部属性。

当前标准预设：

- `presets/sigils/standard-endgame-output-2.0.2.json`：输出取向；
- `presets/sigils/standard-endgame-qol-2.0.2.json`：生存与操作便利取向；
- `presets/weapons/endgame-qol-blessing-standard-2.0.2.json`：Lv15 Quick Cooldown/Cascade/Stout Heart；
- `presets/weapons/endgame-survival-blessing-standard-2.0.2.json`：Lv15 Cascade/Nimble Onslaught/Greater Aegis。

标准 complete 包先用已有非零因子实例为 29 名角色建立各 12 个可解析链接，再创建武器和四颗召唤石；召唤创建使用 `catalogs/top-summons-2.0.2.json` 中合法的 `15/9` 被动/基础加成 lane。标准 existing-only 包不执行因子链接修复，只重配已有因子/武器关系和已存在的四颗目标召唤石。

### 显式老金 Lv99（schema v1）

`presets/sigil-preset-schema.json` 描述 `presets/sigils/latest-endgame-gold-2.0.2.json`。它是明确标记的修改器预设：

- `outer_level=15`，内部 `trait_level=99`；
- 29 名角色各 12 枚因子、24 个唯一 Lv99 词条；
- 同时包含 α、β、γ、守护、金刚、躲避性能和真正的摇曳步；
- 中文“摇曳步”是 `SKILL_159_00`（Flight over Fight），`SKILL_150_00` 是“躲避距离”（Untouchable），两者不能混用；
- `outer_id/primary_id/secondary_id` 与全部 `*_hash` 必须同时匹配，验证器拒绝只改外壳；
- `build_sha256` 固定 29×12 完整配装摘要。

老金组合还引用：

- `presets/weapons/endgame-qol-blessing-2.0.2.json`：三条 Lv99 武器祝福；
- `presets/summons/endgame-qol-passives-2.0.2.json`：四颗现有召唤石的合法满级被动。

只有 ID 明确带 `gold`、显示名和说明明确标记 `Gold` 与 `Lv99` 的 pack 才会写入 99 级因子或武器祝福。

## 一键预设包

`presets/pack-schema.json` 描述 pack manifest。`presets/packs/*.json` 会显示在 Windows 一键菜单中。

- `id/name/description`：稳定标识、显示名和准确范围；
- `invariants`：要求保持 Steam 头、payload 大小和记录数；
- `steps[].kind`：`transform` 生成下一份候选，`verify` 只检查当前输入；
- `steps[].command`：不经过 shell 的参数数组；
- `timeout_seconds`：单阶段超时；
- `audit_required`：阶段是否必须生成 JSON 审计。

命令可使用 `{python}`、`{input}`、`{output}`、`{audit}`、`{root}`、`{editor_root}`、`{run_dir}` 和 `{save_dir}`。一键入口会在独立运行目录展开占位符，完整执行两遍并要求最终文件字节级一致，然后才允许原子部署。

## v1.1.0 的 12 个 pack

| 类别 | ID | 行为 |
| --- | --- | --- |
| Existing-only | `standard-endgame-output` | 正常 Lv15 输出配装；只使用现有武器与召唤实例。 |
| Existing-only | `standard-endgame-qol` | 正常 Lv15 生存/QoL 配装；只使用现有武器与召唤实例。 |
| Existing-only | `latest-endgame-gold` | 显式 Lv99 老金配装；只使用现有武器与召唤实例。 |
| Existing-only | `mainline-safe-endgame` | 资源 900、全命运篇章和 existing-only 老金配装。 |
| Auto-fill complete | `standard-complete-output` | 启用角色、完成 Fate、修复 29×12 因子链接、补齐武器与召唤石，再应用正常 Lv15 输出配装。 |
| Auto-fill complete | `standard-complete-qol` | 同样补齐，再应用正常 Lv15 生存/QoL 配装。 |
| Auto-fill complete | `gold-complete` | 同样补齐，再应用显式 Lv99 老金配装。 |
| Standalone | `unlock-all-characters` | 只 OR 29 个角色行 `1305` 的自然激活 mask。 |
| Standalone | `complete-armory` | 创建缺少的正式武器、完成支持的终盘规格并装备最强武器。 |
| Standalone | `create-top-four-summons` | 创建、解锁并装备四颗终盘召唤石。 |
| Standalone | `resources-900` | 329 种普通堆叠物精确设为 900。 |
| Standalone | `fate-episodes-all` | 完成 319 条 Fate 与 56 个有效任务计数。 |

三个 complete pack 在 `fate-episodes-all` 后、武器/召唤创建和配装步骤前加入 `ensure-sigil-loadouts`。该阶段不创建因子实例，只选择已有非零实例，保留其 `2702` 实例 ID、外壳、等级、flags 和内部 trait lanes，并仅规范化每名角色前 12 个 `1403` 引用及所选实例的 `2706` owner。非目标 `1403` 引用、目标角色尾槽引用和未选中的 owner 必须保留，并从补位候选中排除。最终必须得到 29×12 个全局唯一、可解析且不与尾槽重叠的链接；实例不足或链接仍有歧义时安全失败。`standard-endgame-output`、`standard-endgame-qol`、`latest-endgame-gold` 以及调用 existing-only 老金流程的 `mainline-safe-endgame` 都不执行这一阶段。

所有 pack 必须保持主线字段 `2510/2511/2520/2522` 逐项不变。Existing-only 召唤步骤保留现有 `1460`；创建 pack 使用四颗目标在游戏内保存后规范化的 `1460=6`，但不宣称该字段语义已经解码。武器创建只使用规范空槽，保留未知或模组实例，并拒绝重复实例 ID、重复正式武器和非规范半空壳。
