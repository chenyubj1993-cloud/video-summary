# 视频讲义生成器

给一个 B 站链接（或 BV 号、av 号、b23.tv 短链），在本地把它变成**一份能读、能查、能打印的中文讲义**：

```
链接 → 音频 → 转写 → 纠错 → 逐集摘要 → 分章讲义（HTML/PDF） → 逐步体检 → 交付
```

单集、多分P 合集、几十分钟的纪录片都能跑；课程走"教科书式"、评论/纪实走"笔记式"，由标题自动判断（`--style` 可强制）。一条命令跑完，中途断了重跑同一条即可。全部在本机完成，只有"写讲义正文"那几步会调一次大模型（可换本地 Ollama）。

---

## 它产出什么

跑完一集 7 小时的 40 分P 课程（BV1E9U8BBEvZ，多模态视觉大模型），`out/BV1E9U8BBEvZ/` 下会有：

| 产物 | 内容 | 规模 |
|---|---|---|
| `讲义.html` | **最终交付物**：单文件、侧边目录、图片内嵌、可直接打印成 PDF | 1.5 MB / 97 页 |
| `讲义.md` | 同一份内容的 Markdown | 24,774 汉字 |
| `精简版.html` / `.md` | 复习稿：一页地图 + 十条总纲 + 每章核心结论/概念表/数字/易错点 | 4,485 汉字，约 15 分钟读完 |
| `全课转写_已纠错.md` | 全文，每句带 `?p=N&t=秒` 深链，点开跳到视频那一秒 | 156,653 字 |
| `changes.md` | 每一处纠错的「改前 → 改后」，可逐条核对 | 273 处 |
| `总览.md` | 目录 + 40 篇逐 P 摘要 | 100 KB |
| `板书.md` | 抽帧 + OCR 得到的画面文字（PPT 公式、代码） | 180 帧 |
| `prereq.md` / `termsheet.md` | 前置顺序体检 / 名词速查表 | — |

讲义自带 6 个附录：分P 索引、术语纠错表、转写来历、前后不一致清单、画面文字、名词速查；正文含 15 张画面图、28 道自测题。

---

## 30 秒上手

```bat
:: 1. 依赖
python -m pip install -r requirements.txt

:: 2. 模型（约 1.5GB，从镜像下）
curl -L -o models\faster-whisper-large-v3-turbo\model.bin ^
  https://hf-mirror.com/mobiuslabsgmbh/faster-whisper-large-v3-turbo/resolve/main/model.bin
:: 同目录还要 tokenizer.json / vocabulary.json / config.json / preprocessor_config.json
::（文件清单见 docs/方案总结.md「模型准备」）

:: 3. 大模型 key（任一 OpenAI 兼容接口；只用本地 Ollama 就随便填）
set OPENAI_API_KEY=sk-...

:: 4. 跑
python cli.py all BV1E9U8BBEvZ --dry-run    :: 先看它打算干什么
python cli.py all BV1E9U8BBEvZ              :: 开跑
```

跑完看 `out/BV1E9U8BBEvZ/讲义.html`。

没有 key 也能跑一半：`python cli.py all BV1E9U8BBEvZ --no-llm` 只出转写与纠错稿。

### 输入怎么写都行

21 条命令都收下表里任何一种写法，不用先自己抠 BV 号：

```bat
python cli.py all BV1E9U8BBEvZ
python cli.py all bv1E9U8BBEvZ
python cli.py all av114261397346227
python cli.py all https://www.bilibili.com/video/BV1E9U8BBEvZ
python cli.py all "https://www.bilibili.com/video/BV1E9U8BBEvZ?p=3&spm_id_from=333.1007"
python cli.py all https://b23.tv/xxxxxxx
python cli.py all "【多模态教程】https://www.bilibili.com/video/BV1E9U8BBEvZ?p=2"
```

带 `?p=N` 的链接只做那一集（内部等价于 `--parts N`）。

### 单集也能跑，不只是"课程合集"

- 分章：有分P 编号就按编号；单集（或一集太长）按音频时间切，每章约 25 分钟。
- 风格：`--style auto`（默认）先看标题——出现「教程/课程/入门/详解/实战/考研/精讲」等走**教科书式**（为什么需要它 / 它是怎么做 / 取舍 / 代码），否则走**笔记式**（这一段讲了什么 / 关键信息 / 值得留意的说法）。
- 体检门槛自适应：19 分钟的片子要求 1 章、覆盖率 18 字/分钟；6 小时的课要求 3 章、35 字/分钟。

实测两集非课程内容：`BV1xzy2zMExY`（19 分钟时政吐槽）出 1 章 2,226 字、46 处出处；`BV1T917zFEwb`（69 分钟考研带背）自动切成多章。

**不支持的源**：番剧 / 会员专享 / 需要登录才能看的片（会被重定向到 bangumi，yt-dlp 拿不到普通音轨）。报错里会直接说明，并给出手动放音频走 `--no-dl` 的退路。

---

## 命令

`python cli.py` 不带参数打印全部用法；`python cli.py check` 自检环境（模型、CUDA、词表）。

| 命令 | 作用 | 需要 key |
|---|---|---|
| `all BV号` | 跑完整流水线，按产物新旧断点续跑 | 部分步骤 |
| `transcribe --bv BV号` | 下载音频 + GPU 转写（6.9h → 13 分钟） | — |
| `classify BV号` | 判定行业，决定加载哪张词表 | — |
| `fix BV号 --emit` | 词表纠错，产出 `全课转写_已纠错.md` + `changes.md` | — |
| `summarize BV号` | 逐 P 摘要 | ✓ |
| `overview BV号` | 拼 `总览.md` | — |
| `chapters BV号` | 分章写讲义正文（`--style textbook` 教材体，默认） | ✓ |
| `annotate BV号` | 给术语首次出现处补一句话注解 | — |
| `exercises BV号` | 每章 4 道自测题，答案带出处、默认折叠 | ✓ |
| `figures BV号` | 从抽帧里选图插进对应小节 | — |
| `termsheet BV号 --all` | 名词速查：术语 → 一句话定义 | 可选 |
| `prereq BV号` | 前置顺序体检：哪些术语先用了、后面才解释 | — |
| `handout BV号` | 拼 `讲义.html` + `讲义.md` | — |
| `gate BV号` | 交付前硬检查，不达标退出码 1 | — |
| `brief BV号` | 生成精简复习稿，不动原稿 | ✓ |
| `ask BV号 "问题"` | 从转写里检索并回答，带可点出处 | 可选 |
| `faithful BV号 --sample 100` | 抽检：讲义的话有原文支持吗 | ✓ |
| `series BV号` | 把一个合集当一门课：地图 / 重复概念 / 跨期不一致 | ✓ |
| `consistency BV号` | 同一属性被说出两个不同数字的清单 | — |
| `claims BV号 --llm` | 概念层面的前后矛盾（定义/数量/比较） | ✓ |
| `cite BV号` | 校验正文里每条 `[Pxx mm:ss]` 是否真的存在 | — |
| `frames BV号 --pages 5,14` | 抽帧 + OCR，补画面里的板书 | — |
| `coldstart BV号` | 自动猜听错的英文专名（只出候选） | — |
| `harvest BV号` | 从摘要里反向挖候选误听对 | — |
| `test` | 跑单元测试（18 例，不需要网络/GPU/key） | — |
| `check` | 自检环境：目录、模型、CUDA、词表 | — |

### 断点续跑

不写状态文件，靠**产物新旧**判断：每个阶段声明自己的产物，产物齐全且比上游新就跳过。

```bat
python cli.py all BV号 --dry-run        :: 只打印计划
python cli.py all BV号 --from chapters  :: 从某阶段往后
python cli.py all BV号 --only fix,handout
python cli.py all BV号 --force          :: 全部重跑
```

### 换模型

```bat
set OPENAI_API_KEY=sk-...
python cli.py all BV号                          :: 默认 DeepSeek
python cli.py chapters BV号 --api-base https://api.moonshot.cn/v1 --model kimi-k2
python cli.py chapters BV号 --api-base http://127.0.0.1:11434/v1 --model qwen2.5:14b --api-key ollama
```

任何 `/chat/completions` 都能接，不需要装 openai 包。

---

## 目录结构

```
cli.py                 统一入口，只做命令分发
domains.json           10 个行业的特征词与摘要模板
vsummary/
  core.py              路径、B站接口、音频解码、GPU DLL 注入
  transcribe.py        下载 + 转写（GPU 优先，失败回落 CPU；--parts 与旧结果合并）
  classify.py          行业判定
  fix.py               词表纠错（只应用 obs 规则）
  harvest.py           从摘要反向收回候选误听对
  coldstart.py         词表冷启动（编辑距离）
  llm.py               OpenAI 兼容客户端（重试、超时、用量）
  summarize.py         逐P 摘要
  overview.py          拼总览
  chapters.py          分章写正文（lecture / textbook 两种风格，长料先 map 再 reduce）
  annotate.py          术语首现处补注解（可重复执行，先收回再补）
  termsheet.py         名词速查表
  prereq.py            前置顺序体检
  gate.py              交付硬检查
  consistency.py       数值矛盾
  claims.py            概念矛盾（句式版 / LLM 版）
  cite.py              时间码校验 + 深链渲染
  frames.py            抽帧 + OCR + 对齐
  figures.py           选图并插进章节
  exercises.py         自测题
  brief.py             精简复习稿
  handout.py           拼讲义（HTML + MD + 6 个附录）
  pipeline.py          all：串流程 + 断点续跑
glossary/              词表：common / ai / series/<bv> / terms/ai（专名词典）/ terms_override
models/                faster-whisper（自行下载）
out/<BV号>/            产物
docs/                  方案总结
```

---

## 词表：纠正听错的专名

转写错得最多的不是普通词，是专名（`DINO` 听成 `Deno`、`ResNet50` 听成 `lessnets50`）。词表按四列 TSV：

```
规范词<TAB>误听形(|分隔)<TAB>上下文约束(前_后)<TAB>状态(obs/hyp)
任务	论物|论武|论五			obs
任务	论文	下游_|前置_		obs
DINO	Deno			obs
ResNet50	lessnets50			hyp
```

三条硬规矩：

1. **只存误听形**，不存正确写法——反过来做会把对的字改错。
2. **合法词不进表**。`论文` 本身合法，只有前面是「下游 / 前置」时才替换。
3. `hyp` 只统计命中次数不动文本；确认后改 `obs` 再跑 `fix --emit`。

三层：`common.tsv`（通用）→ `ai.tsv` 等领域词表 → `series/<bv>.tsv`（这一部课专有的名字）。
需要新词时先跑 `harvest` 和 `coldstart`，人只做确认。

---

## 讲义有两种写法（`--derive`）

默认按"每节讲一个概念 + 一个取舍"写，信息密度高但读起来像考点汇总。加 `--derive` 换成推演链：

```bat
python cli.py chapters BV1E9U8BBEvZ --only 3 --force --style textbook --derive --density 150 --out handout_derive
```

每小节按「上一节留下什么 → 它为什么难（写出矛盾）→ 这一节的办法 → 代价 → 代码」推下来，
材料没讲的地方必须写成「材料未展开，按上下文推断：…」。

同一章的差别（ch3）：3,766 → 5,711 汉字，时间码 152/152 仍全部核对到原文。
DINO 那节会写清"不要负样本就会塌缩 → teacher 直接拷贝等于追自己尾巴 → EMA 让目标慢下来 →
centering 再压住被主导的维度"，而不是只给三行结论。

`--out` 可以把结果写到另一个目录，方便两版并排比，确认后再覆盖 `handout/`。

## 怎么算合格（`gate`）

「讲得好不好」没法自动判，「有没有硬伤」可以。`python cli.py gate BV号` 查六件事，前四条不达标退出码就是 1：

| 检查 | 门槛 |
|---|---|
| 章节齐全 | ≥3 章，每章都有「学习目标」和「存疑与勘误」 |
| 出处可核对 | 章节里 `[Pxx mm:ss]` ≥98% 能落到真实转写句上（±15 秒） |
| 术语干净 | 已纠错转写里，obs 规则的误听形残留 = 0 |
| 前置顺序 | 出现 ≥10 次却从没定义的术语 = 0 |
| 覆盖率（参考） | 讲义每音频分钟 ≥35 个汉字 |
| 前后一致（参考） | 数值冲突组数，**只报不判** |

覆盖率那条是防止「少写少错」：只有准确性指标时，把讲义写空反而全对。
`gate` 已经接进 `all`，是最后一步。

## 想查点什么，别翻 97 页

```bat
python cli.py ask BV1E9U8BBEvZ "负样本队列有多大"
python cli.py ask BV1E9U8BBEvZ "DINO 为什么要去负样本" --top 8
python cli.py ask BV1E9U8BBEvZ "SAM 的分割是怎么做的" --no-llm   :: 只给原文，不问模型
```

10,800 句用 BM25 在内存里检索（不需要向量库、不需要 embedding 模型），命中的段落本来就带
`?p=N&t=秒` 深链，所以答案天然可回溯。加 `--llm`（默认，要 key）时只把命中的那几段交给模型，
并明确要求"原文没有就说没有"——问它没讲的事，它会拒绝而不是编，这一点做过对抗测试。

## 抽查：讲义有没有编

`gate` 只验"时间码落在某句转写上"，不验"那句话支持这个论断"。补这一层要用模型逐条判：

```bat
python cli.py faithful BV1E9U8BBEvZ --sample 100     :: 随机抽 100 句带出处的话
python cli.py faithful BV1E9U8BBEvZ --only 3         :: 只查第 3 章
```

做法：把讲义句子和它引用的那几句原话（离时间码最近的 6 句）一起交给模型，判
支持／部分支持／不支持，结果写进 `faithful.md`，把"不支持"的连出处列出来给人看。
本片实测：推演版讲义抽 40 条，**明确支持 17、部分支持 23、不支持 0（100% 有原文支撑）**；
早一版抽 100 条时是 42 / 56 / 2。
部分支持多是"原文说了、讲义做了归纳或换了措辞"，属于正常写作；它和 `gate` 互补——
`gate` 保证可回溯，`faithful` 保证没编。

---

## 把一个合集当成一门课（`series`）

单期工具满地都是，跨期没人做：第 12 期讲过的概念第 37 期又讲一遍，第 7 期说错的话第 31 期自己改了。
`series` 把 N 期放在一起看，产出三样东西：

```bat
python cli.py series BV1qqHZ6UEJy              :: 从这一集自动认出它属于哪个合集
python cli.py series BV1qqHZ6UEJy --max 8      :: 只取前 8 期
python cli.py series --bvs BV1qqHZ6UEJy,BV1Bghv6GEbv   :: 手给顺序，顺序就是讲述顺序
python cli.py series BV1qqHZ6UEJy --pages 1    :: 每期只取第 1 个分P（合集里每期本身是几小时）
```

- **课程地图**：每期解决什么问题、依赖哪一期，按顺序排出来；
- **重复讲过的概念**：同一个概念在哪几期出现，第一次在哪；
- **跨期不一致**：同一主体（模型/参数/数字）在两期给了不同值。

输出在 `out/_series/<合集号>/series.md`。实测 3 期（各取 P1）：地图 3 节、重复概念 1 个
（Transformer 在 E02 与 E03 都讲了）、跨期数值冲突 0 组。
局限要说清：它只比"同一主体"的两个值，跨期换措辞的说法抓不到；每期只读 P1 时，
结论也只覆盖 P1。

## 改一章不用重跑全书（增量）

`chapters` 会给每章算一个内容签名（转写文字 + 分P 范围的 md5），存在
`out/<bv>/chapters_state.json`。重跑时签名没变的章直接跳过：

```bat
python cli.py chapters BV1E9U8BBEvZ --style auto          :: 只重写变过的章
python cli.py chapters BV1E9U8BBEvZ --only 3 --force      :: 强制重写第 3 章
```

注意：讲义正文被人手改过之后，`chapters` 会按"比转写新"跳过，不会覆盖你的修改；
要覆盖就加 `--force`。下游的 `annotate`/`exercises`/`figures` 也有各自的"产物比上游新就跳过"。

## 自测（不用网络，不用 key）

```bat
python cli.py test
```

33 个用例（两个文件）：`tests/test_core.py` 覆盖输入解析、分章切分、出处解析、掩码、检索排序、门槛判定等纯逻辑；`tests/test_fixes.py` 是回归用例，钉住这一轮修掉的 bug——词表规则的大小写与词边界、`718` 不误伤 `1718`、BOM、`consistency` 除零、`claims` 缺字段不默认保留、附录 slug 全篇唯一、行内代码别当公式、`brief --only` 不丢其它章。几毫秒出结果。

## 只想看代码，不想跑

`out/` 与 `models/` 都在 `.gitignore` 里（前者是几 MB 的产物，后者是 3.5GB 权重）。所以 clone 下来是"干净的代码 + 词表 + 文档"，跑一次才会长出产物：

```bat
git clone <this repo> && cd video_summary
pip install -r requirements.txt
python cli.py check                 :: 看模型、依赖、词表在不在
python cli.py all "https://www.bilibili.com/video/BVxxxxxxx" --dry-run
```

模型要单独下（`models/faster-whisper-large-v3-turbo` 等，见上面「30 秒上手」）。

---

## 实测（RTX 5060 Laptop 8G / Ultra 7 251HX / 40 分P、6.92h）

| 项 | 数值 |
|---|---|
| 转写 | large-v3-turbo fp16，28–32x 实时（6.92h → 13 分钟），字级准确率约 99.9% |
| 纠错 | 47 条 obs 规则，命中 273 处 |
| 讲义 | 7 章、37,013 汉字、942 处出处全部核对到原文、12 张图、28 道题、108 页 PDF |
| 精简版 | 4,485 汉字（原稿的 16%），约 15 分钟读完 |
| 书稿密度 | 39 → 89 字/音频分钟（教材区间 60–100，讲稿 200–300） |
| 成本 | 全流程约 61 万 tokens ≈ ¥1；精简版另加 4 万 |
| 端到端 | 约 40 分钟（转写 13 分钟 + 抽帧 OCR 可与其它步骤并行） |

同一段 300 秒音频的速度对照：CPU small int8 3.5x、CPU medium int8 1.1x、GPU medium fp16 13x、GPU turbo fp16 28.9x。

另两种形态也跑通了（都不需要改代码）：

| 样本 | 形态 | 结果 |
|---|---|---|
| `BV1xzy2zMExY` | 单集 19 分钟时政吐槽 | 自动判为「笔记式」，1 章 2,226 字、46 处出处，覆盖率 82 字/分钟 |
| `BV1T917zFEwb` | 单集 70 分钟考研带背 | 按时间切成 3 章、182 处出处，覆盖率 109 字/分钟 |

---

## 已知局限

- **`avg_logprob` 在干净音频上失效。** 10,800 句里最小 -0.266，没有一句低于 -0.6——VAD 切干净后模型对错误同样自信。所以别指望「低置信句 + LLM 复核」，纠错只能靠词表 + 搭配约束。
- **字符层合法的错听治不了。** 例：「智源研究院」被听成「清华河资源研究院」，读音接近、字全合法，字符串表无解，只能人读。
- **平台字幕不能用。** 实测两个 40 分P 课程（80 个分P）：B 站 AI 字幕大面积串台（讲多模态的课字幕是英雄联盟解说，讲强化学习的课字幕是电视剧台词），且多个分P 字幕完全相同、覆盖率有的 869% 有的 8%。所以全程本地 ASR。
- **一致性检测只看得见"挨着属性词的数字"**，相隔几十分钟的口误、不带数字的概念矛盾会漏；宁可少报不乱报（这一集报 3 组，全对）。
- **断言抽取的句式版精度有限**（中文无词边界，主体常抓成上半句尾巴），只作保守兜底；找概念矛盾要加 `--llm`。
- **前置顺序体检只看术语表里那几十个词**，且判"定义"靠紧邻的 是/指/即/称为，会把"其实解释过了"的也报出来。
- **词表冷启动只治英文专名**（51 条英文规则找回 12 条）。中文同音与纯数字两条路都试过并否掉了。
- **讲义正文是模型写的**，覆盖率高不代表讲得对；`gate` 只保证"有出处、数字不打架、术语说清楚"，不保证"讲得好"。
- 只在 Windows + 单张 NVIDIA 消费卡上验证过，代码里没有 Windows 专属逻辑，但没在 Linux 上试。
- GPU 速度随功耗状态波动，同一模型同一段音频实测 32x 与 10x 都出现过，排期按 10x 留余量。

---

## 环境

- Python 3.12（DSH 自带运行时可直接用）；依赖见 `requirements.txt`
- GPU 只需 NVIDIA 驱动，**不需要 CUDA Toolkit**：装 `nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-nvrtc-cu12`，由 `vsummary/core.py:enable_cuda()` 在 `import ctranslate2` 之前把 DLL 目录注入 PATH
- 本机若挂着 socks 代理，pip 要加 `--proxy=`，程序内设 `NO_PROXY=*`
- 体积：turbo 模型约 1.5GB，medium 1.5GB，small 484MB，放 `models/` 下

`run.cmd` 是把本机自带 Python 路径写死的包装脚本，换机器后改里面的 `PY` 那一行即可。

设计取舍、每个坑的实测依据、以及各层为什么这么做，见 [`docs/方案总结.md`](docs/方案总结.md)。
