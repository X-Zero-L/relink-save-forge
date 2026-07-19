# GBFR Relink 2.0 Save Presets

《碧蓝幻想 Relink》2.0 数据清单、可审计配装预设与存档安全辅助脚本。

本仓库不包含游戏数据库、游戏二进制、用户存档、SteamID 或任何本机绝对路径。`catalogs/` 是由本地提取的 SQLite 表生成的轻量清单；`presets/` 只描述配置，不会自行修改存档。

## 仓库内容

- `catalogs/characters.json`：数据库识别出的 29 名满级上限可玩角色。
- `catalogs/weapons.json`：按数据库真实基础武器行解析的 174 条武器 ID/哈希清单（每名角色 6 把）。
- `catalogs/sigils-2.0.json`：2.0 新角色专属因子与 `GEEN_320`–`GEEN_327` 新通用/天星因子数据库行。
- `presets/characters/fediel-celestial-dual-trait-2.0.json`：暗龙菲迪埃尔的 12 槽天星双词条预设。
- `presets/templates/`：其他角色可复用的 JSON/YAML 配装结构。
- `scripts/generate_catalogs.py`：从本地数据库重新生成三份清单。
- `scripts/gbfr_hash.py`：GBFR 自定义 XXHash32 ID 哈希实现。
- `scripts/backup_save.py`：复制存档并生成 SHA-256 清单。
- `scripts/resign_steamid.py`：带强制旧 ID 校验、自动备份、原子写入和显式 `--apply` 的 SteamID64 头部迁移工具。
- `scripts/validate_repository.py`：验证计数、哈希向量、预设结构及敏感数据禁入规则。

## 快速验证

```powershell
python scripts/generate_catalogs.py `
  --game-db ../gbfr-live.db `
  --items-db ../gbfr-live-items.db `
  --output catalogs

python scripts/validate_repository.py
```

生成器只读取数据库。请勿把 `.db`、`.dat` 或真实 SteamID 提交到仓库。

## 暗龙 2.0 天星双词条预设

预设包含三组天星核心、晕厥/上限与追击、狂战士/斯巴达、属性克制转换、双 Gamma，以及暗龙觉醒和战气。每个槽位同时记录主因子 GBID、第二词条 Skill ID 与等级，便于审计和移植。

这份配置是“因子槽位预设”，不声称替代技能、武器加护或局内操作手法。游戏平衡或 2.0 数据表变化后，应重新生成清单并单独复核配装。

## 存档安全

1. 完全退出游戏并暂停 Steam 云同步冲突处理。
2. 先运行 `backup_save.py`，保存备份目录与 `manifest-sha256.json`。
3. SteamID 迁移必须同时提供预期旧 ID 和新 ID；默认仅预览，只有 `--apply` 才写入。
4. SteamID 位于 PC 存档头部偏移 `0x04` 的 8 字节小端值。修改头部不等于修复内部 payload 哈希。
5. 修改 payload 时，只更新由 `SAVEDATA_HASHSEED` 选中的活动 XXHash64 段；不要把十个哈希槽全部重写成“有效”。

详细说明见 [docs/SAVE_SAFETY.md](docs/SAVE_SAFETY.md)。

## 数据来源与边界

数据生成基准、筛选规则、源码提交和输入数据库摘要见 [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)。游戏名称、商标和原始数据归各自权利人所有；本仓库的 MIT 许可证仅覆盖仓库中的原创脚本、结构和文档。

## English summary

Reproducible GBFR Relink 2.0 ID catalogs, auditable JSON/YAML build presets, and conservative save backup/SteamID-header utilities. No saves, personal Steam IDs, game databases, or binaries are distributed.
