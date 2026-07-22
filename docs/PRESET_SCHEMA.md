# 预设结构

Relink Save Forge 使用两层 JSON 预设：配装数据和可执行预设包。二者都只描述允许修改的内容，不包含用户存档、SteamID 或本机路径。

## 全角色因子预设

`presets/sigil-preset-schema.json` 描述一次性覆盖 29 名角色的精确配装。当前默认文件是 `presets/sigils/latest-endgame-gold-2.0.2.json`。

- `outer_level=15`：游戏界面显示的因子外壳等级。
- `trait_level=99`：老金内部两条词条的等级。
- `flags=3`：已验证的已强化/可用标志。
- `character_order`：2.0.2 数据库中的 29 名可玩角色顺序。
- `characters[].sigils`：每名角色恰好 12 枚因子。
- `outer_id/primary_id/secondary_id`：外壳、第一词条和第二词条的真实 GBID。
- `*_hash`：由 ID 重新计算的 32 位哈希；验证器禁止只改外壳不改内部词条。
- `build_sha256`：对 29×12 套完整配装的规范化摘要。

默认终盘老金要求每名角色拥有 24 个不重复的 99 级因子词条，并同时包含 α、β、γ、守护、金刚、躲避性能和真正的摇曳步。中文“摇曳步”对应 `SKILL_159_00`（英文 Flight over Fight）；`SKILL_150_00` 是另一条“躲避距离”（英文 Untouchable），不能混用。

综合终盘包还引用两份独立预设：

- `presets/weapons/endgame-qol-blessing-2.0.2.json`：固定三词条外壳 `ITEM_26_0131`，并把技能冷却、怒涛、霸体写入每把当前装备武器自己的三个 `130m` lane，等级为 99；
- `presets/summons/endgame-qol-passives-2.0.2.json`：按召唤外壳、`skill_lot` 和 `summon_curve` 固定四个合法满级被动，只修改 `1458/1459` 的第一项并保留第二项。

验证器要求角色 24 项、武器 3 项和召唤 4 项合计 31 种效果完全不重复。

## 一键预设包

`presets/pack-schema.json` 描述一组离线修改步骤。`presets/packs/*.json` 会显示在 Windows 一键菜单中。

- `id/name/description`：稳定标识、显示名和范围说明。
- `steps[].kind`：`transform` 生成下一份候选存档，`verify` 只做检查。
- `steps[].command`：不经过 shell 的参数数组。
- `timeout_seconds`：单阶段超时。
- `audit_required`：阶段是否必须生成 JSON 审计。

命令可使用：`{python}`、`{input}`、`{output}`、`{audit}`、`{root}`、`{editor_root}`、`{run_dir}` 和 `{save_dir}`。一键入口会在独立运行目录展开这些占位符，完整预设执行两遍并要求最终文件字节级一致，然后才允许原子部署。

当前发行预设：

- `latest-endgame-gold`：29 人完整 31 效果终盘配装（因子 + 当前装备武器祝福 + 现有四颗终盘召唤石）。
- `resources-900`：329 种普通可叠加物品设为 900。
- `fate-episodes-all`：完成 319 条命运篇章。
- `mainline-safe-endgame`（显示名 `Mainline-Safe Essentials`）：按顺序执行资源、命运篇章和完整终盘配装；只重配现有装备，不创建缺失的武器、召唤或物品实例，也不修改专精。
