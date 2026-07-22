# Relink Save Forge Windows 一键包

这是《碧蓝幻想 Relink》2.0.2 的离线存档预设工具。发行包只包含一键运行所需的应用、预设、清单、脚本和固定版本的 CPython x64 便携运行时；不包含用户存档、SteamID、游戏文件或第三方编辑器源码。

## 开始使用

1. 完全退出游戏和 Steam 云同步中的活动写入。
2. 保持整个 ZIP 解压后的目录结构，不要只复制启动脚本。
3. 双击 `RelinkSaveForge.cmd`。
4. 选择预设。只有明确输入 `y` 或在命令行使用 `--apply` 时，工具才会部署候选存档。

首次需要解析存档时，工具会联网下载固定提交的 `GBFR-Save-Editor`，并在安装前验证 SHA-256。发行包不会使用系统中的 `py.exe` 或 `python.exe` 作为备用运行时；便携运行时缺失、损坏或引导失败时会直接停止。此时请重新下载发行包并核对 Release 页面提供的 SHA-256。

## 包含的预设

- `latest-endgame-gold`：29 名角色的 24 条 Lv99 因子词条、29 把当前装备武器的三条 Lv99 祝福，以及现有四颗终盘召唤石的合法满级被动。
- `resources-900`：把清单确认的 329 种普通可叠加物品（含 8 种解锁卷）设为 900。
- `fate-episodes-all`：完成 319 条数据库确认的命运篇章及 56 个有效任务计数。
- `mainline-safe-endgame`：依次应用资源、命运篇章和完整终盘配装。

这些预设不创建缺失的武器、召唤石或库存实例，不修改主线和专精。完整终盘配装只重配已有的角色因子槽、当前装备武器祝福和现有四颗终盘召唤石的第一被动槽。

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
RelinkSaveForge.cmd --preset latest-endgame-gold
RelinkSaveForge.cmd --preset mainline-safe-endgame --apply
RelinkSaveForge.cmd --validate-only
RelinkSaveForge.cmd --restore-latest
```

工具仅供离线处理你有权修改的本地存档。第三方来源和固定版本信息见 `THIRD_PARTY_NOTICES.md` 及首次运行生成的 `runtime\runtime-lock.json`。
