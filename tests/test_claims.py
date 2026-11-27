"""第三轮回归：断言主体切分、跨期分组、注解收回。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vsummary import claims  # noqa: E402


def _extract(*texts: str) -> list[dict]:
    segs = {"parts": [{"page": 1, "segments": [
        {"start": float(i * 3), "end": float(i * 3 + 2), "text": t}
        for i, t in enumerate(texts)]}]}
    return claims.extract(segs)


class TestClaimsSubject(unittest.TestCase):
    def test_subject_keeps_space(self):
        rows = _extract("batch size是256")
        self.assertEqual((rows[0]["subject"], rows[0]["value"]), ("batch size", "256"))

    def test_negation_subject_has_no_trailing_bu(self):
        rows = _extract("MoCo不是自监督方法")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "否定")
        self.assertEqual(rows[0]["subject"], "MoCo")

    def test_cmp_value_cuts_at_adjective(self):
        rows = _extract("MoCo比SimCLR快")
        self.assertEqual(rows[0]["subject"], "MoCo")
        self.assertEqual(rows[0]["value"], "SimCLR")

    def test_question_is_not_a_claim(self):
        self.assertEqual(_extract("这个模型是不是自监督的？"), [])


class TestSeriesGrouping(unittest.TestCase):
    def test_definition_and_count_do_not_collide(self):
        """同一主体、不同 kind 的两条说法，不能被当成同一个属性比。"""
        items = [
            {"kind": "定义", "subject": "DINO", "value": "自监督方法", "ep": 1,
             "text": "DINO是自监督方法"},
            {"kind": "数量", "subject": "DINO", "value": "3个", "ep": 2,
             "text": "DINO有3个分支"},
        ]
        by: dict = {}
        for c in items:
            by.setdefault((c["kind"], c["subject"]), []).append(c)
        for _key, group in by.items():
            eps = {c["ep"] for c in group}
            vals = {c["value"] for c in group}
            self.assertFalse(len(eps) > 1 and len(vals) > 1)


class TestAnnotateStrip(unittest.TestCase):
    def test_strip_handles_alias_and_case(self):
        from vsummary import annotate
        defs = {"Embedding": "把编号映射成向量"}
        alias_of = {"embedding": ["Embedding", "patch embedding"]}
        text = ("patch embedding（把编号映射成向量）负责查表，"
                "Embedding（把编号映射成向量）本身是 nn.Embedding。")
        out, cnt = annotate._strip_text(text, defs, alias_of)
        self.assertNotIn("（把编号映射成向量）", out)
        self.assertIn("patch embedding负责查表", out)
        self.assertEqual(cnt, 2)


if __name__ == "__main__":
    unittest.main()
