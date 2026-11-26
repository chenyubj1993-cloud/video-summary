"""跑一遍：python -m unittest discover -s tests -v

只测"不需要网络、不需要 GPU、不需要 key"的那部分纯逻辑——
这些正好是改一次代码最容易碰坏、又最难靠肉眼发现的地方。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vsummary import ask, chapters, cite, gate, prereq, series, source  # noqa: E402


class TestSource(unittest.TestCase):
    def test_bv_forms(self):
        for t in ("BV1E9U8BBEvZ", "bv1E9U8BBEvZ", " 【教程】BV1E9U8BBEvZ 合集"):
            self.assertEqual(source.parse(t)[0], "BV1E9U8BBEvZ")

    def test_page_from_url(self):
        bv, p = source.parse("https://www.bilibili.com/video/BV1E9U8BBEvZ?p=3&spm_id_from=xx")
        self.assertEqual((bv, p), ("BV1E9U8BBEvZ", 3))

    def test_bad_input(self):
        with self.assertRaises(SystemExit):
            source.parse("https://space.bilibili.com/123456/video")

    def test_argv_keeps_bvs_list(self):
        got = source.normalize_argv(["series", "BV1E9U8BBEvZ", "--bvs", "BV1E9U8BBEvZ,BV1wFZ8YBEt4"])
        self.assertEqual(got[3], "BV1E9U8BBEvZ,BV1wFZ8YBEt4")

    def test_argv_turns_url_into_bv_and_parts(self):
        got = source.normalize_argv(["transcribe", "--bv", "https://www.bilibili.com/video/BV1E9U8BBEvZ?p=7"])
        self.assertEqual(got[2], "BV1E9U8BBEvZ")
        self.assertEqual(got[-2:], ["--parts", "7"])


def _parts(seconds: list[float]) -> list[dict]:
    out = []
    for i, dur in enumerate(seconds, 1):
        out.append({"page": i, "part": f"{i}. 小节", "duration": dur,
                    "segments": [{"start": 0.0, "end": dur, "text": "内容" * 10}]})
    return out


class TestChapters(unittest.TestCase):
    def test_auto_split_by_number(self):
        parts = _parts([600] * 4)
        for i, p in enumerate(parts, 1):
            p["part"] = f"{i}. 第{i}节"
        ch = chapters.auto_split(parts)
        self.assertEqual(len(ch), 4)

    def test_auto_split_by_time_when_no_numbers(self):
        parts = _parts([1800, 1800, 1800])
        for p in parts:
            p["part"] = "随笔"
        ch = chapters.auto_split(parts, target=1500)
        self.assertGreaterEqual(len(ch), 3)

    def test_sig_changes_with_text(self):
        a = _parts([60])
        b = _parts([60])
        self.assertEqual(chapters._sig(a), chapters._sig(b))
        b[0]["segments"][0]["text"] = "改过的内容"
        self.assertNotEqual(chapters._sig(a), chapters._sig(b))


class TestCite(unittest.TestCase):
    def test_parse(self):
        got = cite.parse("见 [P07 12:34] 与[P12 03:05]")
        self.assertEqual([(a, b) for a, b, _ in got], [(7, 754), (12, 185)])

    def test_no_false_positive(self):
        self.assertEqual(cite.parse("P07 12:34 这里没有方括号"), [])

    def test_stats(self):
        rows = [{"ok": True}, {"ok": False}, {"ok": True}]
        self.assertEqual(cite.stats(rows), (2, 3))


class TestPrereq(unittest.TestCase):
    def test_mask_keeps_length(self):
        text = "前面\n\n```\ncode 代码\n```\n\n后面 [P01 00:01] 结束"
        masked = prereq.mask(text)
        self.assertEqual(len(masked), len(text))
        self.assertNotIn("code", masked)
        self.assertNotIn("[P01 00:01]", masked)
        self.assertIn("前面", masked)

    def test_mask_hides_caption_and_table(self):
        text = "正文\n![图 1](a.jpg)\n*图 5-1　说明*\n| a | b |"
        masked = prereq.mask(text)
        self.assertNotIn("a.jpg", masked)
        self.assertNotIn("图 5-1", masked)
        self.assertNotIn("| a |", masked)
        self.assertIn("正文", masked)


class TestAsk(unittest.TestCase):
    def test_bm25_ranks_relevant_first(self):
        docs = [ask.tokens(t) for t in
                ("队列存放负样本，最多 4096 个", "这里讲图像分类的流程", "温度系数取 0.07")]
        b = ask.BM25(docs)
        hits = b.search("队列 负样本", k=1)
        self.assertTrue(hits)
        self.assertEqual(hits[0][1], 0)

    def test_tokens_split_cjk(self):
        self.assertIn("队列", ask.tokens("负样本队列"))

    def test_expand_uses_aliases(self):
        self.assertTrue(ask.expand("动量", {"动量": {"momentum"}}))


class TestGate(unittest.TestCase):
    def test_need_chapters(self):
        self.assertEqual(gate._need_chapters(19), 1)
        self.assertEqual(gate._need_chapters(70), 2)
        self.assertEqual(gate._need_chapters(415), 3)


class TestSeries(unittest.TestCase):
    def test_concept_key_normalizes(self):
        self.assertEqual(series.concept_key("Transformer "), series.concept_key("transformer"))


if __name__ == "__main__":
    unittest.main()
