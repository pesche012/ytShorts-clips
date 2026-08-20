import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from candidate_models import (
    CandidateProject,
    ClipCandidate,
    ScoreDetail,
    load_candidate_project,
    save_candidate_project,
)
from core import (
    TranscriptSegment,
    analyze_file_candidates,
    delete_candidate_previews,
    encode_selected_candidates,
    normalize_clip_candidates,
)
from scoring import YOUTUBE_SHORTS_PROFILE


def make_candidate(candidate_id: str, start: float, score: float) -> ClipCandidate:
    return ClipCandidate(
        candidate_id=candidate_id,
        start=start,
        end=start + 20,
        title=f"候補 {candidate_id}",
        score=score,
        score_reason="総合理由",
        transcript="発言内容",
        source_path="",
        criteria={
            item.key: ScoreDetail(score, "項目理由", item.label)
            for item in YOUTUBE_SHORTS_PROFILE.criteria
        },
    )


class CandidateTests(unittest.TestCase):
    def test_normalize_candidates_calculates_profile_score_and_excerpt(self):
        criteria = {
            item.key: {"score": 80, "reason": f"{item.label}の理由"}
            for item in YOUTUBE_SHORTS_PROFILE.criteria
        }
        criteria["hook"]["score"] = 100
        data = {
            "highlights": [
                {
                    "start": 10,
                    "end": 35,
                    "title": "強い冒頭",
                    "score_reason": "冒頭の引きが特に強い",
                    "criteria": criteria,
                }
            ]
        }
        transcript = [
            TranscriptSegment(0, 8, "範囲外"),
            TranscriptSegment(12, 20, "驚きの発言"),
            TranscriptSegment(20, 30, "大きなリアクション"),
        ]
        result = normalize_clip_candidates(
            data,
            duration=120,
            transcript=transcript,
            source_path="video.mp4",
            clip_count=1,
        )
        expected = YOUTUBE_SHORTS_PROFILE.calculate_total(
            {item.key: (100 if item.key == "hook" else 80) for item in YOUTUBE_SHORTS_PROFILE.criteria}
        )
        self.assertEqual(result[0].score, expected)
        self.assertIn("驚きの発言", result[0].transcript)
        self.assertNotIn("範囲外", result[0].transcript)
        self.assertEqual(result[0].criteria["hook"].label, "冒頭の引き")

    def test_analysis_saves_candidates_without_encoding(self):
        transcript = [TranscriptSegment(0, 20, "ローカル動画の文字起こし")]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "元動画.mp4"
            source.write_bytes(b"test")
            candidate = make_candidate("candidate-001", 0, 92)
            candidate.source_path = str(source.resolve())
            with (
                patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"),
                patch("core.probe_video_duration", return_value=120.0),
                patch("core.transcribe_with_whisper", return_value=transcript),
                patch("core.find_clip_candidates_with_llm", return_value=[candidate]),
                patch("core.cut_vertical_clip") as cut_clip,
            ):
                result = analyze_file_candidates(
                    video_file=source,
                    api_key="test-key",
                    output_root=root / "outputs",
                    clip_count=1,
                )
            project = load_candidate_project(Path(result["candidate_file"]))
            self.assertEqual(project.source_path, str(source.resolve()))
            self.assertEqual(project.candidates[0].score, 92)
            self.assertEqual(list(Path(result["output_dir"]).glob("*.mp4")), [])
            cut_clip.assert_not_called()

    def test_encode_only_selected_candidates(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source.mp4"
            source.write_bytes(b"test")
            first = make_candidate("candidate-001", 0, 92)
            second = make_candidate("candidate-002", 30, 75)
            for item in (first, second):
                item.source_path = str(source)
            project = CandidateProject(
                project_id="project-1",
                created_at="2026-08-20T12:00:00",
                source_path=str(source),
                source_file_name=source.name,
                video_title="source",
                video_duration=120,
                transcript_source="Whisper（動画ファイル）",
                llm_provider="openrouter",
                llm_model="openrouter/auto",
                scoring_profile_id="youtube_shorts_v1",
                candidates=[first, second],
            )
            candidate_file = root / "切り抜き候補.json"
            save_candidate_project(candidate_file, project)
            with (
                patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"),
                patch("core.cut_vertical_clip") as cut_clip,
            ):
                result = encode_selected_candidates(
                    candidate_file,
                    ["candidate-002"],
                )
            self.assertEqual(result["clip_count"], 1)
            cut_clip.assert_called_once()
            self.assertEqual(cut_clip.call_args.args[3], 30)
            updated = load_candidate_project(candidate_file)
            self.assertEqual(updated.candidates[0].decision, "rejected")
            self.assertEqual(updated.candidates[1].decision, "adopted")

    def test_delete_previews_keeps_candidates_and_completed_videos(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            candidate = make_candidate("candidate-001", 0, 90)
            candidate.source_path = str(source)
            project = CandidateProject(
                project_id="project-1",
                created_at="2026-08-20T12:00:00",
                source_path=str(source),
                source_file_name=source.name,
                video_title="source",
                video_duration=120,
                transcript_source="Whisper（動画ファイル）",
                llm_provider="openrouter",
                llm_model="openrouter/auto",
                scoring_profile_id="youtube_shorts_v1",
                candidates=[candidate],
            )
            candidate_file = root / "切り抜き候補.json"
            save_candidate_project(candidate_file, project)
            preview_dir = root / ".previews"
            preview_dir.mkdir()
            preview_file = preview_dir / "candidate-001.mp4"
            preview_file.write_bytes(b"preview")
            completed = root / "選択Shorts_20260820" / "完成.mp4"
            completed.parent.mkdir()
            completed.write_bytes(b"completed")

            count, total_bytes = delete_candidate_previews(candidate_file)

            self.assertEqual(count, 1)
            self.assertEqual(total_bytes, len(b"preview"))
            self.assertFalse(preview_dir.exists())
            self.assertTrue(candidate_file.exists())
            self.assertTrue(completed.exists())


if __name__ == "__main__":
    unittest.main()
