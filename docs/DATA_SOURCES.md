# 数据来源与版本

## 本次生成基准

- 游戏数据解析器：[Nenkai/GBFRDataTools](https://github.com/Nenkai/GBFRDataTools)，提交 `571a1d1ce71c17601684894dad186269c0fed1dc`。
- 补充因子/词条研究：[alexfrljuckic/GBFRelinkMod](https://github.com/alexfrljuckic/GBFRelinkMod)，提交 `c9bd8350e6deb3a3034194fe6fbf62cd453989e9`。
- `gbfr-live.db` SHA-256：`7A721A9B1822C2C7660C71F653B2FA1B3BE2DAFC4049B6FD4178BA78E5E96789`。
- `gbfr-live-items.db` SHA-256：`4AF30C37143AE15FEF4930A4C7D0FF06B2CC59B8F0EC5F8E17204DB12DFFB59A`。
- 2.0 武器 rebuild 提取库 SHA-256：`1D013CD6E99DF8C4D9027C31BD7DCE95177F3F88830F41FA2D2C72B9DC681791`。
- 2.0 `fate_episode` 提取库 SHA-256：`350AFCE0FB0A0C784EA3F4EF05B19426F6BA4CBCAFDCC5C18B765AC878B70143`。
- 原始 `fate_episode.tbl` SHA-256：`9B6AAF0748A19B5C51FE298F5C062DDEB987EA158423BF116A7E2B4201AA5EF6`。

数据库文件和原始 `.tbl` 不随仓库发布。摘要用于确认清单由哪一份本地提取数据生成，不代表游戏厂商版本签名。GBFRDataTools 与其他上游项目仍受各自许可证约束；本仓库不会把上游代码或游戏数据重新许可为 MIT。

## 清单筛选规则

### 角色（29）

`chara` 表中：

- `CharId LIKE 'PL%'`
- `IsNPC = 0`
- `MaxLevelMaybe = 100`

名称字段原则上保留数据库本地化 key，避免把未验证的社区译名当作正式名称。`PL2900 = Fediel` 是由游戏内 UI 与对应存档记录交叉确认的实机映射；`PL2800` 不再标记为 Fediel。

### 武器（174）

对每名角色生成候选 `WEP_<CharId>_01` 至 `_09`，但只有与 `weapon.Key` 的明文值或自定义哈希真实匹配的数据库基础行才能进入清单。候选生成只用于反查，不允许把未匹配的连续编号当成真实武器写入存档。

`PL2100`、`PL2200`、`PL2300` 均不存在 `_05`；它们的第六把正式武器是 `_07`。觉醒阶段行、模型占位和调试/额外索引不重复计数。

### 2.0 因子

从 `gem` 表选择 `Rarity = 5` 且满足以下任一条件的每一条数据库行：

- 角色专属家族 `GEEN_173`–`GEEN_178`；
- 2.0 通用/天星家族 `GEEN_320`–`GEEN_327`。

同一显示 GBID 可能对应多个不同第二词条数据库行。清单保留 `database_key`、`skill_id_1` 和 `skill_id_2`，不会错误地把它们合并成一个实例。

`scripts/generate_latest_sigil_preset.py` 进一步从完整 `chara/gem` 表生成不依赖 SQLite 的 29 人运行时预设。预设对每个外壳、主词条和副词条分别保存 ID 与哈希，并固定验证 α=`SKILL_160_00`、β=`SKILL_161_00`、γ=`SKILL_162_00`、中文摇曳步=`SKILL_159_00`。`SKILL_150_00` 是独立的躲避距离/Untouchable，不能作为摇曳步替代。

完整终盘预设还使用两类实机结构证明：武器祝福由 `2816` 外壳哈希和物理武器对应的三条 `130m` 运行时 lane 共同组成，库存 `210x/140m` 与武器没有实例链接；召唤石每颗只有一个通用技能被动和一个基础加成槽，四颗目标被动必须分别存在于对应 `summon_lot`，等级必须存在于对应 `summon_curve`。未知召唤字段 `1460` 不从稀有度推导，默认完整保留。

### 普通堆叠物（329 条）

`scripts/generate_stackable_catalog.py` 使用与材料重建器相同的可见性、分类、重要物品和内部占位排除规则，从 `item` 表生成 `catalogs/stackable-items-2.0.2.json`。运行时目录包含 329 种普通材料/消耗品和 8 种 `ITEM_23_*` 解锁卷；数量预设精确写为 900，而不是“至少 900”，避免 999 阻断需要消耗卷轴的任务。

### 命运篇章（324 条数据库记录）

`scripts/generate_fate_episode_catalog.py` 读取完整 `fate_episode` 表，并用 `scripts/gbfr_hash.py` 的 GBFR 自定义 XXHash32 把文本 `Key` 转成存档键。生成结果必须精确包含：

- 319 条真实 `FATE_*`，覆盖 29 名角色、每名 11 篇；
- 5 条 `REMI_*` 辅助记录，只用于完整性核对，不能进入完成集合；
- 58 条非零 `MissionQuestId` 引用，对应 56 个唯一任务 ID，其中 2 个任务由 `PL0000/PL0100` 共享。

存档并不按数据库行号排列。`3501/3502` 各有 820 条记录，除上述 324 条真实数据库 Key 外，还有 496 条固定空占位（`3501=887AE0B0`、`3502=5`）。生成器和完成脚本必须按 Key 哈希定位，禁止按 SQLite 行号、角色序号或存档 unit 顺序映射。

完成规则只允许把 319 条 `FATE_*` 对应的 `3502` 精确设为 `30`，并让 `2560` 中 56 个有效任务位置的 `2561` 至少为 `1`；5 条 `REMI_*`、496 条空占位、44 个空任务位置、已有更高任务计数与主线字段都必须保留。`5801/5815` 没有完整数据库索引映射，不得从单次实机探针推导全量值。

### 2.0 武器超限（162 条数据库规格）

从 `weapon`、`weapon_status_rebuild` 和 `weapon_skill_level_rebuild` 提取规格。入选条件是 `WeaponStatusRebuildId` 具有完整连续 Level 1–7，并且 `WeaponSkillLevelRebuildId1..5` 五条曲线都能在 `weapon_skill_level_rebuild.Unk13` 中解析。

完整数据库得到 162 行：160 条当前规格和 `WEP_PL2800_A0`、`WEP_PL2900_A0` 两条备用运行时规格。每个 `2818` 槽按自己的曲线选择 `Unk12`：对应 `WeaponSkillId` 存在时选哈希匹配行；不存在时按数据库 `rowid` 稳定顺序选第一/唯一行。不得合成 `_10` 技能 ID，也不得使用跨武器统一的最终技能。

正常保存的当前实机存档包含 171 个实例、159 种当前规格（70 个旧觉醒、101 个普通武器），只缺少 `WEP_PL0000_01`。其中 74 个已满实例的 370 个技能槽全部与上述数据库曲线结果相同。

## 更新方法

1. 使用合法取得的游戏文件和 GBFRDataTools 重新提取表。
2. 将数据库放在仓库外部。
3. 运行 `scripts/generate_catalogs.py` 并明确传入两个数据库路径。
4. 运行 `scripts/generate_fate_episode_catalog.py --database ... --source-table ...`，传入完整 `fate_episode` SQLite 表和对应原始 `.tbl`，确认 SHA-256 与行数。
5. 运行 `scripts/generate_weapon_rebuild_catalog.py --database ...`，传入同时含 `weapon`、status 与 skill rebuild 表的数据库。
6. 运行 `scripts/generate_latest_sigil_preset.py --database ... --characters catalogs/characters.json --output presets/sigils/latest-endgame-gold-2.0.2.json`。
7. 运行 `scripts/generate_stackable_catalog.py --database ... --output catalogs/stackable-items-2.0.2.json --editor-root ...`。
8. 阅读 JSON diff，确认 319/5 命运记录、56 个任务、162 条武器曲线规格、29×12 因子、29×3 武器祝福、4 个召唤被动与 329 条普通堆叠物变化符合预期。
9. 运行 `scripts/validate_repository.py`。
