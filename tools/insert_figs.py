"""在技术方案.md 的对应小节插入框图引用。"""

import pathlib

p = pathlib.Path(r"D:\video_summary\docs\技术方案.md")
t = p.read_text(encoding="utf-8")

INSERTS = [
    ("## 2. 端到端链路（15 个阶段）",
     "![端到端 15 个阶段：颜色区分吃算力的、要 key 的、纯本地的、检查类](figures/fig1-pipeline.png)"),
    ("## 3. 数据模型",
     "![四个目录的职责与流向：models 与 glossary 是输入，work 是中间物，out 是交付物](figures/fig3-dirs.png)"),
    ("## 4. 代码架构",
     "![分层与依赖方向：上层依赖下层，下层不知道上层存在](figures/fig2-layers.png)"),
    ("## 6. 质量体系（分层）",
     "![质量六层：前四层是 gate 的硬指标，后两层只打印不拦截](figures/fig4-gates.png)"),
    ("## 12. 附录",
     "![一次输出包含什么：一份主产物、三种形态、三份底稿、三份检查清单](figures/fig5-outputs.png)"),
]

for heading, img in INSERTS:
    if img in t:
        print("已存在，跳过：", heading)
        continue
    idx = t.find(heading)
    if idx < 0:
        print("找不到标题：", heading)
        continue
    end = t.find("\n", idx) + 1
    t = t[:end] + "\n" + img + "\n" + t[end:]

p.write_text(t, encoding="utf-8")
print("行数", len(t.splitlines()), "字符", len(t))
