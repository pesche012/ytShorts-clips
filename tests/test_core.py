import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import (
    AppCancelled,
    AppError,
    Highlight,
    TranscriptSegment,
    build_highlight_prompts,
    build_live_edit_prompts,
    format_duration_label,
    ensure_greeting_segment,
    normalize_highlights,
    normalize_live_edit_segments,
    parse_json3,
    parse_timestamp,
    parse_vtt,
    run_file_pipeline,
    safe_filename,
    _run_cancellable_command,
)


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
        result = normalize_highlights(items, 300, clip_count=3)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[-1].title, "場面3")

    def test_custom_highlight_prompt_is_included(self):
        custom_prompt = "テンポ重視で、30秒前後の場面を選ぶ"
        _, user_prompt = build_highlight_prompts(
            [TranscriptSegment(0, 2, "テスト文字起こし")],
            120,
            custom_prompt,
            clip_count=4,
        )
        self.assertIn(custom_prompt, user_prompt)
        self.assertIn("必ず4個", user_prompt)

    def test_blank_highlight_prompt_uses_standard_policy(self):
        _, user_prompt = build_highlight_prompts([], 120, "  ")
        self.assertIn("指定なし。標準方針で選定する", user_prompt)
        self.assertIn("必ず7個", user_prompt)

    def test_live_edit_prompt_includes_target_duration(self):
        _, user_prompt = build_live_edit_prompts(
            [TranscriptSegment(0, 10, "生配信の内容")],
            duration=600,
            target_seconds=90,
            custom_instructions="ゲームの勝負どころを優先",
        )
        self.assertIn("90 秒", user_prompt)
        self.assertIn("ゲームの勝負どころを優先", user_prompt)
        self.assertIn("時間を少し超えても途中で切らない", user_prompt)
        self.assertIn("冒頭の挨拶", user_prompt)

    def test_live_edit_segments_cover_target_duration(self):
        data = {
            "highlights": [
                {"start": 0, "end": 30, "title": "1", "reason": "理由", "score": 90},
                {"start": 40, "end": 80, "title": "2", "reason": "理由", "score": 80},
            ]
        }
        result = normalize_live_edit_segments(data, duration=120, target_seconds=60)
        self.assertEqual(len(result), 2)
        self.assertGreaterEqual(sum(item.end - item.start for item in result), 60)

    def test_duration_label_uses_minutes(self):
        self.assertEqual(format_duration_label(60), "1分")
        self.assertEqual(format_duration_label(90), "1.5分")

    def test_greeting_is_added_before_selected_segments(self):
        transcript = [
            TranscriptSegment(2, 4, "こんばんは、配信を始めます"),
            TranscriptSegment(4, 10, "今日はゲームをします"),
            TranscriptSegment(30, 50, "最初の見どころ"),
        ]
        segments = [Highlight(30, 50, "見どころ", "理由")]
        result = ensure_greeting_segment(transcript, segments, duration=60)
        self.assertEqual(result[0].title, "冒頭の挨拶")
        self.assertLess(result[0].start, result[1].start)

    def test_file_pipeline_uses_local_video_without_saving_full_path(self):
        from unittest.mock import patch

        transcript = [TranscriptSegment(0, 20, "ローカル動画の文字起こし")]
        highlights = [Highlight(0, 20, "見どころ", "理由", 90)]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "元動画.mp4"
            source.write_bytes(b"test")
            with (
                patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"),
                patch("core.probe_video_duration", return_value=120.0),
                patch("core.transcribe_with_whisper", return_value=transcript),
                patch("core.find_highlights_with_llm", return_value=highlights),
                patch("core.cut_vertical_clip") as cut_clip,
            ):
                result = run_file_pipeline(
                    video_file=source,
                    api_key="test-key",
                    output_root=root / "outputs",
                    clip_count=1,
                )

            report = json.loads((Path(result["output_dir"]) / "見どころ一覧.json").read_text(encoding="utf-8"))
            self.assertEqual(report["source_type"], "local_file")
            self.assertEqual(report["source_file_name"], "元動画.mp4")
            self.assertNotIn("source_path", report)
            cut_clip.assert_called_once()

    def test_file_pipeline_rejects_missing_video(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(AppError):
                run_file_pipeline(
                    video_file=Path(name) / "missing.mp4",
                    api_key="test-key",
                    output_root=Path(name) / "outputs",
                )

    def test_cancellable_command_stops_running_process(self):
        cancel_event = threading.Event()
        timer = threading.Timer(0.2, cancel_event.set)
        started = time.monotonic()
        timer.start()
        try:
            with self.assertRaises(AppCancelled):
                _run_cancellable_command(
                    [sys.executable, "-c", "import time; time.sleep(10)"],
                    cancel_event.is_set,
                )
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 3)

    def test_file_pipeline_removes_partial_output_when_cancelled(self):
        from unittest.mock import patch

        transcript = [TranscriptSegment(0, 20, "ローカル動画の文字起こし")]
        highlights = [Highlight(0, 20, "見どころ", "理由", 90)]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "元動画.mp4"
            source.write_bytes(b"test")
            output_root = root / "outputs"
            with (
                patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"),
                patch("core.probe_video_duration", return_value=120.0),
                patch("core.transcribe_with_whisper", return_value=transcript),
                patch("core.find_highlights_with_llm", return_value=highlights),
                patch("core.cut_vertical_clip", side_effect=AppCancelled("cancelled")),
            ):
                with self.assertRaises(AppCancelled):
                    run_file_pipeline(
                        video_file=source,
                        api_key="test-key",
                        output_root=output_root,
                        clip_count=1,
                        cancel_check=lambda: False,
                    )
            self.assertEqual(list(output_root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
