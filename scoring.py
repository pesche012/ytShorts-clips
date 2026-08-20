from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ScoringCriterion:
    key: str
    label: str
    weight: float


@dataclass(frozen=True)
class ScoringProfile:
    profile_id: str
    label: str
    criteria: tuple[ScoringCriterion, ...]

    def calculate_total(self, scores: Mapping[str, float]) -> float:
        weighted = 0.0
        total_weight = 0.0
        for criterion in self.criteria:
            value = max(0.0, min(100.0, float(scores.get(criterion.key, 0.0))))
            weighted += value * criterion.weight
            total_weight += criterion.weight
        return round(weighted / total_weight, 1) if total_weight else 0.0


YOUTUBE_SHORTS_PROFILE = ScoringProfile(
    profile_id="youtube_shorts_v1",
    label="YouTube Shorts",
    criteria=(
        ScoringCriterion("funniness", "面白さ", 1.0),
        ScoringCriterion("excitement", "盛り上がり", 1.1),
        ScoringCriterion("statement_strength", "発言の強さ", 1.0),
        ScoringCriterion("reaction", "リアクション", 0.9),
        ScoringCriterion("clarity", "分かりやすさ", 1.2),
        ScoringCriterion("hook", "冒頭の引き", 1.3),
    ),
)


SCORING_PROFILES: dict[str, ScoringProfile] = {
    YOUTUBE_SHORTS_PROFILE.profile_id: YOUTUBE_SHORTS_PROFILE,
}


def get_scoring_profile(profile_id: str = YOUTUBE_SHORTS_PROFILE.profile_id) -> ScoringProfile:
    try:
        return SCORING_PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"未対応のスコアリング方式です: {profile_id}") from exc


def build_candidate_schema(
    min_items: int,
    max_items: int,
    profile: ScoringProfile = YOUTUBE_SHORTS_PROFILE,
) -> dict[str, Any]:
    criterion_properties = {
        criterion.key: {
            "type": "object",
            "properties": {
                "score": {"type": "number", "minimum": 0, "maximum": 100},
                "reason": {"type": "string"},
            },
            "required": ["score", "reason"],
            "additionalProperties": False,
        }
        for criterion in profile.criteria
    }
    return {
        "name": "scored_clip_candidates",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "highlights": {
                    "type": "array",
                    "minItems": min_items,
                    "maxItems": max_items,
                    "items": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "number"},
                            "end": {"type": "number"},
                            "title": {"type": "string"},
                            "score_reason": {"type": "string"},
                            "criteria": {
                                "type": "object",
                                "properties": criterion_properties,
                                "required": [item.key for item in profile.criteria],
                                "additionalProperties": False,
                            },
                        },
                        "required": ["start", "end", "title", "score_reason", "criteria"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["highlights"],
            "additionalProperties": False,
        },
    }
