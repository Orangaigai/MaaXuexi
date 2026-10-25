"""Single-player challenge tier and length-cap preferences; independent of other settings."""
import json
import math
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

CHALLENGE_PREFERENCES_PATH = Path('config/challenge-preferences.json')
CHALLENGE_MINIMUM_FLOOR = 3
CHALLENGE_LIMIT_MIN = 20
CHALLENGE_LIMIT_MAX = 100
CHALLENGE_LIMIT_DEFAULT = 50
_lock = threading.RLock()

# Width is the normal sigma used for the per-round random plan.
CHALLENGE_PROFILES = {
    'beginner': dict(label='入门', center=3, sigma=2.5, recommend=True),
    'practiced': dict(label='熟练', center=6, sigma=6),
    'knowledgeable': dict(label='博学', center=10, sigma=7),
    'adept': dict(label='达人', center=15, sigma=8),
    'master': dict(label='大师', center=20, sigma=10),
}
ChallengeProfile = Literal['beginner', 'practiced', 'knowledgeable', 'adept', 'master']


class ChallengePreferences(BaseModel):
    profile: ChallengeProfile = 'beginner'
    limit: StrictInt = Field(default=CHALLENGE_LIMIT_DEFAULT, ge=CHALLENGE_LIMIT_MIN, le=CHALLENGE_LIMIT_MAX)
    model_config = ConfigDict(extra='forbid')


def normal_cdf(value):
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def challenge_profile_params(profile):
    try:
        return CHALLENGE_PROFILES[profile]
    except KeyError:
        raise ValueError(f'未知单人挑战档位：{profile}') from None


def challenge_plan_minimum(minimum):
    """The plan never targets fewer than 3 rounds or the platform scoring rule."""
    return max(CHALLENGE_MINIMUM_FLOOR, int(minimum))


def challenge_length_weights(minimum, profile='beginner', limit=CHALLENGE_LIMIT_DEFAULT):
    """Weights from the normal CDF over each integer count's ±0.5 interval.

    Counts run between the plan minimum and the adjustable cap. Mass beyond
    the bounds is dropped by the normalization, never clipped onto the end
    counts, so a center near the floor still spreads across low counts.
    """
    params = challenge_profile_params(profile)
    lower = challenge_plan_minimum(minimum)
    counts = list(range(lower, max(lower, int(limit)) + 1))
    center, sigma = params['center'], params['sigma']
    # erfc retains small upper-tail intervals that 1-CDF would round to zero.
    def interval(count):
        left,right=(count-.5-center)/sigma,(count+.5-center)/sigma
        if left>=0:return .5*(math.erfc(left/math.sqrt(2))-math.erfc(right/math.sqrt(2)))
        return normal_cdf(right)-normal_cdf(left)
    weights = [interval(count) for count in counts]
    total = sum(weights)
    return counts, [weight/total for weight in weights]


def challenge_plan_mean(minimum, profile='beginner', limit=CHALLENGE_LIMIT_DEFAULT):
    counts, weights = challenge_length_weights(minimum, profile, limit)
    return sum(count*weight for count, weight in zip(counts, weights))


def challenge_profile_summary(limit=None, minimum=CHALLENGE_MINIMUM_FLOOR):
    """Display rows per tier: planned means for the current cap, not guarantees."""
    limit = CHALLENGE_LIMIT_DEFAULT if limit is None else int(limit)
    rows = []
    for name, params in CHALLENGE_PROFILES.items():
        counts, weights = challenge_length_weights(minimum, name, limit)
        rows.append(dict(name=name, label=params['label'], center=params['center'], sigma=params['sigma'],
                         recommend=params.get('recommend', False),
                         mean=sum(count*weight for count, weight in zip(counts, weights)),
                         sixteen_or_more=sum(weight for count, weight in zip(counts, weights) if count >= 16)))
    return rows


def read_challenge_preferences(path=None):
    with _lock:
        try:
            value = (path or CHALLENGE_PREFERENCES_PATH).read_text('utf-8-sig')
            return ChallengePreferences.model_validate_json(value).model_dump()
        except (OSError, ValueError, TypeError):
            return ChallengePreferences().model_dump()


def save_challenge_preferences(value):
    value = ChallengePreferences.model_validate(value).model_dump()
    with _lock:
        CHALLENGE_PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = CHALLENGE_PREFERENCES_PATH.with_suffix('.tmp')
        try:
            temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            temp.replace(CHALLENGE_PREFERENCES_PATH)
        finally:
            temp.unlink(missing_ok=True)
    return value
