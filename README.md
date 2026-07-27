# Relink Save Forge

《碧蓝幻想 Relink》2.0.2 离线存档修改、可审计预设和 Windows 一键工具。默认发行版提供完整目录备份、离线候选、双遍幂等验证和原子部署；仓库同时保留材料、命运篇章、因子、武器与 2.0 超限的研究/重建脚本。

本仓库不包含游戏数据库、游戏二进制、用户存档、SteamID 或任何本机绝对路径。`catalogs/` 是由本地提取的 SQLite 表生成的最小运行时清单；`presets/` 只描述允许执行的配置。Windows 包不会再分发无许可证的第三方编辑器源码，而是在首次运行时下载并固定到已验证提交。

## 仓库内容

- `catalogs/characters.json`：数据库识别出的 29 名满级上限可玩角色。
- `catalogs/weapons.json`：按数据库真实基础武器行解析的 174 条武器 ID/哈希清单（每名角色 6 把）。
- `catalogs/fate-episodes-2.0.json`：Relink 2.0 完整命运篇章清单，含 319 条 `FATE_*`、5 条必须保留的 `REMI_*` 与 56 个唯一战斗任务 ID。
- `catalogs/weapon-rebuild-2.0.json`：162 条完整数据库曲线规格（160 条当前规格、2 条 `_A0` 备用运行时规格）及每把武器独立的五槽技能向量。
- `catalogs/weapon-runtime-aliases.json`：162 条数据库运行时武器哈希到正式基础武器的映射。
- `catalogs/sigils-2.0.json`：2.0 新角色专属因子与 `GEEN_320`–`GEEN_327` 新通用/天星因子数据库行。
- `catalogs/stackable-items-2.0.2.json`：329 种可安全设置为 900 的普通可叠加物品，含 8 种解锁卷。
- `catalogs/sigil-legal-pairs-2.0.2.json` 与 `catalogs/skill-level-caps-2.0.2.json`：标准版 Lv15 因子的数据库合法组合与逐技能总等级上限。
- `catalogs/weapon-runtime-identities-2.0.2.json` 与 `catalogs/weapon-instance-template-2.0.2.json`：174 把正式武器的基础/运行时身份和规范空实例模板。
- `catalogs/top-summons-2.0.2.json`：四颗终盘召唤石的实机验证创建元组，包含合法 `15/9` lane 与游戏保存后规范化的 `1460=6`。
- `presets/sigils/latest-endgame-gold-2.0.2.json`：29 名角色各 12 枚、24 个唯一 99 级词条的数据库脱离版终盘老金预设。
- `presets/sigils/standard-endgame-*-2.0.2.json`：只使用数据库真实因子行、所有非空内部词条均为合法 Lv15 的输出与生存/QoL 预设。
- `presets/weapons/endgame-qol-blessing-2.0.2.json`：29 把当前装备武器统一使用技能冷却、怒涛、霸体三条 99 级祝福。
- `presets/weapons/*-standard-2.0.2.json`：正常版输出与生存/QoL 的合法 Lv15 武器祝福。
- `presets/summons/endgame-qol-passives-2.0.2.json`：现有四颗终盘召唤石的合法满级被动，保留外壳、第二加成槽和未知状态字段。
- `presets/packs/`：12 个可独立选择的现有实例、自动补齐、资源、命运篇章、角色、武器和召唤石预设包。
- `app/` 与 `RelinkSaveForge.cmd`：自动定位、备份、双遍验证、原子部署和恢复入口。
- `scripts/generate_catalogs.py`：从本地数据库重新生成三份清单。
- `scripts/generate_fate_episode_catalog.py`：从 2.0 `fate_episode` SQLite 表与原始 `fate_episode.tbl` 生成可复核命运篇章清单。
- `scripts/generate_weapon_rebuild_catalog.py`：从 2.0 武器/status 表重建觉醒与运行时哈希清单。
- `scripts/gbfr_hash.py`：GBFR 自定义 XXHash32 ID 哈希实现。
- `scripts/build_materials_complete.py`：补齐安全的 2.0 可见材料/普通物品，不写 `210x` 实例或主线字段。
- `scripts/complete_all_fate_episodes.py`：只完成数据库确认的 319 条命运篇章及其 56 个有效任务位置，保留 `REMI_*`、空占位、主线与统计字段。
- `scripts/build_all_sigils_strict.py`：为 29 名角色建立 12 槽、内外词条一致的 Lv15 因子装备链。
- `scripts/equip_latest_endgame_gold_sigils.py`：应用 α/β/γ、摇曳步、天星、守护、金刚和逐角色觉醒/战气的 99/99 终盘因子预设。
- `scripts/equip_verified_weapon_blessings.py`：按 `1402 → 2802` 定位 29 把当前装备武器，同步写 `2816` 与 87 个 `130m` 运行时词条槽。
- `scripts/equip_verified_summon_traits.py`：只重配四颗已装备终盘召唤石的被动槽，完整保留其加成槽、链接和 `1460`。
- `scripts/unlock_all_characters.py`：只对 29 个预分配角色行的 `1305` 执行自然激活 mask 的按位 OR，不覆盖已有 bit 或进度。
- `scripts/ensure_sigil_loadouts.py`：只用已有非零因子实例为 29 名角色建立各 12 个唯一、可解析的 `1403 ↔ 2702/2706` 链接，不创建或改写因子内容，并保留非目标配装、尾槽引用与未选中的 owner。
- `scripts/ensure_all_weapons.py`：只在规范空槽中创建缺少的正式武器，保留未知/模组实例，拒绝重复实例 ID 或重复正式武器。
- `scripts/ensure_top_summons.py`：创建、解锁并装备缺少的四颗终盘召唤石，使用实机保存确认的完整实例元组。
- `scripts/set_stackable_quantity.py`：只把 329 种普通堆叠物设为 900，排除主线、钥匙、Fate 特殊物和 `210x` 实例。
- `scripts/build_all_weapons_verified.py`：基于真实打造探针与数据库清单建立 174 个完整武器实例。
- `scripts/complete_all_weapon_awakenings.py`：按当前武器对应的五条数据库技能曲线完成全部普通/旧觉醒武器的 2.0 超限 Lv7。
- `scripts/run_full_rebuild.py`：串联材料、命运篇章、因子、武器、超限、武器祝福、召唤被动和最终验证的一键离线流水线。
- `scripts/verify_full_rebuild.py`：统一审计角色、因子、武器、物品、主线保护、活动哈希和 `210x` 基线。
- `scripts/backup_save.py`：复制存档并生成 SHA-256 清单。
- `scripts/resign_steamid.py`：带强制旧 ID 校验、自动备份、原子写入和显式 `--apply` 的 SteamID64 头部迁移工具。
- `scripts/validate_repository.py`：验证计数、哈希向量、预设结构及敏感数据禁入规则。

## Windows 一键包

从 [Releases](https://github.com/X-Zero-L/relink-save-forge/releases/latest) 下载 `RelinkSaveForge-win-x64-v*.zip`，解压后双击 `RelinkSaveForge.cmd`。包内含便携式 CPython，不要求系统安装 Python；首次运行会联网下载固定提交的 `GBFR-Save-Editor` 存档核心并记录来源。

v1.1.0 菜单提供 12 个经过仓库验证的预设。配装预设明确分为两条边界：

- **Existing Inventory / 仅配装现有实例**：`standard-endgame-output`、`standard-endgame-qol`、`latest-endgame-gold` 和 `mainline-safe-endgame`。它们不会修复缺失的因子链接，也不创建缺失的武器或召唤石；召唤重配保留现有实例的未知 `1460` 字段。
- **Complete / Auto-Fill / 自动补齐后配装**：`standard-complete-output`、`standard-complete-qol` 和 `gold-complete`。它们依次启用角色、完成命运篇章、用已有非零因子实例为 29 人建立各 12 个可解析链接、补齐并强化正式武器、创建并装备四颗终盘召唤石，再应用所选配装。

另外五个包可单独运行：`unlock-all-characters`、`complete-armory`、`create-top-four-summons`、`resources-900` 和 `fate-episodes-all`。因此用户可以只解锁角色、只补武器、只补召唤石，也可以选择正常 Lv15 或显式老金 Lv99 的完整组合。

正常版有两种取向：`Output` 使用合法 Lv15 的输出因子与 Quick Cooldown/Cascade/Stout Heart 武器祝福；`Survival/QoL` 使用合法 Lv15 的生存便利因子与 Cascade/Nimble Onslaught/Greater Aegis 武器祝福。两者所有因子外壳和内部 lane 都来自 2.0.2 数据库真实组合，并检查单项总等级不超过对应曲线上限。只有显示名明确标记 `Gold` 和 `Lv99` 的包才会写入 99 级因子或武器祝福。

一键入口会先确认游戏未运行，备份整个 `SaveGames`，在 `%LOCALAPPDATA%` 下的独立运行目录生成候选和 JSON 审计，再完整执行第二遍并要求 SHA-256/字节完全一致。只有用户确认部署时才会原子替换 `SaveData1.dat`；活动档在处理期间发生变化会立即中止。恢复功能只接受来源目录、Steam 封装和清单哈希匹配的备份，不会自动覆盖未知的新存档。

所有包都保持 Steam 封装、payload 大小、记录数以及主线字段 `2510/2511/2520/2522` 不变。Complete 包的因子链接阶段不生成实例：它只选择已有非零因子，保留实例 ID、外壳、等级、flags 和内部词条，仅规范化前 12 个 `1403` 槽与所选实例的 `2706` owner；非目标 `1403`、目标角色第 13 个及后续尾槽、未选中的 owner 都原样保留并从补位池排除，可安全使用的实例不足 348 个时停止。自动补齐武器只使用 256 个预分配物理槽中的规范空槽：目标为 174 把正式武器，其中 160 把具有完整 2.0 终盘运行时规格，14 把保持基础规格；未知或模组武器原样保留，重复实例 ID、重复正式武器或半空壳会在写盘前拒绝。角色解锁只 OR `1305` 的自然 mask。创建召唤石时，四个目标的 `1460` 使用实机保存后的规范值 `6`；该字段语义仍未知，现有实例重配不会改写它。完整说明见 [docs/ONE_CLICK_WINDOWS.md](docs/ONE_CLICK_WINDOWS.md)。

## 快速验证

```powershell
python scripts/generate_catalogs.py `
  --game-db ../gbfr-live.db `
  --items-db ../gbfr-live-items.db `
  --output catalogs

python scripts/generate_weapon_rebuild_catalog.py `
  --database ../weapon-rebuild-2.0.sqlite

python scripts/generate_fate_episode_catalog.py `
  --database ../fate.sqlite `
  --source-table ../fate_episode.tbl

python scripts/validate_repository.py
```

生成器只读取数据库。请勿把 `.db`、`.dat`、审计中的私人路径或真实 SteamID 提交到仓库。

## 离线重建工具

这些脚本依赖外部 `GBFR-Save-Editor` Python API，但仓库不捆绑该项目。把其 checkout 放在本仓库旁边，或为每条命令传入 `--editor-root`，也可设置环境变量：

```powershell
$env:GBFR_SAVE_EDITOR_ROOT = "path/to/GBFR-Save-Editor"
```

数据库和存档都应放在 gitignored 的 `local/` 中。`run_full_rebuild.py` 拒绝输出覆盖输入、拒绝覆盖已有流水线产物，并拒绝把工作目录、候选存档、审计或报告写入 `%LOCALAPPDATA%\GBFR\Saved\SaveGames`。

### 一键完整重建

以下命令不会改写 `local/input.dat`，并把中间副本、审计报告和最终候选写入独立的 `local/rebuild-001/`。`weapon-probe-before.dat` 与 `weapon-probe-after.dat` 是在游戏内只打造并装备一把武器前后的已知正常保存点，用于证明当前存档版本的武器实例分配规则；它们不随仓库发布。

```powershell
python scripts/run_full_rebuild.py `
  --input local/input.dat `
  --items-db local/gbfr-live-items.db `
  --game-db local/gbfr-live.db `
  --weapon-original local/weapon-probe-before.dat `
  --weapon-probe local/weapon-probe-after.dat `
  --baseline local/input.dat `
  --editor-root ../GBFR-Save-Editor `
  --work-dir local/rebuild-001
```

需要先检查命令计划时追加 `--dry-run`。需要绑定账号头部断言时追加 `--expected-steam-id $steamId`；不要把真实 SteamID 写进仓库。工作目录中的默认最终候选为 `07-endgame.dat`，最终综合报告为 `pipeline-report.json`，完整验证报告为 `08-verification.json`。

### 分步运行

需要定位单个阶段时，可按同一顺序分步运行：

```powershell
python scripts/build_materials_complete.py `
  local/input.dat local/01-materials.dat `
  --database local/gbfr-live-items.db `
  --quantity 900 `
  --audit local/01-materials-audit.json

python scripts/complete_all_fate_episodes.py `
  local/01-materials.dat local/02-fates.dat `
  --catalog catalogs/fate-episodes-2.0.json `
  --audit local/02-fates-audit.json

python scripts/equip_latest_endgame_gold_sigils.py `
  local/02-fates.dat local/03-sigils.dat `
  --characters catalogs/characters.json `
  --preset presets/sigils/latest-endgame-gold-2.0.2.json `
  --audit local/03-sigils-audit.json

python scripts/build_all_weapons_verified.py `
  --input local/03-sigils.dat `
  --original local/weapon-probe-before.dat `
  --probe local/weapon-probe-after.dat `
  --database local/gbfr-live.db `
  --output local/04-weapons.dat `
  --audit local/04-weapons-audit.json

python scripts/complete_all_weapon_awakenings.py `
  local/04-weapons.dat local/05-transcendence.dat `
  --audit local/05-transcendence-audit.json `
  --expect-instances 171 `
  --expect-types 159

python scripts/equip_verified_weapon_blessings.py `
  local/05-transcendence.dat local/06-blessings.dat `
  --characters catalogs/characters.json `
  --preset presets/weapons/endgame-qol-blessing-2.0.2.json `
  --audit local/06-blessings-audit.json

python scripts/equip_verified_summon_traits.py `
  local/06-blessings.dat local/07-endgame.dat `
  --preset presets/summons/endgame-qol-passives-2.0.2.json `
  --audit local/07-summons-audit.json

python scripts/verify_full_rebuild.py `
  local/07-endgame.dat `
  --baseline local/input.dat `
  --items-db local/gbfr-live-items.db `
  --sigil-preset presets/sigils/latest-endgame-gold-2.0.2.json `
  --stack-quantity 900 `
  --report local/final-verification.json
```

工具输出通过离线验证后，仍应保留原存档和 SHA-256 备份，再由用户自行替换活动文件并进游戏检查。流水线不会自动覆盖活动存档。

## 命运篇章 2.0 字段结论

- `3501/3502` 各有 820 条配对记录：319 条真实 `FATE_*` 必须按 Key 哈希定位并把 `3502` 精确写为 `30`；5 条 `REMI_*` 与 496 条空占位必须保持原样；
- 空占位固定为 `3501=887AE0B0`、`3502=5`，不能当作可分配槽；
- 数据库有 58 条非零 `MissionQuestId` 引用，但只有 56 个唯一任务 ID。`2560/2561` 向量长度为 100，其中 56 个有效位置只保证 `2561 >= 1` 并保留更高计数，44 个空位置必须继续为 `0`；
- `3501`、`2560` 和主线字段 `2510/2511/2520/2522` 必须逐项不变；
- `5801/5815` 是尚无完整索引映射的统计/通知类字段，不是命运篇章、槽位或 StatusUP 的独立开关。完成流程必须完整保留，禁止全量设值、复制一次探针索引或猜测最大计数。

全量完成 319 条真实命运篇章会覆盖每名角色最后两个因子槽的数据库条件，不需要伪造额外角色槽位标志。完整契约和实机差分见 [docs/FATE_EPISODES_2_0.md](docs/FATE_EPISODES_2_0.md)。

## 武器 2.0 字段结论

- `2807` 是旧觉醒等级：旧觉醒武器保持 `10`，普通武器保持 `0`；
- `2817` 是 2.0 超限等级，满级 `7`，不是完成布尔值；
- `2815` 是 flags，必须保留已有 bit；
- `2818` 是五项技能哈希向量，每一槽都必须由该武器的 `WeaponSkillLevelRebuildId1..5` 关联到 `weapon_skill_level_rebuild.Unk13`，再写入所选行的 `Unk12` 哈希；
- 有对应 `WeaponSkillId` 时必须选同技能行；没有时使用数据库稳定顺序中的第一/唯一行；
- 禁止通过 `_10` 后缀合成 `max_skill_id`，也禁止给所有武器写统一第五技能；
- 完整数据库共有 162 条规格，其中 160 条为当前规格，另有 2 条 `_A0` 备用运行时规格。

当前正常保存的实机样本包含 171 个可处理实例、159 种当前规格，其中 70 个旧觉醒实例、101 个普通武器实例；唯一缺少的当前规格是 `WEP_PL0000_01`。已有 74 个游戏内满级实例的 370 个技能槽与数据库曲线逐槽完全一致，这也是生成器、完成脚本和验证器采用的硬性证明。详见 [docs/WEAPON_REBUILD_2_0.md](docs/WEAPON_REBUILD_2_0.md)。

## 2.0.2 标准 Lv15 与显式老金 Lv99

标准预设只发布数据库真实 `gem` 行允许的外壳/主词条/副词条组合。每个非空内部 lane 固定为 Lv15，同时按角色聚合相同技能的总等级并检查不超过该技能的 2.0.2 曲线上限；单词条因子的空副 lane 明确写回空哈希与等级 0。`standard-endgame-output` 偏向输出，`standard-endgame-qol` 偏向生存和操作便利；对应 `standard-complete-*` 会先自动补齐角色状态、命运篇章、因子装备链接、武器与四颗召唤石。标准包不会写任何 99 级 trait。

老金预设的 ID 明确带 `gold`，显示名和说明明确标记 `Gold` 与 `Lv99`，不会伪装成正常配装。

默认完整配装覆盖全部 29 名角色。每人 12 枚因子、24 个不重复的 99 级内部词条，包含三组天星、属性克制转换、α、β、γ、伤害上限、追击、守护、金刚、躲避性能、摇曳步、豪胆、自动复活，以及逐角色觉醒和战气。每把当前装备武器另外获得技能冷却、怒涛和霸体三条 99 级祝福；四颗全局召唤石分别提供激昂、药水携带、斯巴达 Echo 和狂战士 Echo 的合法 15 级满级效果。每个角色最终覆盖 31 种不重复效果。

这次实机样本纠正了一个容易混淆的英文映射：中文“摇曳步”是 `SKILL_159_00`（Flight over Fight，哈希 `EC1C6779`）；`SKILL_150_00` 是中文“躲避距离”（英文 Untouchable），不是摇曳步。α、β、γ分别使用 `SKILL_160_00`、`SKILL_161_00` 和 `SKILL_162_00`，三者是独立终盘效果，不能因为内部等级为 99 就互相替代。

角色因子部分的规范化构建摘要固定为 `CD07EA5ECC868137F1B97B2FF95A74974CFC861D769569FEE122BE45CBF866BB`。生成器会从 2.0.2 `chara/gem` 表重新解析 29 名角色的觉醒与战气；仓库验证器进一步检查 696 个角色因子内部槽、87 个武器祝福槽、四个召唤被动、唯一外壳约束、每人恰好一个摇曳步、昏厥、Linked Together 和 Lv99 激昂，以及最终 31 效果无重复。老金召唤预设把原 Lv15 激昂换为 Lv15 闪避性能，避免效果重复。

## 存档安全

1. 完全退出游戏并暂停 Steam 云同步冲突处理。
2. 先运行 `backup_save.py`，保存备份目录与 `manifest-sha256.json`。
3. SteamID 迁移必须同时提供预期旧 ID 和新 ID；默认仅预览，只有 `--apply` 才写入。
4. SteamID 位于 PC 存档头部偏移 `0x04` 的 8 字节小端值。修改头部不等于修复内部 payload 哈希。
5. 修改 payload 时，只更新由 `SAVEDATA_HASHSEED` 选中的活动 XXHash64 段；不要把十个哈希槽全部重写成“有效”。

详细说明见 [docs/SAVE_SAFETY.md](docs/SAVE_SAFETY.md) 和 [docs/SAVE_STRUCTURE.md](docs/SAVE_STRUCTURE.md)。

## 数据来源与边界

数据库表提取依赖上游 [Nenkai/GBFRDataTools](https://github.com/Nenkai/GBFRDataTools) 提交 `571a1d1ce71c17601684894dad186269c0fed1dc`；补充因子/词条研究参考 [alexfrljuckic/GBFRelinkMod](https://github.com/alexfrljuckic/GBFRelinkMod) 提交 `c9bd8350e6deb3a3034194fe6fbf62cd453989e9`；离线存档读写依赖 [xcier/GBFR-Save-Editor](https://github.com/xcier/GBFR-Save-Editor) 固定提交 `8fdb4497fcf0cf67a4b122062a00f8ff07cc3942`。后两份审计 checkout 当前未提供可用于再分发的明确许可证文件，因此源码仓库和发布包都不分发或重许可其代码；一键入口仅在用户机器上按固定 URL 下载存档核心。数据生成基准、筛选规则、输入 SHA-256 与复现方法见 [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)。

游戏名称、商标、游戏数据、上游代码和第三方工具归各自权利人及其许可证约束。本仓库的 MIT 许可证仅覆盖本仓库原创的脚本和代码，不覆盖提取出的游戏数据库内容、用户存档、GBFRDataTools 或 GBFR-Save-Editor。

## English summary

Relink Save Forge v1.1.0 provides 12 auditable one-click packs for Relink 2.0.2. Users can choose legal level-15 output or survival/QoL builds, explicitly labeled level-99 modifier builds, existing-inventory-only transforms, or auto-fill bundles that enable all characters, rebuild 29×12 resolvable links from existing nonzero sigil instances, create missing official weapons, and create/equip the verified top-four summons. Existing-only packs do not repair missing sigil links. Standalone character, armory, summon, resources-x900, and Fate Episode packs are also included. Every pack preserves main-story fields 2510/2511/2520/2522; no saves, personal Steam IDs, game databases, or unlicensed third-party editor code are distributed.
