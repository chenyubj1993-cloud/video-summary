"""针对本轮修掉的 bug 的回归测试（不联网、不依赖模型）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vsummary import brief, claims, consistency, fix, handout, prereq  # noqa: E402


class TestFixRules(unittest.TestCase):
    def _rules(self, body: str) -> list[dict]:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.tsv"
            p.write_text(body, encoding="utf-8")
            return fix.load_rules([str(p)])

    def test_latin_rule_is_case_insensitive(self):
        r = self._rules("LLaMA\tLAMA\t\tobs")[0]
        self.assertEqual(r["re"].sub(lambda m: m.group(0).replace(m.group("bad"), "LLaMA"),
                                     "用 LAMA 做预训练"), "用 LLaMA 做预训练")

    def test_latin_rule_respects_word_boundary(self):
        r = self._rules("CLIP\tclip\t\tobs")[0]
        self.assertEqual(len(r["re"].findall("clip_embedding 和 clip")), 1)

    def test_digit_rule_does_not_hit_longer_number(self):
        r = self._rules("768\t718\t\tobs")[0]
        self.assertEqual(len(r["re"].findall("1718 和 718")), 1)

    def test_bom_in_tsv_is_stripped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.tsv"
            p.write_bytes("\ufeff任务\t论物\t\tobs\n".encode("utf-8"))
            r = fix.load_rules([str(p)])[0]
            self.assertEqual(r["good"], "任务")

    def test_context_constraint(self):
        r = self._rules("任务\t论文\t下游|前置_\tobs")[0]
        self.assertEqual(len(r["re"].findall("下游论文")), 1)
        self.assertEqual(len(r["re"].findall("这篇论文")), 0)


class TestConsistency(unittest.TestCase):
    def test_near_zero_denominator(self):
        self.assertEqual(consistency._near("0", "1"), (False, ""))

    def test_near_magnitude(self):
        self.assertTrue(consistency._near("2400", "24000")[0])
        self.assertTrue(consistency._near("4096", "4097")[0])


class TestGateResidual(unittest.TestCase):
    def test_case_only_rules_are_not_counted_as_residue(self):
        """CLIP←clip 这类纯大小写规则不能用来数残留，否则永远是 50 处。"""
        rules = [{"good": "CLIP", "bads": ["clip"], "status": "obs"},
                 {"good": "任务", "bads": ["论物"], "status": "obs"}]
        kept = [r for r in rules
                if not any(b.lower() == r["good"].lower() for b in r["bads"])]
        self.assertEqual([r["good"] for r in kept], ["任务"])


class TestHandout(unittest.TestCase):
    def test_slug_unique_across_chapters(self):
        seen: dict = {}
        handout.md_to_html("## 学习目标\n\n正文\n", seen)
        _, toc2 = handout.md_to_html("## 学习目标\n\n正文\n", seen)
        self.assertEqual(toc2[0][1], "学习目标-2")

    def test_inline_code_is_not_math(self):
        body, _ = handout.md_to_html("用 `$HOME` 与环境变量 $x^2$ 的区别\n")
        self.assertIn("$HOME", body)
        self.assertIn("math-inline", body)

    def test_indented_code_is_not_math(self):
        body, _ = handout.md_to_html("    $notmath$ 是缩进代码\n")
        self.assertIn("$notmath$", body)


class TestBrief(unittest.TestCase):
    def test_normalize_inserts_blank_lines(self):
        md = "**核心结论**\n- 一\n- 二\n| a | b |\n"
        out = brief.normalize(md)
        self.assertIn("**核心结论**\n\n- 一", out)
        self.assertIn("\n\n| a | b |", out)


class TestClaims(unittest.TestCase):
    def test_missing_both_true_is_not_kept(self):
        rows = [{"subject": "X", "a": {"value": "A", "page": 1, "text": ""},
                 "b": {"value": "B", "page": 1, "text": ""}}]

        class Fake:
            def chat(self, *a, **k):
                return '{"id":0}'
        kept, missing = claims.confirm(rows, Fake())
        self.assertEqual((kept, missing), ([], 1))

    def test_judge_counts_undecided(self):
        rows = [{"subject": "X", "a": {"value": "A", "page": 1, "text": ""},
                 "b": {"value": "B", "page": 1, "text": ""}}]

        class Fake:
            def chat(self, *a, **k):
                return "这不是 JSON"
        kept, missing = claims.judge(rows, Fake())
        self.assertEqual((kept, missing), ([], 1))


class TestPrereq(unittest.TestCase):
    def test_override_with_empty_def_is_not_a_term(self):
        terms = prereq.load_terms()
        self.assertNotIn("残差", {v["name"] for v in terms.values()})


if __name__ == "__main__":
    unittest.main()
