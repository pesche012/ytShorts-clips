from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


CANDIDATE_SCHEMA_VERSION = 1


@dataclass
class ScoreDetail:
    score: float
    reason: str
    label: str = ""


@dataclass
class ClipCandidate:
    candidate_id: str
    start: float
    end: float
    title: str
    score: float
    score_reason: str
    transcript: str
    source_path: str
    scoring_profile_id: str = "youtube_shorts_v1"
    criteria: dict[str, ScoreDetail] = field(default_factory=dict)
    decision: str = "pending"
    encoded_file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClipCandidate":
        criteria = {
            str(key): ScoreDetail(**value)
            for key, value in (data.get("criteria") or {}).items()
            if isinstance(value, dict)
        }
        values = dict(data)
        values["criteria"] = criteria
        return cls(**values)


@dataclass
class CandidateProject:
    project_id: str
    created_at: str
    source_path: str
    source_file_name: str
    video_title: str
    video_duration: float
    transcript_source: str
    llm_provider: str
    llm_model: str
    scoring_profile_id: str
    candidates: list[ClipCandidate]
    schema_version: int = CANDIDATE_SCHEMA_VERSION
    selection_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidateProject":
        version = int(data.get("schema_version", 1))
        if version > CANDIDATE_SCHEMA_VERSION:
            raise ValueError("この候補ファイルは新しいバージョンのアプリで作成されています。")
        values = dict(data)
        values["schema_version"] = version
        values["candidates"] = [
            ClipCandidate.from_dict(item)
            for item in data.get("candidates", [])
            if isinstance(item, dict)
        ]
        values.setdefault("selection_history", [])
        return cls(**values)


def save_candidate_project(path: Path, project: CandidateProject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(project.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_candidate_project(path: Path | str) -> CandidateProject:
    candidate_path = Path(path)
    try:
        data = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"切り抜き候補ファイルを読み込めませんでした。\n{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("切り抜き候補ファイルの形式が正しくありません。")
    try:
        return CandidateProject.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"切り抜き候補ファイルの内容が正しくありません。\n{exc}") from exc
