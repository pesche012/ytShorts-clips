import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import normalize_highlights, parse_json3, parse_timestamp, parse_vtt, safe_filename


class CoreTests(unittest.TestCase):
    def test_safe_filename(self):
        self.assertEqual(safe_filename('  A/B:C*D?  '), "A_B_C_D")

    def test_parse_timestamp(self):
        self.assertAlmostEqual(parse_timestamp("01:02:03.500"), 3723.5)
        self.assertAlmostEqual(parse_timestamp("02:03.250"), 123.25)

    def test_parse_json3(self):
        payload = {
            "events": [
                {"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "こんにちは"}]},
                {"tStartMs": 3000, "dDurationMs": 1000, "segs": [{"utf8": "世界"}]},
            ]
        }
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "a.json3"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            result = parse_json3(path)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].text, "こんにちは")
        self.assertEqual(result[1].start, 3.0)

    def test_parse_vtt(self):
        content = "WEBVTT\n\n00:00:01.000 --> 00:00:03.500\n<b>テスト</b>です\n"
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "a.vtt"
            path.write_text(content, encoding="utf-8")
            result = parse_vtt(path)
        self.assertEqual(result[0].text, "テストです")
        self.assertEqual(result[0].end, 3.5)

    def test_normalize_highlights(self):
        items = {
            "highlights": [
                {"start": index * 20, "end": index * 20 + 18, "title": f"場面{index + 1}", "reason": "理由", "score": 90 - index}
                for index in range(7)
            ]
        }
        result = normalize_highlights(items, 300)
        self.assertEqual(len(result), 7)
        self.assertEqual(result[-1].title, "場面7")


if __name__ == "__main__":
    unittest.main()
