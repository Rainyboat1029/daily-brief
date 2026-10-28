# daily-brief —— 每天定时把「天气 + 新闻摘要」推到手机

纯标准库实现，不需要服务器、不需要一直开着的电脑。跑在 GitHub Actions 上，
每天北京时间 07:07 执行（由 cron-job.org 触发，不用 GitHub 自带定时，原因见下面「定时触发」），取稿窗口固定为**昨天 07:00 → 今天 07:07**：

```
Open-Meteo 查天气（免费、免 key）
      +
10 个源抓新闻（中新网 4 频道 / 中国政府网 / IT之家 / 鹰角官网 / 3 家游戏媒体）
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
| **07:07（当前设置）** | `7 23 * * *` |
| 07:00 | `0 23 * * *`（整点，容易被 GitHub 排队延迟） |
| 08:07 | `7 0 * * *` |
| 08:00 | `0 0 * * *`（整点，容易被延迟） |

**改取稿窗口**：默认 `WINDOW_START: "07:00"`（在 workflow 的 env 里），语义是「昨天 07:00 → 现在」，07:07 运行时刚好覆盖昨天一整天。改成 `"06:00"` 就把窗口前移到昨天 06:00；留空则回退到旧的 `LOOKBACK_HOURS`（按小时数倒推）。

**例外**：明日方舟公告在 `feeds.json` 里单独写了 `lookback_hours: 72`，因为它报的是「即将开启的活动」，需要三天提前量；其它源都跟 `WINDOW_START` 走。

**改新闻源**：编辑 `config/feeds.json`。当前默认源（2026-10-27 逐个实测可用）：

| 源 | 取法 | 主题 | 上限 | 说明 |
|---|---|---|---|---|
| 中新网·要闻 | RSS `china.xml` | 国内 | 10 | 中国新闻社，国家级通讯社 |
| 中新网·滚动 | RSS `scroll-news.xml` | 国内 | 10 | 更新最快，与要闻去重 |
| 中新网·财经 | RSS `finance.xml` | 国内 | 6 | 经济数据、产业政策 |
| 中新网·国际 | RSS `world.xml` | 国际 | 8 | 交给模型只挑重大事件 |
| 中国政府网·要闻 | 首页 HTML | 政策 | 8 | 国务院政策原文；该站已无 RSS，用 `type: html_list` 抓 |
| IT之家 | RSS | 科技 | 8 | `only` 白名单只留小米/苹果 |
| 鹰角官网·明日方舟公告 | 页面内嵌 JSON | 明日方舟 | 4 | 官方公告原文，见下面「明日方舟怎么筛」 |
| 游研社 | RSS | 明日方舟 | 4 | 游戏媒体，`only` 只捞带「明日方舟」的 |
| 机核 | RSS | 明日方舟 | 4 | 同上 |
| 游戏陀螺 | RSS | 明日方舟 | 4 | 同上 |

`max` 是每源上限，`topic` 是送模型的标签，`only` 是大小写不敏感白名单（命中任一关键词才保留）。
某个源哪天挂了会自动跳过，不影响整份简报——看日志里「抓取失败」那行就知道该换源。

**已实测失败、别再捡回来的源**：新华网 RSS（栏目 2022 年 12 月后冻结，内容全是旧的）、人民网 RSS（`<channel>` 为空）、央视网 RSS（404）、光明网/经济日报/科技日报/求是/参考消息/中国日报中文（均 404）、新浪/观察者网/环球网 RSS（0 条）、36氪（WAF 验证页）、cnBeta（HTML）、澎湃 feedx（超时）、V2EX（拒绝连接）、联合早报（XML 损坏）、BBC 中文（按你的要求移除）。

**改简报风格**：`scripts/daily_brief.py` 里的 `build_prompt()`，改那段提示词即可。

## 已知限制

- GitHub 的定时任务不保证准点，整点（如 00:00、23:00 UTC）排队最严重，延迟几分钟到几十分钟都有可能，极端情况会被丢弃。所以 cron 特意用了 `7 23 * * *` 而不是 `0 23 * * *`。
- 取稿窗口锚定北京时间 `07:00`，不看 workflow 实际几点跑起来：哪怕 GitHub 延迟到 07:40 才执行，窗口仍是「昨天 07:00 → 07:40」，不会漏掉凌晨的新闻，只会多几条。
- 连续 60 天仓库无任何提交，GitHub 会自动停用 scheduled workflow，随便 push 一下就能恢复。
- RSS 会失效：36氪、cnBeta、澎湃（feedx）目前有反爬或已停更，已从默认列表移除。
- 中国政府网首页改版会让「要闻」链接规则失效；真挂了会体现在日志的「中国政府网·要闻: 0 条」，改 `feeds.json` 里的 `pattern` 或删掉该源即可。
- 官方媒体的 RSS 关停是常态（新华网、人民网、央视网都已停），所以源列表要当作易耗品维护。
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

所以现在的做法是：**用同量级、可稳定抓取的官方源替代**（中新网四个频道 + 中国政府网要闻 + IT之家/Solidot 里的小米苹果科技），
再让模型按「新闻早班车」的取舍标准筛选——国内要闻为主，国际只留重大事件，社会琐事一律丢。

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

## 明日方舟怎么筛

需求是「只要国服《明日方舟》，不要《终末地》」，代码里三道闸：

1. **官方公告优先**：鹰角官网 `/news` 页把公告列表以转义 JSON 内嵌在 HTML 里，`parse_hypergryph()` 按 `cid` 切块解析出 `title / brief / displayTime`，取 **72 小时内**最新 4 条。该页只有国服公告，所以活动、限定寻访、制作组通讯、联动、闪断更新都能拿到。
2. **黑名单**：`exclude` 里写死了 `终末地 / endfield / 日服 / 国际服 / 繁中 / 台服 / 韩服 / 美服`，命中即丢——所以同公司的《明日方舟：终末地》和非国服公告不会混进来。
3. **游戏媒体捞漏**：游研社、机核、游戏陀螺三家走 `only: ["明日方舟"]` 白名单，只保留标题或摘要里带「明日方舟」的文章，用来补联动报道、行业解读这类官方公告不写的内容。这三家体量小，多数天是 0 条，属正常。

简报里的呈现规则：`【明日方舟】0-3 条`，要求写清活动/寻访名称、起止时间、是否有新干员或联动；当天没有国服新公告就**整节省略**，不许拿旧闻凑数。

## 每条新闻都带信源时间

简报里每条都长这样，方括号里是**该条新闻自己的发布时间**，不是生成简报的时间：

```
【国内要闻】
1. [10-27 21:35] 央行下调存款准备金率 0.25 个百分点。此次降准...
2. [10-28] "我将无我，不负人民"的时代答卷。...
【明日方舟】
1. [10-24 20:30] [限定寻访·庆典]【将雪投火】限时寻访即将开启。...
```

时间的来源：

| 源 | 时间来源 | 精度 |
|---|---|---|
| 中新网 / IT之家 / 游研社 / 机核 / 游戏陀螺 / 触乐 | RSS 的 `pubDate` | 到分钟 |
| 鹰角官网 | 公告内嵌 JSON 的 `displayTime` | 到分钟 |
| 中国政府网 | 列表页不给时间，用**抓取当日** | 只到日期 |

实现上是在候选行里就带上 `[MM-DD HH:MM]`，提示词要求模型「照抄，一个字都不要改」，并且明确写了「候选行没给时间就不要写方括号」——这样它既不会编时间，也不会把方舟三天前的公告写成今天。

## 选稿与文风（提示词）

约束都在 `build_prompt()` 里，改风格只需要改这一段：

1. **选稿权重**：国内要闻 6-8 条（政策部署、重要会议、经济数据、重大工程、民生政策、立法反腐）→ 国际 2-4 条（只收战争与冲突的局势变化、大国政坛重大变故、冲击全球市场的事件；没有够格的就写「今日无重大国际事件」）→ 科技 2-4 条，且只认小米（Redmi、澎湃 OS、小米汽车）与苹果（iPhone、iPad、Mac、Apple Intelligence）→ 明日方舟 0-3 条（只写国服新活动/联动/版本更新，没有就省略）。
2. **文风**：通讯社简讯体，客观、克制、信息密度高。禁止破折号「——」，禁止「据悉 / 小编 / 我们」这类口吻，不用感叹号和网络流行语，不评价、不煽情、不预测。
3. **事实纪律**：只允许使用候选新闻里的事实，不补充背景、不编造数字引语日期，拿不准就不写；每条不超过 70 字，全篇不超过 900 字。

模型调用失败时会退化成 `fallback_digest()`：只列原始标题、标注「未经筛选」，不会假装是整理过的新闻。

## 定时触发（重要）

本仓库**不用 GitHub 自带的 `schedule`**，实测失效：

- 换过 `0 0 * * *`、`7 0 * * *`、`7 23 * * *` 三个表达式，两天时间，`schedule` 事件 0 次；
- 又加了一个 `*/5 * * * *` 的探针 workflow，推到仓库 35 分钟内该触发 6 次，实际 0 次；
- 同期 GitHub 状态页显示 All Systems Operational，排除平台故障；
- 每次手动 `workflow_dispatch` 都正常，说明 workflow 本身没问题。

结论：这个仓库的 GitHub 定时器不工作（新账号被限流的可能最大），修不了，只能换外部定时器。

所以 `daily-brief.yml` 里只有 `workflow_dispatch`，每天的触发由 **cron-job.org** 调 GitHub API 完成：

1. 建一个 **fine-grained PAT**（https://github.com/settings/personal-access-tokens/new）：只选 `daily-brief` 这一个仓库，权限只勾 **Actions: Read and write**，有效期 90 天。
2. https://cron-job.org 注册免费账号（免费额度 50 个任务、最小间隔 1 分钟，够用）。
3. 新建 cronjob：
   - URL：`https://api.github.com/repos/Rainyboat1029/daily-brief/actions/workflows/daily-brief.yml/dispatches`
   - Method：`POST`
   - 时间：每天 `07:07`，时区 `Asia/Shanghai`
   - Headers：
     ```
     Authorization: Bearer <你的 PAT>
     Accept: application/vnd.github+json
     Content-Type: application/json
     ```
   - Body：`{"ref":"main"}`
4. 点 "TEST RUN"，返回 **204** 即成功，Actions 页面会立刻多一条 `workflow_dispatch` 记录。
5. 打开 cron-job.org 的失败通知（Execution failure），它挂了你才会知道。

命令行验证同样的接口：

```bash
curl -i -X POST \\
  -H "Authorization: Bearer $GITHUB_PAT" \\
  -H "Accept: application/vnd.github+json" \\
  "https://api.github.com/repos/Rainyboat1029/daily-brief/actions/workflows/daily-brief.yml/dispatches" \\
  -d '{"ref":"main"}'
```

返回 `HTTP/2 204` 就是成功（204 没有 body，别当成失败）。

如果哪天 GitHub 的 `schedule` 又能用了，把 `daily-brief.yml` 里注释掉的那四行取消注释，同时删掉 cron-job.org 的任务，否则一天会发两封。

## 花费

| 项目 | 费用 |
|---|---|
| GitHub Actions | 0（public 仓库免费不限分钟） |
| Open-Meteo | 0（免费无 key） |
| RSS | 0 |
| PushPlus / 飞书 webhook | 0 |
| DeepSeek API | 每天一次、约 1.5k token 输入 + 500 输出，按现行价折算约 **每天几分钱** |
