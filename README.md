# daily-brief —— 每天定时把「天气 + 新闻摘要」推到手机

纯标准库实现，不需要服务器、不需要一直开着的电脑。跑在 GitHub Actions 上，
每天 UTC 00:00（北京时间 08:00）自动执行：

```
Open-Meteo 查天气（免费、免 key）
      +
5 个 RSS 源抓新闻
      ↓
DeepSeek 生成 400 字简报（唯一花钱的地方）
      ↓
PushPlus 微信 / 飞书机器人 / 邮件 → 手机
```

## 目录

```
daily-brief/
├── .github/workflows/daily-brief.yml   # 定时任务
├── scripts/daily_brief.py              # 全部逻辑，单文件约 400 行
├── config/feeds.json                   # 新闻源，随便改
├── .env.example                        # 本地测试用的变量示例
└── README.md
```

## 你需要提供的信息（就这 4 条）

| # | 要什么 | 说明 |
|---|---|---|
| 1 | **城市** | 中文名，**多个用逗号分隔**，如 `苏州,成都` |
| 2 | **推送渠道** | `pushplus`（微信，最省事）/ `feishu`（飞书群机器人）/ `email` |
| 3 | **推送时间** | 默认早上 8:00，可以直接说「改到 7:30」 |
| 4 | **新闻口味** | 默认综合 + 科技；也可以说加财经、游戏、体育 |

API key **不用发给我**：下面第 3 步你自己在 GitHub 里填进 Secrets。

## 部署（约 5 分钟）

### 1. 建仓库并上传

`.github` 是点开头的目录，网页拖拽上传经常会漏掉，建议用命令行：

```bash
cd daily-brief
git init
git add .
git commit -m "init daily brief"
git branch -M main
git remote add origin https://github.com/<你的用户名>/daily-brief.git
git push -u origin main
```

> 仓库设为 **public** 时 Actions 完全免费且不限分钟；public 意味着代码公开，
> 但 Secrets 里的 key 不会公开，这点是安全的。不想公开就设 private，
> 每天跑 1 分钟，私有库每月 2000 分钟额度也绰绰有余。

### 2. 打开 Actions

仓库页 → **Actions** 标签 → 左侧 `daily-brief` → 右侧 **Enable workflow**。
（第一次 push 后 GitHub 会提示启用，没提示就是已启用。）

### 3. 填 Secrets

**Settings → Secrets and variables → Actions → New repository secret**，逐条加：

| Secret 名 | 必填 | 值 |
|---|---|---|
| `DEEPSEEK_API_KEY` | ✅ | `sk-...`，[platform.deepseek.com](https://platform.deepseek.com/api_keys) 创建 |
| `CITY` | ✅ | `苏州,成都`（逗号分隔） |
| `PUSH_CHANNEL` | ✅ | 当前用 `pushplus`（微信）。不稳时改 `email`，同一份代码不用改 |
| `PUSHPLUS_TOKEN` | 用 pushplus 时 | 见下 |
| `FEISHU_WEBHOOK` | 用 feishu 时 | 见下 |
| `FEISHU_SECRET` | 可选 | 飞书机器人开了「签名校验」才需要 |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASS` `MAIL_TO` | 用 email 时 | 见下 |

可选仓库变量（Variables 标签，不是 Secrets）：`DEEPSEEK_MODEL`，默认 `deepseek-chat`。

**怎么拿 PushPlus token**：微信扫码登录 [pushplus.plus](http://pushplus.plus) →
「一对一推送」页面顶部就是一串 32 位 token。免费版每天有条数限制，一天一条够用。

**怎么拿飞书 webhook**：飞书群 → 群设置 → 群机器人 → 添加「自定义机器人」→
复制 Webhook 地址。如果勾了「签名校验」，把密钥填进 `FEISHU_SECRET`。

**邮件**：`SMTP_HOST` 填 `smtp.qq.com`（QQ 邮箱）或 `smtp.163.com`，
`SMTP_USER` 是邮箱地址，`SMTP_PASS` 是**授权码**不是登录密码，
`SMTP_PORT` 填 `465`，`MAIL_TO` 填收件邮箱。

### 4. 手动测一次

Actions → `daily-brief` → **Run workflow**：

- 先勾 `dry_run = true` 跑一次，日志里能直接看到生成的简报，不会真的推手机；
- 满意了再正常跑一次，手机会收到。

## 本地测试（可选）

```bash
cd daily-brief
CITY=苏州,成都 python3 scripts/daily_brief.py --dry-run
```

没有 `DEEPSEEK_API_KEY` 时会退化成原始摘要（不调用模型），用来验证抓取是否正常。
接上 key 后本地也能真推：

```bash
export DEEPSEEK_API_KEY=sk-xxx
export CITY=苏州,成都
export PUSH_CHANNEL=pushplus
export PUSHPLUS_TOKEN=xxxx
python3 scripts/daily_brief.py
```

## 常用改动

**改推送时间**：编辑 `.github/workflows/daily-brief.yml` 里的 cron。
**GitHub 的 cron 用 UTC，要减 8 小时**：

| 想要北京时间 | cron |
|---|---|
| **08:00（当前设置）** | `0 0 * * *` |
| 07:00 | `0 23 * * *` |
| 07:30 | `30 23 * * *` |
| 06:45 | `45 22 * * *` |

**改新闻源**：编辑 `config/feeds.json`，`max` 控制每个源最多取几条。
某个源哪天挂了会自动跳过，不影响整份简报——看日志里「抓取失败」那行就知道该换源。

**改简报风格**：`scripts/daily_brief.py` 里的 `build_prompt()`，改那段提示词即可。

## 已知限制

- GitHub 的定时任务不保证准点，高峰期会延迟几分钟到十几分钟。
- 连续 60 天仓库无任何提交，GitHub 会自动停用 scheduled workflow，随便 push 一下就能恢复。
- RSS 会失效：36氪、cnBeta、澎湃（feedx）目前有反爬或已停更，已从默认列表移除。
- BBC 中文在部分国内网络下连不上，GitHub 的海外 runner 通常正常；连不上会自动跳过。
- 天气数据来自 Open-Meteo，免费无 key，精度到城市级，不替代手机自带天气 App 的逐小时预报。

## 关于《人民日报》「新闻早班车」

你要求抓这个栏目原文，我实测了一圈，**目前没有稳定可行的自动抓取路径**，结论和原因是：

| 尝试过的入口 | 结果 |
|---|---|
| 人民日报客户端栏目页 `peopleapp.com/column/30051597014-...` | 单篇文章页可读，但**列表页 404**，拿不到当日新文章 ID；页面数据是 JS 渲染，背后的 `rmrb-bff-display-zh` 接口直接返回 400（缺签名/客户端凭证） |
| 网易号 / 搜狐号 人民日报镜像 | 网易是纯 JS 壳子（标题都是占位符），搜狐 301 |
| 搜狗微信搜索 `weixin.sogou.com` | 搜索页能出结果，但**返回的是第三方转载**（"新闻摘录""句子合集"之类），不是官方号当日原文；跟进文章链接被反爬拦住，只回 822 字节的验证页 |
| 人民网 RSS（`people.com.cn/rss/*.xml`） | XML 结构还在，但 `<channel>` 里**早就是空的**，0 条 |
| RSSHub / wechat2rss 公共实例 | 抓公众号必须先部署 wechat2rss 后端（要一台常开的 Docker），与"零成本零维护"冲突 |

所以现在的做法是：**用同量级、可稳定抓取的源替代**（中国新闻网 + BBC 中文 + IT之家 + Solidot + 少数派），
再让模型按「新闻早班车」的取舍标准筛选——硬新闻优先，社会琐事一律丢。

如果你确实想要早班车原文，三条路，按省事程度排：

1. **等你哪天在微信里刷到，把那条 `mp.weixin.qq.com/s?...` 链接发我**，我可以改成「每天抓这个固定栏目」——但公众号文章链接每天变，做不到无人值守。
2. **自己部署 wechat2rss**（Docker，一台常开机器），它会给你一个真正可订阅的 RSS，填进 `config/feeds.json` 即可，代码不用改。
3. **保持现状**：内容覆盖面其实差不多，只是措辞不是党报口吻。

## 过滤社会琐事

两级过滤：

1. **关键词预过滤**（`drop_junk()`，在进模型之前就丢掉，省 token）：
   默认词表在 `scripts/daily_brief.py` 的 `DEFAULT_JUNK`，含「不慎落水 / 救人 / 拾金不昧 / 走失 / 车祸 / 纠纷 / 暖新闻」等 30 个词。
   想改不用动代码，加环境变量 `FILTER_KEYWORDS="关键词1,关键词2"` 覆盖即可。
2. **模型筛选**（提示词里明确要求）：
   优先国家政策、经济数据、科技突破、国际局势、教育医疗住房；丢弃社会琐事、明星八卦、体育赛果、地方小事、标题党；
   并且要求「宁少勿滥」——宁可只给 4 条硬新闻，也不许为了凑数塞琐事。

## 花费

| 项目 | 费用 |
|---|---|
| GitHub Actions | 0（public 仓库免费不限分钟） |
| Open-Meteo | 0（免费无 key） |
| RSS | 0 |
| PushPlus / 飞书 webhook | 0 |
| DeepSeek API | 每天一次、约 1.5k token 输入 + 500 输出，按现行价折算约 **每天几分钱** |
