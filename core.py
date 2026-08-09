from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable


ProgressCallback = Callable[[str, float | None], None]


class AppError(RuntimeError):
    """An error that can be shown to the user without a traceback."""


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class Highlight:
    start: float
    end: float
    title: str
    reason: str
    score: float = 0.0


def _notify(callback: ProgressCallback | None, message: str, progress: float | None = None) -> None:
    if callback:
        callback(message, progress)


def safe_filename(value: str, max_length: int = 70) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return (value[:max_length].rstrip(" ._") or "youtube_video")


def seconds_to_timestamp(value: float) -> str:
    value = max(0.0, float(value))
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    seconds = value % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def format_duration_label(seconds: int) -> str:
    return f"{seconds / 60:g}分"


def parse_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    parts = text.split(":")
    if len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"時刻を解釈できません: {value}")


def parse_json3(path: Path) -> list[TranscriptSegment]:
    data = json.loads(path.read_text(encoding="utf-8"))
    result: list[TranscriptSegment] = []
    for event in data.get("events", []):
        start_ms = event.get("tStartMs")
        if start_ms is None:
            continue
        pieces = event.get("segs") or []
        text = "".join(str(piece.get("utf8", "")) for piece in pieces)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or text == "\n":
            continue
        start = float(start_ms) / 1000
        duration = max(0.1, float(event.get("dDurationMs", 2000)) / 1000)
        result.append(TranscriptSegment(start, start + duration, text))
    return _deduplicate_segments(result)


_VTT_TIMING = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s+-->\s+"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})"
)


def parse_vtt(path: Path) -> list[TranscriptSegment]:
    content = path.read_text(encoding="utf-8-sig", errors="replace")
    blocks = re.split(r"\r?\n\s*\r?\n", content)
    result: list[TranscriptSegment] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if _VTT_TIMING.search(line)), None)
        if timing_index is None:
            continue
        match = _VTT_TIMING.search(lines[timing_index])
        if not match:
            continue
        text = " ".join(lines[timing_index + 1 :])
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            result.append(
                TranscriptSegment(
                    parse_timestamp(match.group("start")),
                    parse_timestamp(match.group("end")),
                    text,
                )
            )
    return _deduplicate_segments(result)


def _deduplicate_segments(segments: Iterable[TranscriptSegment]) -> list[TranscriptSegment]:
    result: list[TranscriptSegment] = []
    for segment in sorted(segments, key=lambda item: (item.start, item.end)):
        text = segment.text.strip()
        if not text:
            continue
        if result and text == result[-1].text and segment.start <= result[-1].end + 0.25:
            result[-1].end = max(result[-1].end, segment.end)
            continue
        result.append(TranscriptSegment(segment.start, max(segment.start + 0.1, segment.end), text))
    return result


def _youtube_options(ffmpeg_path: str, cookie_browser: str | None = None) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ffmpeg_location": ffmpeg_path,
    }
    deno_path = os.environ.get("YTSM_DENO_EXE") or shutil.which("deno")
    if not deno_path and os.name == "nt":
        local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
        candidates = list((local_app_data / "Microsoft" / "WinGet" / "Packages").glob("DenoLand.Deno_*/deno.exe"))
        user_profile = Path(os.environ.get("USERPROFILE", ""))
        candidates.append(user_profile / ".deno" / "bin" / "deno.exe")
        deno_path = next((str(path) for path in candidates if path.is_file()), None)
    if deno_path:
        options["js_runtimes"] = {"deno": {"path": deno_path}}
    if cookie_browser:
        options["cookiesfrombrowser"] = (cookie_browser,)
    return options


def fetch_metadata(url: str, ffmpeg_path: str, cookie_browser: str | None = None) -> dict[str, Any]:
    try:
        import yt_dlp
    except ImportError as exc:
        raise AppError("yt-dlpが見つかりません。setup.batをもう一度実行してください。") from exc

    options = _youtube_options(ffmpeg_path, cookie_browser)
    options["skip_download"] = True
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise AppError(f"YouTube動画の情報を取得できませんでした。\n{exc}") from exc
    if not info or info.get("_type") == "playlist":
        raise AppError("動画URLを1本だけ指定してください。")
    return info


def _choose_caption(info: dict[str, Any]) -> tuple[str, str] | None:
    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    preferred = ("ja", "ja-orig", "ja-JP", "en", "en-orig", "en-US")
    for language in preferred:
        if language in manual:
            return language, "manual"
    for language in preferred:
        if language in automatic:
            return language, "automatic"
    if manual:
        return next(iter(manual)), "manual"
    if automatic:
        return next(iter(automatic)), "automatic"
    return None


def download_caption(
    url: str,
    info: dict[str, Any],
    work_dir: Path,
    ffmpeg_path: str,
    cookie_browser: str | None = None,
) -> tuple[list[TranscriptSegment], str] | None:
    choice = _choose_caption(info)
    if not choice:
        return None
    language, kind = choice
    import yt_dlp

    options = _youtube_options(ffmpeg_path, cookie_browser)
    options.update(
        {
            "skip_download": True,
            "outtmpl": str(work_dir / "captions.%(ext)s"),
            "subtitleslangs": [language],
            "subtitlesformat": "json3/vtt/best",
            "writesubtitles": kind == "manual",
            "writeautomaticsub": kind == "automatic",
        }
    )
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)
    except Exception:
        return None

    files = list(work_dir.glob("captions*.json3")) + list(work_dir.glob("captions*.vtt"))
    if not files:
        return None
    caption_path = files[0]
    segments = parse_json3(caption_path) if caption_path.suffix == ".json3" else parse_vtt(caption_path)
    if not segments:
        return None
    label = "YouTube字幕" if kind == "manual" else "YouTube自動字幕"
    return segments, f"{label}（{language}）"


def download_video(
    url: str,
    work_dir: Path,
    ffmpeg_path: str,
    cookie_browser: str | None,
    callback: ProgressCallback | None,
    max_duration_seconds: float | None = None,
) -> Path:
    import yt_dlp

    options = _youtube_options(ffmpeg_path, cookie_browser)

    def hook(status: dict[str, Any]) -> None:
        if status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            downloaded = status.get("downloaded_bytes", 0)
            ratio = (downloaded / total) if total else None
            percent = f"{ratio * 100:.0f}%" if ratio is not None else "処理中"
            _notify(callback, f"動画を取得しています… {percent}", 0.08 + (ratio or 0) * 0.17)

    options.update(
        {
            "outtmpl": str(work_dir / "source.%(ext)s"),
            "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b[height<=1080]/best",
            "merge_output_format": "mp4",
            "progress_hooks": [hook],
        }
    )
    if max_duration_seconds is not None:
        try:
            from yt_dlp.utils import download_range_func

            options["download_ranges"] = download_range_func(
                None, [(0, float(max_duration_seconds))]
            )
            options["force_keyframes_at_cuts"] = True
            options["live_from_start"] = False
        except ImportError as exc:
            raise AppError("生配信の時間指定に対応したyt-dlpが必要です。setup.batを実行してください。") from exc
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        raise AppError(f"動画をダウンロードできませんでした。\n{exc}") from exc

    candidates = [
        path
        for path in work_dir.glob("source.*")
        if path.suffix.lower() not in {".part", ".ytdl", ".json", ".vtt", ".json3"}
    ]
    if not candidates:
        raise AppError("ダウンロードした動画ファイルが見つかりません。")
    return max(candidates, key=lambda path: path.stat().st_size)


def transcribe_with_whisper(
    video_path: Path,
    model_name: str,
    callback: ProgressCallback | None = None,
) -> list[TranscriptSegment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise AppError("Whisperが見つかりません。setup.batをもう一度実行してください。") from exc

    _notify(callback, f"Whisper（{model_name}）を準備しています…", 0.28)
    try:
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        stream, _ = model.transcribe(
            str(video_path),
            beam_size=5,
            vad_filter=True,
            word_timestamps=False,
        )
        result: list[TranscriptSegment] = []
        for index, segment in enumerate(stream, start=1):
            text = segment.text.strip()
            if text:
                result.append(TranscriptSegment(float(segment.start), float(segment.end), text))
            if index % 10 == 0:
                _notify(callback, f"Whisperで文字起こし中… {seconds_to_timestamp(segment.end)}", 0.32)
    except Exception as exc:
        raise AppError(f"Whisperの文字起こしに失敗しました。\n{exc}") from exc
    if not result:
        raise AppError("音声から文字を検出できませんでした。")
    return result


def transcript_as_text(segments: Iterable[TranscriptSegment]) -> str:
    return "\n".join(
        f"[{seconds_to_timestamp(item.start)} - {seconds_to_timestamp(item.end)}] {item.text}"
        for item in segments
    )


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def normalize_highlights(data: Any, duration: float, clip_count: int = 7) -> list[Highlight]:
    if not 1 <= clip_count <= 20:
        raise ValueError("作成本数は1〜20本で指定してください")
    items = data.get("highlights") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("highlights配列がありません")
    result: list[Highlight] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        try:
            start = max(0.0, parse_timestamp(item.get("start", 0)))
            end = min(duration, parse_timestamp(item.get("end", start + 30)))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        if end - start > 60:
            end = start + 60
        if end - start < 10:
            center = (start + end) / 2
            start = max(0.0, center - 7.5)
            end = min(duration, start + 15)
            start = max(0.0, end - 15)
        title = str(item.get("title") or f"見どころ {index + 1}").strip()
        reason = str(item.get("reason") or "注目度の高い場面").strip()
        try:
            score = float(item.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        result.append(Highlight(start, end, title[:60], reason[:200], score))
        if len(result) == clip_count:
            break
    if len(result) != clip_count:
        raise ValueError(
            f"見どころを{clip_count}件要求しましたが、{len(result)}件しか返されませんでした"
        )
    return result


def build_highlight_prompts(
    transcript: list[TranscriptSegment],
    duration: float,
    custom_instructions: str = "",
    clip_count: int = 7,
) -> tuple[str, str]:
    if not 1 <= clip_count <= 20:
        raise ValueError("作成本数は1〜20本で指定してください")
    transcript_text = transcript_as_text(transcript)
    instructions = custom_instructions.strip() or "指定なし。標準方針で選定する"
    system_prompt = (
        "あなたはYouTube Shortsの熟練編集者です。文字起こしから視聴維持率が高くなる場面を選びます。"
        "ユーザー指定は内容・雰囲気・長さの希望として優先しますが、"
        "指定された件数・有効なタイムスタンプ・JSON形式の条件は必ず守ってください。"
        "必ず有効なjsonだけを返してください。"
    )
    user_prompt = f"""
動画の長さは {duration:.1f} 秒です。以下のタイムスタンプ付き文字起こしから、最も魅力的な見どころを必ず{clip_count}個選んでください。

今回の編集方針（ユーザー指定）:
{instructions}

条件:
- 各クリップは原則15〜60秒。話の途中から始めず、オチや結論の直後で終える
- 同じ内容・時間帯を重複させない
- 冒頭で興味を引き、単体でも意味が通じる場面を優先
- startとendは動画先頭からの秒数（数値）
- scoreはShortsとしての期待度を0〜100で評価
- titleは日本語で短く、reasonも日本語
- 結果は次のjson形式のみ:
{{"highlights":[{{"start":12.3,"end":48.0,"title":"短いタイトル","reason":"選定理由","score":92}}]}}

文字起こし:
{transcript_text}
""".strip()
    return system_prompt, user_prompt


def find_highlights_with_llm(
    transcript: list[TranscriptSegment],
    duration: float,
    api_key: str,
    provider_id: str = "openrouter",
    model: str = "openrouter/auto",
    custom_instructions: str = "",
    clip_count: int = 7,
    callback: ProgressCallback | None = None,
) -> list[Highlight]:
    try:
        from llm_providers import LLMProviderError, build_highlights_schema, get_provider
    except ImportError as exc:
        raise AppError("LLM接続モジュールが見つかりません。アプリを再インストールしてください。") from exc

    try:
        provider = get_provider(provider_id)
    except LLMProviderError as exc:
        raise AppError(str(exc)) from exc

    system_prompt, user_prompt = build_highlight_prompts(
        transcript, duration, custom_instructions, clip_count
    )

    last_error: Exception | None = None
    for attempt in range(1, 4):
        _notify(callback, f"{provider.display_name} / {model} が見どころを選んでいます…（{attempt}/3）", 0.48)
        try:
            content = provider.complete_json(
                api_key=api_key,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout=300,
                json_schema=build_highlights_schema(clip_count, clip_count),
            )
            return normalize_highlights(_extract_json(content), duration, clip_count)
        except LLMProviderError as exc:
            last_error = exc
            message = str(exc)
            if any(word in message for word in ("APIキー", "残高", "利用上限", "未対応")):
                raise AppError(message) from exc
        except Exception as exc:
            last_error = exc
        if attempt < 3:
            time.sleep(1.5 * attempt)
    raise AppError(f"LLMから見どころを取得できませんでした。\n{last_error}")


def build_live_edit_prompts(
    transcript: list[TranscriptSegment],
    duration: float,
    target_seconds: int,
    custom_instructions: str = "",
    preserve_ending: bool = True,
    include_greeting: bool = True,
) -> tuple[str, str]:
    transcript_text = transcript_as_text(transcript)
    instructions = custom_instructions.strip() or "指定なし。盛り上がりと分かりやすさを優先する"
    ending_rule = (
        "最後の区間は、締めの発言・結論・挨拶・笑い・余韻が完全に終わるまで含める。"
        "時間を少し超えても途中で切らない"
        if preserve_ending
        else "完成時間を優先し、最後の区間も指定時間内に収める"
    )
    intro_rule = (
        "最初の区間には、元配信にある冒頭の挨拶・自己紹介・開始宣言を含める。"
        "挨拶がない場合も、話の途中ではなく自然な導入から始める"
        if include_greeting
        else "最初から見どころを優先して始める"
    )
    system_prompt = (
        "あなたは生配信の切り抜きを作る熟練動画編集者です。"
        "退屈な間、無言、重複、脱線を除き、見どころだけで自然につながる1本を設計します。"
        "有効なタイムスタンプとJSON形式を必ず守ってください。"
    )
    user_prompt = f"""
元動画は {duration:.1f} 秒です。完成動画を {target_seconds} 秒にするため、採用する区間を時系列順に選んでください。

今回の編集方針（ユーザー指定）:
{instructions}

条件:
- highlightsの合計時間は {target_seconds} 秒以上、{target_seconds + 30} 秒以内にする
- {intro_rule}
- 各区間は原則8〜90秒で、話の途中から始めず結論の直後で終える
- 無言、待ち時間、同じ話、内輪だけのやり取り、不要な脱線を除く
- 1本の動画として導入・展開・結論が自然につながるようにする
- {ending_rule}
- 区間を重複させず、元動画での時系列順に並べる
- startとendは元動画先頭からの秒数（数値）
- titleとreasonは日本語、scoreは重要度を0〜100で評価
- 結果は次のjson形式のみ:
{{"highlights":[{{"start":12.3,"end":48.0,"title":"採用場面","reason":"採用理由","score":92}}]}}

文字起こし:
{transcript_text}
""".strip()
    return system_prompt, user_prompt


def normalize_live_edit_segments(
    data: Any,
    duration: float,
    target_seconds: int,
) -> list[Highlight]:
    items = data.get("highlights") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("highlights配列がありません")
    candidates: list[Highlight] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        try:
            start = max(0.0, parse_timestamp(item.get("start", 0)))
            end = min(duration, parse_timestamp(item.get("end", start)))
        except (TypeError, ValueError):
            continue
        if end - start < 3:
            continue
        try:
            score = float(item.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        candidates.append(
            Highlight(
                start=start,
                end=end,
                title=str(item.get("title") or f"採用場面 {index + 1}")[:60],
                reason=str(item.get("reason") or "見どころ")[:200],
                score=score,
            )
        )
    result: list[Highlight] = []
    total = 0.0
    for item in sorted(candidates, key=lambda value: value.start):
        if result and item.start < result[-1].end:
            item.start = result[-1].end
        if item.end - item.start < 3:
            continue
        result.append(item)
        total += item.end - item.start
        if total >= target_seconds:
            break
    if total < target_seconds:
        raise ValueError(
            f"採用区間が{total:.0f}秒しかなく、指定された{target_seconds}秒に足りません"
        )
    return result


_GREETING_PATTERN = re.compile(
    r"(?:こんにちは|こんばんは|おはよう|どうも[、,!！ ]|よろしくお願いします|"
    r"始めていきましょう|配信を始め|今日もよろしく)",
    flags=re.IGNORECASE,
)


def ensure_greeting_segment(
    transcript: list[TranscriptSegment],
    segments: list[Highlight],
    duration: float,
    include_greeting: bool = True,
) -> list[Highlight]:
    if not include_greeting or not transcript:
        return segments
    search_limit = min(duration * 0.2, 600)
    greeting_index = next(
        (
            index
            for index, item in enumerate(transcript)
            if item.start <= search_limit and _GREETING_PATTERN.search(item.text)
        ),
        None,
    )
    if greeting_index is None:
        return segments
    greeting_source = transcript[greeting_index]
    greeting_start = max(0.0, greeting_source.start - 1.0)
    greeting_end = min(duration, greeting_source.end + 3.0)
    for item in transcript[greeting_index + 1 :]:
        if item.start > greeting_source.end + 12:
            break
        greeting_end = min(duration, item.end)
        if greeting_end >= greeting_source.end + 8:
            break

    result = list(segments)
    for item in result:
        if item.start <= greeting_end and item.end >= greeting_start:
            item.start = min(item.start, greeting_start)
            item.end = max(item.end, greeting_end)
            return sorted(result, key=lambda value: value.start)
    result.append(
        Highlight(
            start=greeting_start,
            end=greeting_end,
            title="冒頭の挨拶",
            reason="動画が唐突に始まらないよう、元配信の挨拶を含める",
            score=100,
        )
    )
    return sorted(result, key=lambda value: value.start)


def find_live_edit_segments_with_llm(
    transcript: list[TranscriptSegment],
    duration: float,
    target_seconds: int,
    api_key: str,
    provider_id: str,
    model: str,
    custom_instructions: str = "",
    preserve_ending: bool = True,
    include_greeting: bool = True,
    callback: ProgressCallback | None = None,
) -> list[Highlight]:
    try:
        from llm_providers import LLMProviderError, build_highlights_schema, get_provider
    except ImportError as exc:
        raise AppError("LLM接続モジュールが見つかりません。アプリを再インストールしてください。") from exc
    provider = get_provider(provider_id)
    system_prompt, user_prompt = build_live_edit_prompts(
        transcript,
        duration,
        target_seconds,
        custom_instructions,
        preserve_ending,
        include_greeting,
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        _notify(callback, f"{provider.display_name} / {model} が不要部分を除いています…（{attempt}/3）", 0.48)
        try:
            content = provider.complete_json(
                api_key=api_key,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout=300,
                json_schema=build_highlights_schema(1, 30),
            )
            segments = normalize_live_edit_segments(
                _extract_json(content), duration, target_seconds
            )
            return ensure_greeting_segment(
                transcript, segments, duration, include_greeting
            )
        except LLMProviderError as exc:
            last_error = exc
            message = str(exc)
            if any(word in message for word in ("APIキー", "残高", "利用上限", "未対応")):
                raise AppError(message) from exc
        except Exception as exc:
            last_error = exc
        if attempt < 3:
            time.sleep(1.5 * attempt)
    raise AppError(f"LLMから生配信の編集区間を取得できませんでした。\n{last_error}")


def cut_vertical_clip(
    ffmpeg_path: str,
    video_path: Path,
    output_path: Path,
    start: float,
    end: float,
    resolution: str,
) -> None:
    width, height = (1080, 1920) if resolution == "1080p" else (720, 1280)
    duration = max(0.1, end - start)
    video_filter = (
        f"[0:v]split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=20:2[bg];"
        f"[fgsrc]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
    )
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{duration:.3f}",
        "-filter_complex",
        video_filter,
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(command, capture_output=True, text=True, creationflags=creationflags)
    if completed.returncode != 0:
        message = (completed.stderr or "FFmpegの処理に失敗しました").strip()
        raise AppError(f"動画の切り抜きに失敗しました。\n{message[-800:]}")


def cut_standard_clip(
    ffmpeg_path: str,
    video_path: Path,
    output_path: Path,
    start: float,
    end: float,
    resolution: str,
) -> None:
    max_height = 1080 if resolution == "1080p" else 720
    duration = max(0.1, end - start)
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{duration:.3f}",
        "-vf",
        f"scale=-2:min(ih\\,{max_height}),format=yuv420p",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(command, capture_output=True, text=True, creationflags=creationflags)
    if completed.returncode != 0:
        message = (completed.stderr or "FFmpegの処理に失敗しました").strip()
        raise AppError(f"通常動画の切り抜きに失敗しました。\n{message[-800:]}")


def run_pipeline(
    url: str,
    api_key: str,
    output_root: Path,
    whisper_model: str = "small",
    llm_provider: str = "openrouter",
    llm_model: str = "openrouter/auto",
    highlight_prompt: str = "",
    clip_count: int = 7,
    resolution: str = "720p",
    cookie_browser: str | None = None,
    callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if not re.match(r"^https?://", url.strip(), flags=re.IGNORECASE):
        raise AppError("YouTubeのURLを正しく入力してください。")
    if not 1 <= clip_count <= 20:
        raise AppError("作成本数は1〜20本で指定してください。")
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        import imageio_ffmpeg

        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise AppError("動画処理エンジンを準備できません。setup.batをもう一度実行してください。") from exc

    with tempfile.TemporaryDirectory(prefix=".youtube-short-", dir=output_root) as temp_name:
        work_dir = Path(temp_name)
        _notify(callback, "YouTube動画の情報を確認しています…", 0.02)
        info = fetch_metadata(url.strip(), ffmpeg_path, cookie_browser)
        title = str(info.get("title") or info.get("id") or "youtube_video")
        duration = float(info.get("duration") or 0)
        if duration <= 0:
            raise AppError("動画の長さを取得できませんでした。")

        _notify(callback, "YouTube字幕を確認しています…", 0.05)
        caption = download_caption(url.strip(), info, work_dir, ffmpeg_path, cookie_browser)
        if caption:
            transcript, transcript_source = caption
            _notify(callback, f"{transcript_source}を取得しました。", 0.24)
        else:
            transcript = []
            transcript_source = "Whisper"
            _notify(callback, "字幕がないためWhisperを使用します。", 0.24)

        video_path = download_video(url.strip(), work_dir, ffmpeg_path, cookie_browser, callback)
        if not transcript:
            transcript = transcribe_with_whisper(video_path, whisper_model, callback)

        highlights = find_highlights_with_llm(
            transcript=transcript,
            duration=duration,
            api_key=api_key,
            provider_id=llm_provider,
            model=llm_model,
            custom_instructions=highlight_prompt,
            clip_count=clip_count,
            callback=callback,
        )

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_dir = output_root / f"{safe_filename(title)}_{stamp}"
        final_dir.mkdir(parents=True, exist_ok=False)
        _notify(callback, f"{clip_count}本のShortsを書き出します…", 0.56)
        for index, item in enumerate(highlights, start=1):
            filename = f"{index:02d}_{safe_filename(item.title, 45)}.mp4"
            output_path = final_dir / filename
            _notify(
                callback,
                f"動画 {index}/{clip_count} を作成中: {item.title}",
                0.55 + (index / clip_count) * 0.42,
            )
            cut_vertical_clip(
                ffmpeg_path, video_path, output_path, item.start, item.end, resolution
            )

        report = {
            "source_url": url.strip(),
            "video_title": title,
            "video_duration": duration,
            "transcript_source": transcript_source,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "clip_count": clip_count,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "highlights": [asdict(item) for item in highlights],
        }
        (final_dir / "見どころ一覧.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (final_dir / "文字起こし.txt").write_text(transcript_as_text(transcript), encoding="utf-8")
        _notify(callback, "完了しました。", 1.0)
        return {"output_dir": str(final_dir), **report}


def probe_video_duration(ffmpeg_path: str, video_path: Path) -> float:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(
        [ffmpeg_path, "-hide_banner", "-i", str(video_path)],
        capture_output=True,
        text=True,
        creationflags=creationflags,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", completed.stderr or "")
    if not match:
        return 0.0
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def create_live_montage(
    ffmpeg_path: str,
    video_path: Path,
    output_path: Path,
    segments: list[Highlight],
    target_seconds: int,
    resolution: str,
    work_dir: Path,
    preserve_ending: bool = True,
    callback: ProgressCallback | None = None,
) -> None:
    parts_dir = work_dir / "live_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    part_paths: list[Path] = []
    for index, item in enumerate(segments, start=1):
        part_path = parts_dir / f"part_{index:03d}.mp4"
        _notify(
            callback,
            f"採用場面 {index}/{len(segments)} を切り出しています…",
            0.56 + (index / len(segments)) * 0.28,
        )
        cut_standard_clip(
            ffmpeg_path,
            video_path,
            part_path,
            item.start,
            item.end,
            resolution,
        )
        part_paths.append(part_path)

    concat_path = parts_dir / "concat.txt"
    concat_path.write_text(
        "\n".join(f"file '{path.as_posix()}'" for path in part_paths) + "\n",
        encoding="utf-8",
    )
    _notify(
        callback,
        (
            f"不要部分を除き、締めを最後まで含めた約{format_duration_label(target_seconds)}の動画へまとめています…"
            if preserve_ending
            else f"不要部分を除き、{format_duration_label(target_seconds)}の動画へまとめています…"
        ),
        0.88,
    )
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
    ]
    if not preserve_ending:
        command.extend(["-t", str(target_seconds)])
    command.extend([
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(output_path),
    ])
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(command, capture_output=True, text=True, creationflags=creationflags)
    if completed.returncode != 0:
        message = (completed.stderr or "FFmpegの結合処理に失敗しました").strip()
        raise AppError(f"生配信の切り抜き動画を結合できませんでした。\n{message[-800:]}")


def run_live_edit_pipeline(
    url: str,
    api_key: str,
    output_root: Path,
    target_seconds: int,
    live_capture_minutes: int = 30,
    whisper_model: str = "small",
    llm_provider: str = "openrouter",
    llm_model: str = "openrouter/auto",
    edit_prompt: str = "",
    preserve_ending: bool = True,
    include_greeting: bool = True,
    resolution: str = "720p",
    cookie_browser: str | None = None,
    callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if not re.match(r"^https?://", url.strip(), flags=re.IGNORECASE):
        raise AppError("YouTubeの生配信URLを正しく入力してください。")
    if not 30 <= target_seconds <= 1800:
        raise AppError("完成動画の長さは0.5〜30分で指定してください。")
    if not 1 <= live_capture_minutes <= 360:
        raise AppError("生配信の取得時間は1〜360分で指定してください。")
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        import imageio_ffmpeg

        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise AppError("動画処理エンジンを準備できません。setup.batをもう一度実行してください。") from exc

    with tempfile.TemporaryDirectory(prefix=".youtube-live-edit-", dir=output_root) as temp_name:
        work_dir = Path(temp_name)
        _notify(callback, "生配信の情報を確認しています…", 0.02)
        info = fetch_metadata(url.strip(), ffmpeg_path, cookie_browser)
        title = str(info.get("title") or info.get("id") or "youtube_live")
        live_status = str(info.get("live_status") or "")
        is_live = bool(info.get("is_live")) or live_status == "is_live"

        if is_live:
            capture_seconds = live_capture_minutes * 60
            if target_seconds > capture_seconds:
                raise AppError("完成動画の長さは、生配信の取得時間より短くしてください。")
            _notify(
                callback,
                f"配信中の映像を現在位置から{live_capture_minutes}分取得します…",
                0.05,
            )
            video_path = download_video(
                url.strip(),
                work_dir,
                ffmpeg_path,
                cookie_browser,
                callback,
                max_duration_seconds=capture_seconds,
            )
            transcript_source = "Whisper（配信中の取得映像）"
            transcript = transcribe_with_whisper(video_path, whisper_model, callback)
            duration = probe_video_duration(ffmpeg_path, video_path)
            if duration <= 0:
                duration = max(item.end for item in transcript)
            source_status = "live"
        else:
            duration = float(info.get("duration") or 0)
            if duration <= 0:
                raise AppError("生配信アーカイブの長さを取得できませんでした。")
            _notify(callback, "生配信アーカイブの字幕を確認しています…", 0.05)
            caption = download_caption(url.strip(), info, work_dir, ffmpeg_path, cookie_browser)
            if caption:
                transcript, transcript_source = caption
                _notify(callback, f"{transcript_source}を取得しました。", 0.24)
            else:
                transcript = []
                transcript_source = "Whisper"
                _notify(callback, "字幕がないためWhisperを使用します。", 0.24)
            video_path = download_video(
                url.strip(), work_dir, ffmpeg_path, cookie_browser, callback
            )
            if not transcript:
                transcript = transcribe_with_whisper(video_path, whisper_model, callback)
            source_status = "archive"

        if target_seconds > duration:
            raise AppError(
                f"完成動画の長さ（{format_duration_label(target_seconds)}）は、"
                f"取得した映像（{format_duration_label(int(duration))}）より短くしてください。"
            )
        segments = find_live_edit_segments_with_llm(
            transcript=transcript,
            duration=duration,
            target_seconds=target_seconds,
            api_key=api_key,
            provider_id=llm_provider,
            model=llm_model,
            custom_instructions=edit_prompt,
            preserve_ending=preserve_ending,
            include_greeting=include_greeting,
            callback=callback,
        )

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_dir = output_root / f"{safe_filename(title)}_生配信編集_{stamp}"
        final_dir.mkdir(parents=True, exist_ok=False)
        duration_label = format_duration_label(target_seconds)
        filename_prefix = "生配信切り抜き_約" if preserve_ending else "生配信切り抜き_"
        output_path = final_dir / f"{filename_prefix}{duration_label}.mp4"
        create_live_montage(
            ffmpeg_path=ffmpeg_path,
            video_path=video_path,
            output_path=output_path,
            segments=segments,
            target_seconds=target_seconds,
            resolution=resolution,
            work_dir=work_dir,
            preserve_ending=preserve_ending,
            callback=callback,
        )
        output_duration = probe_video_duration(ffmpeg_path, output_path)
        report = {
            "mode": "live_edit",
            "source_url": url.strip(),
            "video_title": title,
            "source_status": source_status,
            "source_duration": duration,
            "target_duration": target_seconds,
            "target_duration_minutes": target_seconds / 60,
            "output_duration": output_duration,
            "output_duration_minutes": output_duration / 60,
            "preserve_ending": preserve_ending,
            "include_greeting": include_greeting,
            "transcript_source": transcript_source,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "segments": [asdict(item) for item in segments],
        }
        (final_dir / "編集内容.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (final_dir / "文字起こし.txt").write_text(
            transcript_as_text(transcript), encoding="utf-8"
        )
        _notify(callback, "生配信の切り抜き動画が完成しました。", 1.0)
        return {"output_dir": str(final_dir), "output_file": str(output_path), **report}
