# 流放之路2资料问答（AstrBot 插件）

在 AstrBot 中用 `/poe2 问题` 查询《流放之路2》资料；匹配到带有 Poe2DB 图片的词条时，答案后会附上图片，没有可用图片则照常回复文字。用 `/poe2search 词条名` 直接搜索技能、装备、怪物等词条并返回摘要和链接，有 Poe2DB 图片时也会附图；用 `/poe2bd 游戏角色名` 打开 poe.ninja 当前赛季的角色筛选结果，或用 `/poe2 国际服物价`、`/poe2 国服物价` 获取对应服务器的通货价格图片。资料搜索优先读取 [Poe2DB 简体中文站](https://poe2db.tw/cn/)，需要时回退到[繁体中文站](https://poe2db.tw/tw/)；同时检索 [PoE2Wiki](https://www.poe2wiki.net/wiki/Path_of_Exile_2_Wiki)。聊天模型只根据实际取得的资料生成简体中文回答，回答末尾附上来源链接。

## 安装

在 AstrBot 的 `data/plugins/` 目录克隆仓库，然后重启或在插件管理页加载：

```bash
cd AstrBot/data/plugins
git clone https://github.com/xiaoliz1/astrbot_plugin_poe2db.git
```

插件代码需要 Python 3.10 或更新版本。AstrBot 会按 `requirements.txt` 安装 `beautifulsoup4`；当前会话还需要配置可用的聊天模型。

已安装旧版时，在 AstrBot 插件管理页更新并重载插件；若按上述命令克隆安装，也可以在插件目录运行 `git pull --ff-only` 后重载。更新后插件信息页应显示 `v1.8.0`。

## 使用

### 联网搜索

```text
/poe2 碎骨有什么效果？
/poe2 Mageblood 的词缀是什么？
/poe2 元素专注辅助宝石有什么作用？
```

同一主题命中多个相近词条时，会先让提问者选择。示例如下（候选项和回答以实际检索结果为准）：

```text
你：/poe2 火球有什么效果？

机器人：“火球”可能对应多个词条，请选择：
1. 火球
2. 火球辅助宝石
请在 60 秒内回复编号；回复“取消”可结束选择。

你：1

机器人：根据你选择的“火球”词条回答……
资料来源：Poe2DB、PoE2Wiki
```

### 物价查询

```text
/poe2 国际服物价
/poe2 国服物价
```

### 角色查询

```text
/poe2bd 角色名
```

插件让当前模型提取最多两个主题的简体、繁体和英文名称。若只提取到中文，但 Poe2DB 命中的词条地址含英文名称，插件会用该名称补查 Wiki。同一主题命中多个相近词条时，会先列出候选项，请发起提问的人在 60 秒内回复编号；多个主题会分别判断，不会把不同主题误当作歧义。仅对实际匹配到的词条提供答案，不会把搜索结果中只提到关键词的无关页面当作证据。若页面没有说明掉落方式等细节，回答会明确说明无法确认。

`/poe2search 词条名` 不调用聊天模型，直接搜索 Poe2DB 和 PoE2Wiki，按词条标题与搜索词的相似度排列结果，并显示简短摘要和原始页面链接；中文搜索命中 Poe2DB 后，会尝试用页面中的英文名称补查 Wiki。有 Poe2DB 图片时会额外发送图片；没有图片时仍以文字和链接回复。图片只接受 Poe2DB 图片 CDN 的 HTTPS 图片地址。没有直接命中时会返回两站的搜索页链接。技能、辅助宝石、装备、词缀、职业、怪物和地图等类别取决于资料站实际收录内容。

`/poe2bd 游戏角色名` 会生成 poe.ninja 当前赛季的公开 Builds 筛选链接，筛选链接同时包含页面搜索状态和角色名条件。赛季名称从 poe.ninja 公开的赛季列表读取。详情页路径还需要 poe.ninja 分配的账号标识；命令无法从角色名可靠推导该标识时，会返回筛选页链接供你点击对应角色。

| 来源 | 可用内容 | 说明 |
| --- | --- | --- |
| [Poe2DB 简体](https://poe2db.tw/cn/)／[繁体](https://poe2db.tw/tw/) | 中文词条、游戏数值和说明 | 简体优先；匹配不到或暂时无法读取时尝试繁体。 |
| [PoE2Wiki](https://www.poe2wiki.net/wiki/Path_of_Exile_2_Wiki) | 英文词条 | 通过公开 MediaWiki 搜索接口找词条，再读取词条正文；站点要求浏览器验证时跳过。 |
| [poe.ninja PoE2 Builds](https://poe.ninja/poe2/builds) | 角色搜索链接 | 按输入的角色名生成当前赛季筛选页面；不调用 Builds/角色内部 API。 |

普通问答默认最多引用 5 条资料，分别来自 Poe2DB 和 PoE2Wiki 实际命中的页面；`/poe2search` 也最多显示同样数量的词条。可在 AstrBot 插件配置调整资料条数（1–8）、单次站点请求超时（5–30 秒）和内存缓存时间（0–60 分钟）；若设为 1，每次只显示一条来源。某个来源失败时，另一个来源仍可提供答案。回答会说明资料不足或来源冲突；游戏数值可能随版本改变，应以附上的页面为准。

## 物价图片

两种物价命令都从 Poe2DB 的 14 个经济分类页采集中文名称与分类，并依据页面提供的英文词条名匹配行情数据，不维护物品名称对照表。由于不同站点的分类并非完全对应，图片类别以 Poe2DB 的物品页面归类为准。国际服价格来自 [poe.ninja 的公开 PoE2 经济接口](https://poe.ninja/docs/api)，国服价格来自 [poecurrency.top 的公开 API](https://poecurrency.top/api.html)。只显示直接以神圣石计价的物品，按类别展示；国际服按神圣石估价从高到低排序，国服沿用国际服对应物品的顺序。每张图片最多显示 50 项。

插件启动时会采集一次；之后按北京时间每天 00:00、06:00、12:00、18:00 更新两张表。更新失败时保留上次成功的图片数据，并在 15 分钟后重试；命令直接返回最近一次采集的表格。图片上会标注表格更新时间。行情源自身可能有缓存：poe.ninja 的数据约按小时更新，poecurrency.top 的汇总接口缓存 1 小时；因此定时任务按时重新采集，源站数据仍以其实际更新时间为准。物价命令不调用聊天模型，但需要 AstrBot 的 HTML 图片渲染服务。价格仅供参考；poecurrency.top 说明其服务仅供个人非商业使用。

普通问答只按需读取 Poe2DB 与 PoE2Wiki；两种物价图片都从 Poe2DB 读取中文名称和分类，再分别从 poe.ninja、poecurrency.top 读取价格。插件不随仓库打包站点数据库或物品名称对照表。

## 开发与资料依据

- [AstrBot 插件开发文档](https://github.com/AstrBotDevs/AstrBot/blob/master/docs/zh/dev/star/plugin.md)：插件入口、命令和配置。
- [Poe2DB](https://poe2db.tw/cn/) 和 [PoE2Wiki](https://www.poe2wiki.net/wiki/Path_of_Exile_2_Wiki)：运行时资料来源。各站点内容的权利归各自权利人所有。
- [poe.ninja API 文档](https://poe.ninja/docs/api)：当前赛季与 PoE2 通货价格的数据接口。
- [poecurrency.top API 文档](https://poecurrency.top/api.html)：国服当前赛季及通货均价接口。
