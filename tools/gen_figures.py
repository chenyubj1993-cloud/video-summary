"""生成技术方案文档用框图（HTML/SVG 由 Chrome 截图后自动裁白）。"""

import pathlib

OUT = pathlib.Path(r"D:\video_summary\docs\figures")
OUT.mkdir(parents=True, exist_ok=True)

CSS = """
* { box-sizing: border-box; }
body { margin: 0; padding: 26px 30px; background: #fff;
  font-family: "Microsoft YaHei", "Source Han Sans SC", system-ui, sans-serif;
  color: #1c1a18; }
.row { display: flex; align-items: stretch; margin: 0 0 14px 0; }
.lab { width: 132px; flex: 0 0 132px; font-size: 12.5px; color: #7a6f63;
  display: flex; align-items: center; justify-content: flex-end; padding-right: 14px;
  text-align: right; line-height: 1.35; }
.box { flex: 1 1 0; border: 1px solid #d9d2c7; border-radius: 7px; padding: 9px 11px;
  background: #fbf9f5; font-size: 13px; line-height: 1.42; }
.box b { font-size: 13.5px; display: block; margin-bottom: 2px; }
.box span { font-size: 11.5px; color: #6d635a; }
.arw { flex: 0 0 26px; display: flex; align-items: center; justify-content: center;
  color: #b3a89a; font-size: 15px; }
.blue { background: #eef4fd; border-color: #b8d0ef; }
.orange { background: #fdf1e7; border-color: #eec9a6; }
.green { background: #eef7ee; border-color: #b9dab9; }
.grey { background: #f6f4f1; }
.legend { display: flex; gap: 20px; font-size: 12px; color: #5f564d; margin-top: 6px;
  padding-left: 132px; }
.key { display: inline-block; width: 12px; height: 12px; border-radius: 3px;
  border: 1px solid #d9d2c7; margin-right: 5px; vertical-align: -1px; }
"""

LEG = ('<div class="legend">'
       '<span><i class="key blue"></i>吃 CPU/GPU 的重活</span>'
       '<span><i class="key orange"></i>需要大模型 key（可跳过）</span>'
       '<span><i class="key grey"></i>纯本地确定性计算</span>'
       '<span><i class="key green"></i>检查 / 交付</span></div>')


def page(body, w=1180):
    return (f"<!doctype html><meta charset=utf-8><style>{CSS}</style>"
            f'<body style="width:{w}px">{body}</body>')


def box(t, s, cls=""):
    return f'<div class="box {cls}"><b>{t}</b><span>{s}</span></div>'


A = '<div class="arw">→</div>'

# ---------------------------------------------------------------- 图 1 流水线
rows = [
    ("把视频变成文字", [box("1 download", "yt-dlp 只下音频 · 203 MB / 6.9 h · 约 15 min", "blue"), A,
                        box("2 transcribe", "faster-whisper large-v3-turbo · 156 k 字 · 13 min（32×）", "blue")]),
    ("改成可读的文本", [box("3 classify", "关键词打分猜行业，只用来决定加载哪份词表", "grey"), A,
                        box("4 fix", "术语纠错 · 47 条规则 · 命中 307 处", "grey")]),
    ("生成结构化内容", [box("5 summarize", "逐分P 一句话 + 要点 + 术语 + 存疑 · 40 次调用", "orange"), A,
                        box("6 overview", "把 40 篇摘要拼成总览", "grey"), A,
                        box("7 consistency", "抽数字，比同一分P 两分钟内的两次说法", "grey"), A,
                        box("8 chapters", "按章写正文，每句带 [Pxx mm:ss] · 约 10 min", "orange")]),
    ("补齐阅读辅助", [box("9 annotate", "术语首次出现处插一句定义", "grey"), A,
                      box("10 exercises", "每章 4 道自测题，答案可回查", "orange"), A,
                      box("11 figures", "变化检测抽帧 + OCR，选图插进章节", "blue")]),
    ("检查与拼装", [box("12 termsheet", "附录 F 名词速查 · 78 条", "orange"), A,
                    box("13 prereq", "前置顺序体检：哪些术语先用后解释", "grey"), A,
                    box("14 handout", "拼 HTML + MD，图片 base64 内嵌", "grey"), A,
                    box("15 gate", "六项检查，不过就退出码 1", "green")]),
]
fig1 = "".join(
    f'<div class="row"><div class="lab">{lab}</div>{"".join(items)}</div>'
    for lab, items in rows) + LEG
(OUT / "fig1-pipeline.html").write_text(page(fig1), encoding="utf-8")

# ---------------------------------------------------------------- 图 2 分层
fig2 = f"""
<div class="row"><div class="lab">入口</div>
  {box("cli.py", "27 条命令 · 先把 BV 号/链接/短链统一成 BV，再分发", "green")}</div>
<div class="row"><div class="lab"></div><div class="arw">↓</div></div>
<div class="row"><div class="lab">编排</div>
  {box("pipeline.py", "15 阶段 · 每个阶段自己报“产物比上游新吗” · 不写状态文件", "green")}</div>
<div class="row"><div class="lab"></div><div class="arw">↓</div></div>
<div class="row"><div class="lab">生成层<br>（会写新文字）</div>
  {box("chapters", "章节正文", "orange")}{A}{box("handout", "讲义与附录", "orange")}{A}
  {box("brief / exercises", "精简版 / 自测题", "orange")}</div>
<div class="row"><div class="lab">分析层<br>（下判断）</div>
  {box("claims", "概念矛盾", "grey")}{A}{box("consistency", "数字矛盾", "grey")}{A}
  {box("prereq", "前置顺序", "grey")}{A}{box("termsheet / annotate", "术语", "grey")}{A}
  {box("faithful", "忠实度抽检", "orange")}</div>
<div class="row"><div class="lab">都依赖</div><div class="arw" style="justify-content:flex-start">↓</div></div>
<div class="row"><div class="lab">底层<br>（无业务判断）</div>
  {box("core", "路径 / B站 API / 解码 / GPU 探测 / 原子写", "blue")}{A}
  {box("llm", "OpenAI 兼容客户端", "blue")}{A}
  {box("transcribe / frames", "转写 / 抽帧", "blue")}{A}
  {box("source", "输入解析", "blue")}{A}
  {box("cite", "时间码解析与核对", "blue")}</div>
<div class="legend"><span>箭头＝依赖方向。下层不知道上层的存在；有 5 处刻意的跨层 import，理由见 4.2。</span></div>
"""
(OUT / "fig2-layers.html").write_text(page(fig2), encoding="utf-8")

# ---------------------------------------------------------------- 图 3 目录
fig3 = f"""
<div class="row"><div class="lab">一次性输入</div>
  {box("models/", "faster-whisper 权重（1.5 GB），只读不写", "blue")}{A}
  {box("glossary/", "词表四层：common / 领域 / series / terms", "grey")}</div>
<div class="row"><div class="lab">参与计算</div><div class="arw" style="justify-content:flex-start">↓</div></div>
<div class="row"><div class="lab">中间产物<br>（可删）</div>
  {box("work/<BV>/", "音频、原始 segments、日志 · 约 350 MB", "blue")}</div>
<div class="row"><div class="lab">加工成</div><div class="arw" style="justify-content:flex-start">↓</div></div>
<div class="row"><div class="lab">最终产物<br>（要交付）</div>
  {box("out/<BV>/ 19 个文件", "转写 · 已纠错稿 · 章节 · 讲义 HTML/MD/PDF · 精简版 · 板书 · 三份检查清单", "green")}</div>
<div class="row"><div class="lab">暴露为</div><div class="arw" style="justify-content:flex-start">↓</div></div>
<div class="row"><div class="lab">交互</div>
  {box("ask", "问一句，回一条带时间码的证据", "green")}{A}
  {box("series", "把多期当成一门课", "green")}{A}
  {box("gate", "能不能交付", "green")}</div>
<div class="legend"><span>只有 <code>out/</code> 和 <code>glossary/series/</code> 是给人看的；<code>work/</code> 随时可清。</span></div>
"""
(OUT / "fig3-dirs.html").write_text(page(fig3), encoding="utf-8")

# ---------------------------------------------------------------- 图 4 质量六层
layers = [
    ("转写层", "字对不对", "156 k 字全部过词表；词表只收“误听形”，合法词不进表", "#eef4fd"),
    ("词表层", "专名一致", "47 条规则命中 307 处；大小写 + ASCII 边界 + 上下文约束", "#eef4fd"),
    ("结构层", "章齐不齐", "每章必须有「学习目标」「自测题」「存疑与勘误」", "#f6f4f1"),
    ("出处层", "话有没有依据", "931 处时间码逐条核对，落不到真实句段就标红", "#f6f4f1"),
    ("一致性层", "前后打不打架", "数字 3 组 + 概念 9 组，只报不判", "#fdf1e7"),
    ("文风层", "读得下去吗", "覆盖率 ≥35 字/分钟（防“少写少错”），密度实测 86", "#fdf1e7"),
]
rows4 = "".join(
    f'<div class="row"><div class="lab">第 {i} 层</div>'
    f'<div class="box" style="background:{c};flex:0 0 150px"><b>{n}</b><span>{sub}</span></div>'
    f'{A}<div class="box" style="background:{c}"><span style="font-size:12.5px;color:#3a342e">{d}</span></div></div>'
    for i, (n, sub, d, c) in enumerate(layers, 1))
fig4 = rows4 + ('<div class="legend"><span>前四层是 <code>gate</code> 的硬指标（不过就退出码 1）；后两层参考项，只打印不拦截。</span></div>')
(OUT / "fig4-gates.html").write_text(page(fig4), encoding="utf-8")

# ---------------------------------------------------------------- 图 5 产物
fig5 = f"""
<div class="row"><div class="lab">主产物</div>
  {box("讲义.html（单文件 1.6 MB）", "7 章正文 · 12 张画面图 · 28 道自测题 · 931 处可点出处 · 6 个附录", "green")}</div>
<div class="row"><div class="lab">同一份内容<br>的其它形态</div>
  {box("讲义.md", "39,053 汉字，可直接贴进笔记软件", "grey")}{A}
  {box("讲义.pdf", "111 页，打印用", "grey")}{A}
  {box("精简版", "4,441 汉字，约 15 分钟读完", "grey")}</div>
<div class="row"><div class="lab">可核查的底稿</div>
  {box("全课转写_已纠错.md", "123,105 汉字，每句带时间码", "blue")}{A}
  {box("板书.md", "446 段画面文字（OCR）", "blue")}{A}
  {box("changes.md", "307 处纠错逐条对照", "blue")}</div>
<div class="row"><div class="lab">三份清单</div>
  {box("consistency.md", "数字前后不一致：3 组", "orange")}{A}
  {box("claims.md", "概念前后不一致：9 组", "orange")}{A}
  {box("prereq.md", "术语先用后解释：23 个", "orange")}</div>
<div class="legend"><span>同样的底稿还能回答“负样本队列多大”这类问题（<code>cli.py ask</code>），答案带证据位置。</span></div>
"""
(OUT / "fig5-outputs.html").write_text(page(fig5), encoding="utf-8")

print("html 写完：", *[p.name for p in sorted(OUT.glob("*.html"))])
