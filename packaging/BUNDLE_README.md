# Relink Save Forge Windows 一键包

这是《碧蓝幻想 Relink》2.0.2 的离线存档预设工具。发行包只包含一键运行所需的应用、预设、清单、脚本和固定版本的 CPython x64 便携运行时；不包含用户存档、SteamID、游戏文件或第三方编辑器源码。

## 开始使用

1. 完全退出游戏和 Steam 云同步中的活动写入。
2. 保持整个 ZIP 解压后的目录结构，不要只复制启动脚本。
3. 双击 `RelinkSaveForge.cmd`。
4. 选择预设。只有明确输入 `y` 或在命令行使用 `--apply` 时，工具才会部署候选存档。

首次需要解析存档时，工具会联网下载固定提交的 `GBFR-Save-Editor`，并在安装前验证 SHA-256。发行包不会使用系统中的 `py.exe` 或 `python.exe` 作为备用运行时；便携运行时缺失、损坏或引导失败时会直接停止。此时请重新下载发行包并核对 Release 页面提供的 SHA-256。

## 包含的预设

v1.1.0 共包含 12 个预设，可按需要独立选择。

仅使用现有实例：

- `standard-endgame-output`：数据库真实组合的正常 Lv15 输出因子、Lv15 Quick Cooldown/Cascade/Stout Heart 武器祝福，以及现有四颗终盘召唤石被动。
- `standard-endgame-qol`：数据库真实组合的正常 Lv15 生存/QoL 因子、Lv15 Cascade/Nimble Onslaught/Greater Aegis 武器祝福，以及现有四颗终盘召唤石被动。
- `latest-endgame-gold`：显式老金版；29 名角色各 24 条 Lv99 因子词条、当前装备武器三条 Lv99 祝福，以及现有四颗终盘召唤石的合法满级被动。
- `mainline-safe-endgame`：资源 900 → 全命运篇章 → `latest-endgame-gold`，仍然只重配现有武器和召唤石实例。

这四个 existing-only 包不会自动修复因子装备链接；输入存档必须已经让 29 名角色各有 12 个可解析的因子实例链接。

自动补齐后配装：

- `standard-complete-output`：启用全部角色、完成命运篇章、用已有非零因子实例建立 29×12 个可解析链接、补齐并强化正式武器、创建并装备四颗终盘召唤石，再应用正常 Lv15 输出配装。
- `standard-complete-qol`：同样自动补齐，再应用正常 Lv15 生存/QoL 配装。
- `gold-complete`：同样自动补齐，再应用明确标记的 Lv99 老金因子和武器祝福。

三个 complete 包的 `ensure-sigil-loadouts` 阶段位于 Fate Episodes 之后、武器/召唤创建与最终配装之前。它不创建因子实例，也不改外壳、等级、flags 或内部词条；只规范化角色前 12 个 `1403` 引用和所选实例的 `2706` owner。非目标 `1403`、目标角色尾槽和未选中的 owner 会保留并从补位池排除。若没有 348 个可安全选用的已有非零因子实例，流程会停止而不是伪造实例。

独立操作：

- `unlock-all-characters`：只对 29 个预分配角色行的 `1305` 执行自然激活 mask 的按位 OR；不会覆盖已有 bit、等级、装备或剧情。
- `complete-armory`：只在规范空武器槽中创建缺少的 174 把正式武器，完成 160 把终盘运行时规格，保留 14 把基础规格，并为每名角色装备已验证的最强武器。
- `create-top-four-summons`：创建缺少的 Rolan、Lilith、Beelzebub、Lucilius，解锁目录并装备四颗。
- `resources-900`：把清单确认的 329 种普通可叠加物品（含 8 种解锁卷）设为 900。
- `fate-episodes-all`：完成 319 条数据库确认的命运篇章及 56 个有效任务计数。

现有实例召唤预设完整保留未知字段 `1460`。只有创建召唤石的包会对四颗目标写入实机保存后确认的规范值 `6`；该字段的具体语义仍未解码。武器创建保留所有未知或模组实例，并在重复实例 ID、重复正式武器、非规范半空壳或规范空槽不足时安全停止。全部 12 个预设都保持主线字段 `2510/2511/2520/2522` 不变，也不修改专精。

## 安全行为

- 运行前确认游戏已经关闭。
- 修改前完整备份 `SaveGames` 目录并记录 SHA-256。
- 只在独立运行目录处理副本；候选必须通过双遍、逐字节幂等验证。
- 部署前再次确认活动存档未被其他程序改写。
- 部署或复验失败时，从本次完整备份恢复原存档。

默认备份、候选和日志位于 `%LOCALAPPDATA%\RelinkSaveForge`。进一步说明见 `docs\ONE_CLICK_WINDOWS.md` 和 `docs\SAVE_SAFETY.md`。

## 命令行

```powershell
RelinkSaveForge.cmd --list-presets
RelinkSaveForge.cmd --preset standard-endgame-qol
RelinkSaveForge.cmd --preset standard-complete-output --apply
RelinkSaveForge.cmd --preset gold-complete --apply
RelinkSaveForge.cmd --validate-only
RelinkSaveForge.cmd --restore-latest
```

工具仅供离线处理你有权修改的本地存档。第三方来源和固定版本信息见 `THIRD_PARTY_NOTICES.md` 及首次运行生成的 `runtime\runtime-lock.json`。
