# 流放之路2资料问答（AstrBot 插件）

通过 `/poe2` 提问。插件从 [poe2db.tw 繁体中文版](https://poe2db.tw/tw/)检索对应词条，将少量页面文字交给当前会话的 AstrBot 聊天模型整理，并在回答后附上资料页链接。搜索和回答都不使用其他资料站点。

## 安装

从 [GitHub 仓库](https://github.com/xiaoliz1/astrbot_plugin_poe2db)克隆到 AstrBot 的 `data/plugins/`，然后重启 AstrBot，或在插件管理页加载插件：

```bash
cd AstrBot/data/plugins
git clone https://github.com/xiaoliz1/astrbot_plugin_poe2db.git
```

AstrBot 安装或更新插件时会读取 `requirements.txt` 安装 `beautifulsoup4`。当前会话还需要配置可用的聊天模型。

目录应为：

```text
AstrBot/data/plugins/astrbot_plugin_poe2db/
├── main.py
├── poe2db.py
├── metadata.yaml
├── _conf_schema.json
└── requirements.txt
```

## 使用

```text
/poe2 碎骨有什么效果？
/poe2 魔血的词缀是什么？
/poe2 元素專注輔助寶石有什么作用？
```

插件先让当前模型把问题转成最多两个站内搜索词，再读取最多两个匹配页面，由模型根据取得的文字生成简体中文回答。检索不到时会给出站内搜索链接；站点或模型不可用时会直接说明。默认缓存页面 15 分钟，以减少重复请求。

插件配置可在 AstrBot 管理页调整：资料页数 1–3、站点请求超时 5–30 秒、缓存 0–60 分钟。搜索范围固定为 `https://poe2db.tw/tw/`。本插件不预装站点数据，具体内容随站点更新；回答中的数值、版本和机制以附上的页面为准。

## 资料与开发依据

- [poe2db.tw/tw](https://poe2db.tw/tw/)：唯一检索来源。页面标示其 Wiki 内容使用 CC BY-NC-SA 3.0；本插件仅按需读取短文本用于问答，并附原页链接。
- [AstrBot 插件开发文档](https://github.com/AstrBotDevs/AstrBot/blob/master/docs/zh/dev/star/plugin.md)：插件入口、元数据与安装说明。
