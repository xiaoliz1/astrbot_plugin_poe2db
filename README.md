# 流放之路2资料问答（AstrBot 插件）

插件用于查询《流放之路 2》资料、价格和角色构筑。`/poe2` 联网回答问题并在有匹配图片时附图；`/poe2search` 搜索词条并返回摘要、来源链接和可用图片；`/poe2bd` 查找 poe.ninja 当前赛季角色并返回 Poe2DB 简体中文 Build 链接；`/poe2分析` 支持角色名、poe.ninja 角色或 PoB 链接、WeGame 分享链接，生成中文构筑分析并附传奇物品市集链接。`/poe2 国际服物价` 和 `/poe2 国服物价` 返回神圣石价格图片，支持定时采集、手动采集与群通知。资料优先来自 [Poe2DB 简体中文站](https://poe2db.tw/cn/)，并检索 [Poe2Wiki](https://www.poe2wiki.net/wiki/Path_of_Exile_2_Wiki)。

## 安装

在 AstrBot 的 `data/plugins/` 目录克隆仓库，然后重启或在插件管理页加载：

```bash
cd AstrBot/data/plugins
git clone https://github.com/xiaoliz1/astrbot_plugin_poe2db.git
```

插件代码需要 Python 3.10 或更新版本以及 AstrBot 4.24.2 或更新版本。AstrBot 会按 `requirements.txt` 安装所需 Python 包。为了让插件读取 poe.ninja 公开 Builds 页面，还需在运行 AstrBot 的同一 Python 环境安装 Chromium：

```bash
python -m playwright install chromium
```

Chromium 仅用于按角色名搜索 poe.ninja 当前赛季公开 Builds 页面。poe.ninja PoB 分享从公开导出接口读取 PoB2 码，WeGame 分享通过 Poe2DB 接口解析，两者都不需要 Chromium。若未安装 Chromium、角色名无法唯一匹配或 Builds 页面加载失败，角色名查询会回退到筛选页链接。

已安装旧版时，在 AstrBot 插件管理页更新并重载插件；若按上述命令克隆安装，也可以在插件目录运行 `git pull --ff-only` 后重载。更新后插件信息页应显示 `v1.22.4`。

## 使用

直接输入 `/poe2` 可查看命令速览。

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

在 AstrBot WebUI 打开“插件”中的本插件详情页，再进入“物价采集”页面，点击“立即采集”即可手动更新两服价格并重新生成图片。页面会显示采集状态和两张图片的生成时间。手动采集不会提前触发群通知；通知仍按配置的每日时段发送。

### 角色查询与构筑分析

```text
/poe2bd 角色名
/poe2分析 角色名
/poe2分析 https://poe.ninja/poe2/builds/当前赛季/character/账号标识/角色名
/poe2分析 https://poe.ninja/poe2/pob/分享编号
/poe2分析 https://www.wegame.com.cn/helper/poe2/#/share/分享码
```

`/poe2bd` 会在 poe.ninja 当前赛季查找唯一精确匹配的角色，返回详情链接和 Poe2DB 简体中文 Build 分享页；无需 `poe2-mcp`。无法唯一匹配时会返回筛选页链接，导出或分享失败时会提供手动导入入口。

`/poe2分析 角色名` 会在 poe.ninja 当前赛季搜索角色；也可以直接发送 poe.ninja 的 Builds 角色详情页链接。两种方式都会把公开构筑交给 `poe2-mcp` 分析，并尝试对比天梯构筑。

`/poe2分析 https://poe.ninja/poe2/pob/分享编号` 会读取该公开 PoB 分享页对应的 PoB2 导入码，由插件直接调用 `poe2-mcp` 的 `import_pob` 工具导入，再把解析后的构筑数据交给后续分析，并生成 Poe2DB 简体中文 Build 分享页。该分享链接不包含可确认的 poe.ninja 账号，因此不能声称已经读取了特定角色或其角色级天梯数据；若工具提供了可验证的职业或技能构筑对比，才会报告该比较。`/poe2分析 https://www.wegame.com.cn/helper/poe2/#/share/分享码` 会把分享码提交到 Poe2DB 的公开接口，跟随接口返回的简体中文构筑页并读取 `PoB Code`，再生成 Poe2DB 简体中文 Build 分享页，由插件直接调用 `poe2-mcp` 的 `import_pob` 工具导入并分析。此流程不使用 PoBR，也不需要 Chromium；AstrBot 只需能访问 Poe2DB。分享内容是公开链接，插件不要求 WeGame 账号或密码。WeGame 分享不包含 poe.ninja 角色账号，所以不能比较这个角色本人的天梯记录；只有 MCP 工具返回了可验证的职业或技能构筑对比数据时才会报告相关比较。

分析会提取 PoB 中当前装备组和已镶嵌珠宝，只对明确标为传奇的物品查找市集链接。poe.ninja 角色和 poe.ninja PoB 分享构筑使用 Poe2DB `/us/` 国际服物品页；WeGame 构筑使用 Poe2DB `/cn/` 简体物品页的“市集”链接，保留国服页面提供的赛季路径，不回退到国际服市集。物品中文名取自对应中文条目。若分享页、装备数据或市集链接无法生成，会提供手动导入入口或说明缺失部分，不会猜测链接。需要当前会话配置可用聊天模型，且 AstrBot MCP 管理页中的 `poe2-mcp` 已连接并启用工具。

`/poe2search 词条名` 不调用聊天模型，直接搜索 Poe2DB 和 PoE2Wiki，按词条标题与搜索词的相似度排列结果，并显示简短摘要和原始页面链接；中文搜索命中 Poe2DB 后，会尝试用页面中的英文名称补查 Wiki。有 Poe2DB 图片时会额外发送图片；没有图片时仍以文字和链接回复。图片只接受 Poe2DB 图片 CDN 的 HTTPS 图片地址。没有直接命中时会返回两站的搜索页链接。技能、辅助宝石、装备、词缀、职业、怪物和地图等类别取决于资料站实际收录内容。

`/poe2bd 游戏角色名` 使用 Playwright 打开 poe.ninja 当前赛季的公开 Builds 筛选页；唯一精确匹配时直接返回角色详情链接，无唯一匹配时返回筛选页链接。赛季名称从 poe.ninja 公开的赛季列表读取。插件只读取公开页面渲染的内容，不调用 Builds/Profile 内部接口，也不登录或读取隐藏角色；页面结构变化可能导致搜索页回退。

| 来源 | 可用内容 | 说明 |
| --- | --- | --- |
| [Poe2DB 简体](https://poe2db.tw/cn/)／[繁体](https://poe2db.tw/tw/) | 中文词条、游戏数值和说明 | 简体优先；匹配不到或暂时无法读取时尝试繁体。 |
| [PoE2Wiki](https://www.poe2wiki.net/wiki/Path_of_Exile_2_Wiki) | 英文词条 | 通过公开 MediaWiki 搜索接口找词条，再读取词条正文；站点要求浏览器验证时跳过。 |
| [poe.ninja PoE2 Builds](https://poe.ninja/poe2/builds) | 当前赛季公开角色详情链接、角色 PoB 导入码和 PoB 分享导入码 | 从公开筛选页及角色详情页读取角色数据；PoB 分享从公开分享页读取导入码；不读取角色私有数据。 |
| [Poe2DB 简体中文账号页](https://poe2db.tw/cn/account) | WeGame 分享解析与 PoB2 导入码 | 插件调用 Poe2DB 的分享解析接口，并读取其返回的简体中文构筑页；只处理公开分享链接。 |
| [Poe2DB 中文 PoB](https://poe2db.tw/cn/pob) | 角色专属简体中文 Build 分享页 | 将生成的 PoB2 码提交到 Poe2DB 分享接口；分享失败时提供此页供手动导入。 |
| AstrBot 已连接的 `poe2-mcp` | 公开角色或 PoB2 构筑分析和天梯比较 | `/poe2分析` 只把该 MCP 服务器的工具提供给分析流程；具体结果取决于 MCP 工具实际返回的数据。 |

普通问答默认最多引用 5 条资料，分别来自 Poe2DB 和 PoE2Wiki 实际命中的页面；`/poe2search` 也最多显示同样数量的词条。可在 AstrBot 插件配置调整资料条数（1–8）、单次站点请求超时（5–30 秒）和内存缓存时间（0–60 分钟）；若设为 1，每次只显示一条来源。某个来源失败时，另一个来源仍可提供答案。回答会说明资料不足或来源冲突；游戏数值可能随版本改变，应以附上的页面为准。

## 物价图片

两种物价图片都不再采集 Poe2DB 的经济分类页。国际服价格和物品图标来自 [poe.ninja 的公开 PoE2 经济接口](https://poe.ninja/docs/api)，按照神圣石价格从高到低排序；国服价格、中文名称和物品图标来自 [poecurrency.top 的公开 API](https://poecurrency.top/api.html)，只保留直接以神圣石计价的条目，并按照国际服物品顺序排列。国服接口中的英文名称用于匹配国际服物品并补充中文名称；暂未匹配到中文名称的国际服条目保留接口原名。两张图都会在物品名称左侧显示物品图标，每张最多显示 50 项。

插件启动时会在后台采集一次，之后每小时在配置时区的整点自动采集并预生成国际服、国服图片。物价命令直接发送最近一次已生成的图片，不会触发价格请求或重新渲染；首次采集或图片生成尚未完成时，插件会在数据和图片准备好后自动回发。可在配置中设置时区。采集失败时保留上次成功的数据和图片，并在 15 分钟后重试。生成的图片保存在插件数据目录中，只有对应的新图片成功生成并保存后才会清理旧图；AstrBot 临时目录清理不会提前删除当前物价图。图片上会标注表格更新时间。行情源自身可能有缓存：poe.ninja 的数据约按小时更新，poecurrency.top 的汇总接口缓存 1 小时；源站数据仍以其实际更新时间为准。物价命令不调用聊天模型，但需要 AstrBot 的 HTML 图片渲染服务。价格仅供参考；poecurrency.top 说明其服务仅供个人非商业使用。

### 自动物价通知

在 AstrBot 插件配置中设置：

- **启用物价自动通知**：默认关闭。开启后，在通知时段向匹配的群发送最近一次后台采集生成的国际服和国服图片。启动时首次采集不会主动群发；但有人在图片准备期间查询物价时，插件会在图片就绪后回发给原会话。
- **群通知名单模式**：白名单只向名单内的群发送；黑名单向插件已识别的群发送，并跳过名单内的群。黑名单能识别本次 AstrBot 运行期间使用过 `/poe2`、`/poe2search` 或 `/poe2bd` 的群。
- **群号 / 群会话列表**：每行一个群号或完整 UMO，例如 `123456789` 或 `aiocqhttp:GroupMessage:123456789`。空白名单不会发送。若只填写数字群号，插件会优先匹配已识别的群；尚未识别且当前只有一个支持主动消息的平台时，会按该平台补全。多个平台时请填写完整 UMO，避免把消息发到错误的平台。
- **每日物价通知时段**：默认 `00:00,06:00,12:00,18:00`，可用逗号或换行填写多个 24 小时时刻；`24:00` 等同于 `00:00`。此设置只控制群通知，不影响每小时后台采集。
- **物价采集与通知时区 UTC 偏移小时数**：默认 `8`（中国标准时间），支持半小时等小数偏移；每小时采集在该时区整点执行。

修改通知时段或时区后重载插件。如果后台采集失败，插件会在 15 分钟后重试；通知时段遇到采集失败时，会等到采集和图片生成成功后再发送。主动发送取决于当前平台适配器的能力。

普通问答只按需读取 Poe2DB 与 PoE2Wiki；物价图片由后台每小时分别从 poe.ninja、poecurrency.top 读取数据并预生成，不请求 Poe2DB 经济页面。插件不随仓库打包站点数据库或物品名称对照表。

## 开发与资料依据

- [AstrBot 插件开发文档](https://github.com/AstrBotDevs/AstrBot/blob/master/docs/zh/dev/star/plugin.md)：插件入口、命令和配置。
- [Poe2DB](https://poe2db.tw/cn/) 和 [PoE2Wiki](https://www.poe2wiki.net/wiki/Path_of_Exile_2_Wiki)：运行时资料来源。各站点内容的权利归各自权利人所有。
- [Poe2DB 简体中文账号页](https://poe2db.tw/cn/account)：WeGame 分享解析入口；插件读取 Poe2DB 返回的简体中文构筑页和 PoB2 导入码。
- [poe.ninja API 文档](https://poe.ninja/docs/api)：当前赛季与 PoE2 通货价格的数据接口。
- [poecurrency.top API 文档](https://poecurrency.top/api.html)：国服当前赛季及通货均价接口。
