# 流放之路2资料问答（AstrBot 插件）

在 AstrBot 中用 `/poe2 问题` 查询《流放之路2》资料。插件优先读取 [Poe2DB 简体中文站](https://poe2db.tw/cn/)，需要时回退到[繁体中文站](https://poe2db.tw/tw/)；同时检索 [PoE2Wiki](https://www.poe2wiki.net/wiki/Path_of_Exile_2_Wiki)、[RePoE](https://repoe-fork.github.io/poe2/) 和 [poe-game-data](https://github.com/seominugi/poe-game-data/tree/master/poe2)。当前会话的聊天模型只根据实际取得的资料生成简体中文回答，回答末尾附上来源链接。

## 安装

在 AstrBot 的 `data/plugins/` 目录克隆仓库，然后重启或在插件管理页加载：

```bash
cd AstrBot/data/plugins
git clone https://github.com/xiaoliz1/astrbot_plugin_poe2db.git
```

插件代码需要 Python 3.10 或更新版本。AstrBot 会按 `requirements.txt` 安装 `beautifulsoup4`；当前会话还需要配置可用的聊天模型。

## 使用

```text
/poe2 碎骨有什么效果？
/poe2 冰霜新星是什么技能？
/poe2 魔血的词缀是什么？
/poe2 Fireball 需要什么条件？
```

插件让当前模型提取最多两个主题的简体、繁体和英文名称。仅对实际匹配到的词条或记录提供答案，不会把搜索结果中只提到关键词的无关页面当作证据。英文译名不确定时，英文来源可能查不到对应条目。

| 来源 | 可用内容 | 说明 |
| --- | --- | --- |
| [Poe2DB 简体](https://poe2db.tw/cn/)／[繁体](https://poe2db.tw/tw/) | 中文词条、游戏数值和说明 | 简体优先；匹配不到或暂时无法读取时尝试繁体。 |
| [PoE2Wiki](https://www.poe2wiki.net/wiki/Path_of_Exile_2_Wiki) | 英文词条 | 通过公开 MediaWiki 搜索接口找词条，再读取词条正文；站点要求浏览器验证时跳过。 |
| [RePoE](https://repoe-fork.github.io/poe2/) | 宝石元数据、机制词条、暗金物品索引 | 读取按需缓存的静态 JSON。暗金索引没有词缀，不能用它回答具体词缀。 |
| [poe-game-data](https://github.com/seominugi/poe-game-data/tree/master/poe2) | 技能、辅助宝石的繁体／英文名称和说明，以及暗金名称／类别 | 固定读取 `v2026.10.13` 数据快照以保持字段稳定；暗金数据未提供词缀。 |

默认最多引用 5 条资料，使四类来源都有机会出现在同一答案中。可在 AstrBot 插件配置调整资料条数（1–8）、单次站点请求超时（5–30 秒）和内存缓存时间（0–60 分钟）。某个来源失败时，其余来源仍可提供答案。回答会说明资料不足或来源冲突；游戏数值可能随版本改变，应以附上的页面和数据快照为准。

插件只按需读取上述站点和数据文件，不随仓库打包完整站点数据库或名称对照表。

## 开发与资料依据

- [AstrBot 插件开发文档](https://github.com/AstrBotDevs/AstrBot/blob/master/docs/zh/dev/star/plugin.md)：插件入口、命令和配置。
- [Poe2DB](https://poe2db.tw/cn/)、[PoE2Wiki](https://www.poe2wiki.net/wiki/Path_of_Exile_2_Wiki)、[RePoE](https://repoe-fork.github.io/poe2/) 和 [poe-game-data](https://github.com/seominugi/poe-game-data/tree/master/poe2)：运行时资料来源。各站点内容与数据的权利归各自权利人所有。
