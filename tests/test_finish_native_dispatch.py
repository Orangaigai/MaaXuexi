"""Check the installed Maa native API without connecting to Android or an app."""
import json
from pathlib import Path
import unittest
import numpy as np
from maa.controller import CustomController
from test_optimization import worker


class DispatchProbe(CustomController):
    def __init__(self):
        self.events=[]
        super().__init__()

    def connect(self): return True
    def request_uuid(self): return 'offline-finish-dispatch'
    def get_features(self): return 0  # The same feature path as AdbShellInput.
    def start_app(self, intent): return False
    def stop_app(self, intent): return False
    def screencap(self): return np.zeros((1280,720,3),np.uint8)
    def click(self, x, y):
        self.events.append(('tap',x,y))
        return True
    def swipe(self, x1, y1, x2, y2, duration):
        self.events.append(('swipe',x1,y1,x2,y2,duration))
        return True
    def unexpected(self, *args):
        raise AssertionError('Unexpected input in the offline dispatch probe')
    touch_down=touch_move=touch_up=click_key=input_text=key_down=key_up=unexpected


class FinishNativeDispatchTests(unittest.TestCase):
    def test_recovery_press_keeps_real_portrait_coordinates_and_duration(self):
        records=json.loads((Path(__file__).parent/'fixtures/daily-flow-20261016/completion-after-verification.json').read_text('utf-8'))
        value=worker()
        value._daily_completion_expected=True
        value._daily_finish_metrics={}
        value._screen_records=lambda:records
        probe=DispatchProbe()
        self.assertTrue(probe.post_connection().wait().succeeded)
        self.assertTrue(probe.post_screencap().wait().succeeded)
        value.tasker.controller=probe
        self.assertTrue(value._completion_button('Press',records=records))
        self.assertEqual(probe.events,[('swipe',616,76,616,76,120)])
        self.assertEqual(value._daily_finish_metrics['finish_inputs'],[
            dict(action='stationary_press',point=[616,76],duration_ms=120)])


if __name__=='__main__':unittest.main()
