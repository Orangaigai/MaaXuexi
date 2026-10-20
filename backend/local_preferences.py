"""Local display and notification preferences; independent of task/model configuration."""
import json
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, StrictBool
from typing import Literal

PREFERENCES_PATH = Path('config/local-preferences.json')
_lock = threading.RLock()


class LocalPreferences(BaseModel):
    window_mode: Literal['background', 'bottom', 'normal', 'topmost'] = 'background'
    show_on_attention: StrictBool = True
    notifications: StrictBool = True
    open_console_on_schedule: StrictBool = False
    model_config = ConfigDict(extra='forbid')


def read_preferences(path=None):
    with _lock:
        try:
            return LocalPreferences.model_validate_json((path or PREFERENCES_PATH).read_text('utf-8-sig')).model_dump()
        except (OSError, ValueError, TypeError):
            return LocalPreferences().model_dump()


def save_preferences(value):
    value = LocalPreferences.model_validate(value).model_dump()
    with _lock:
        PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = PREFERENCES_PATH.with_suffix('.tmp')
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        temp.replace(PREFERENCES_PATH)
    return value


def desktop_notification(notify, **options):
    """The switch affects system notifications only, never reports or task pauses."""
    if not read_preferences()['notifications']:
        return False
    notify(**options)
    return True
