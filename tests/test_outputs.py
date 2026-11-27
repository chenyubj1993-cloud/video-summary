"""渲染层与各处纯逻辑的回归测试（不联网、不依赖模型与已跑产物）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vsummary import (ask, chapters, coldstart, exercises, figures, gate,  # noqa: E402
                      handout, prereq, termsheet)


class TestHandout(unittest.TestCase):
    def _html(self, md: str) -> str:
        return handout.md_to_html(md)[0]

    def test_mathml_rendered(self):
        html = self._html("行内 $x^2$ 与\n\n$$a+b$$\n")
        self.assertIn("<math", html)
        self.assertNotIn("$x^2$", html)

    def test_inline_code_not_treated_as_math(self):
        html = self._html("环境变量 `$HOME` 别当公式\n")
        self.assertIn("$HOME", html)
        self.assertNotIn("<math", html)

    def test_table_and_code_survive(self):
        html = self._html("| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nx = $1\n```\n")
        self.assertIn("<table>", html)
        self.assertIn("$1", html)

    def test_slug_unique_across_chapters(self):
        seen: dict[str, int] = {}
        a = handout.md_to_html("## 学习目标\n\ntext", seen)[0]
        b = handout.md_to_html("## 学习目标\n\ntext", seen)[0]
        self.assertNotEqual(a, b)


class TestFigures(unittest.TestCase):
    def test_ocr_lines_both_shapes(self):
        self.assertEqual(figures.ocr_lines({"ocr": "a\nb"}), ["a", "b"])
        self.assertEqual(figures.ocr_lines({"_items": [(0, 0, "x"), (0, 5, "y")]}), ["x", "y"])

    def test_informative_prefers_code(self):
        plain = {"ocr": "总结\n线性探测\n全参数微调\n视觉 Prompt"}
        code = {"ocr": "def f(x):\n    return x * 2  # 注释\nmodel.fc = nn.Linear(768, 10)\nfor i in range(3):\n    print(i)"}
        self.assertEqual(figures.informative({"ocr": "架构图"}), 0.0)
        self.assertGreater(figures.informative(code), figures.informative(plain))

    def test_dedupe_collapses_same_page(self):
        frames = [{"ocr": "模型 蒸馏 损失 函数"}, {"ocr": "模型 蒸馏 损失 函数"}, {"ocr": "完全 不同 的 板书"}]
        self.assertEqual(len(figures.dedupe(frames)), 2)

    def test_nearest_cite_requires_same_page(self):
        text = "前文 [P05 01:00] 中间 [P06 02:00] 后文\n\n下一段"
        pos = figures.nearest_cite(text, 5, 65.0, tol=30)
        self.assertEqual(pos, text.index("\n\n"))     # 插在该段落末尾
        self.assertIsNone(figures.nearest_cite(text, 7, 65.0, tol=30))


class TestPrereq(unittest.TestCase):
    def test_mask_keeps_offsets(self):
        src = "甲 $x$ [P01 00:10] 乙\n\n```py\ncode\n```\n\n> 引用\n| 表 | 行 |\n"
        masked = prereq.mask(src)
        self.assertEqual(len(masked), len(src))
        self.assertNotIn("code", masked)
        self.assertNotIn("[P01 00:10]", masked)

    def test_pattern_latin_boundary(self):
        # 大小写不敏感是刻意的（正文常写成 patch embedding / ViT-B）
        self.assertEqual(len(prereq.pattern("ViT").findall("ViT 与 vit 以及 ViT-B")), 3)
        self.assertEqual(len(prereq.pattern("Embedding").findall("patch embedding 和 Embedding")), 2)

    def test_find_marks_definition_context(self):
        text = "先提到 ViT [P01 00:01]。\n\nViT 是一种把图像切成 patch 的骨干 [P01 02:00]。"
        hits = prereq.find(text, [prereq.pattern("ViT")])
        self.assertEqual(len(hits), 2)
        self.assertFalse(hits[0][1])
        self.assertTrue(hits[1][1])


class TestTermsheet(unittest.TestCase):
    def test_clean_folds_duplicate_paren(self):
        self.assertEqual(termsheet.clean("在无标注数据上训练（在无标注数据上训练）"), "在无标注数据上训练")

    def test_clean_truncates_at_sentence_end(self):
        got = termsheet.clean("用于对齐两个模态的表示。后面是另一句话，很长很长")
        self.assertTrue(got.startswith("用于对齐两个模态的表示。"))

    def test_find_def_needs_sentence_start(self):
        text = "我们知道这个 ViT 是指把图像切块。\nViT 是指把图像切成 patch 序列。"
        self.assertEqual(termsheet.find_def(text, ["ViT"]).strip(), "把图像切成 patch 序列")


class TestGate(unittest.TestCase):
    def test_chapter_threshold_by_duration(self):
        self.assertEqual(gate._need_chapters(10), 1)
        self.assertEqual(gate._need_chapters(60), 2)
        self.assertEqual(gate._need_chapters(200), 3)


class TestAsk(unittest.TestCase):
    def test_tokens_mixes_latin_digits_cjk(self):
        got = ask.tokens("MoCo 用 4096 个负样本")
        self.assertIn("moco", got)
        self.assertIn("4096", got)
        self.assertIn("负样", got)

    def test_expand_adds_alias(self):
        got = ask.expand("动量编码器怎么更新", {"动量编码器": ["momentum encoder"]})
        self.assertIn("momentum", got)


class TestExercises(unittest.TestCase):
    def test_parse_pairs(self):
        pairs = exercises.parse("Q|为什么用队列\nA|为了存负样本 [P05 01:02]\n\nQ|K 取多少\nA|4096 [P05 02:00]")
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0][0], "为什么用队列")
        self.assertIn("P05", pairs[0][1])

    def test_parse_ignores_incomplete(self):
        self.assertEqual(exercises.parse("Q|只有问题没有答案"), [])


class TestColdstart(unittest.TestCase):
    def test_ratio_and_accept(self):
        self.assertGreater(coldstart.ratio("PanelGPT", "PandaGPT"), 0.6)
        self.assertFalse(coldstart.acceptable("info", "DINO", 0.5))
        self.assertTrue(coldstart.acceptable("lessnets50", "ResNet50", 0.7, count=3))

    def test_best_match_picks_closest(self):
        name, _score = coldstart.best_match("vikuna", ["Vicuna", "VGG", "ViT"])
        self.assertEqual(name, "Vicuna")


class TestChapters(unittest.TestCase):
    def test_strip_fence(self):
        self.assertTrue(chapters.strip_fence("```markdown\n# 第 1 章 标题\n\n正文\n```\n").startswith("# 第 1 章"))
        kept = "正文里的 ```py 不算围栏"
        self.assertEqual(chapters.strip_fence(kept), kept)

    def test_time_split_respects_target(self):
        def part(page: int) -> dict:
            segs = [{"start": float(s), "end": float(s + 4), "text": "一句话"}
                    for s in range(0, 3600, 5)]
            return {"page": page, "part": f"{page}. 小节", "duration": 3600, "segments": segs}

        got = chapters.time_split([part(1), part(2), part(3)], target=1500)
        self.assertGreaterEqual(len(got), 2)
        for pieces in got.values():
            total = sum(p["duration"] for p in pieces)
            self.assertLessEqual(total, 1500 * 1.6)


if __name__ == "__main__":
    unittest.main()
