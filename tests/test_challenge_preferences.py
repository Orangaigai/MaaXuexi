"""Independent configuration, strict local API, and non-single-player isolation."""
import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from fastapi.testclient import TestClient
import challenge_preferences as preferences
import webui
import test_fun_challenge as fun_tests

class ChallengePreferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'challenge-preferences.json'
        patched=patch.object(preferences,'CHALLENGE_PREFERENCES_PATH',self.path)
        patched.start();self.addCleanup(patched.stop)
        self.client=TestClient(webui.app);self.addCleanup(self.client.close)

    def test_defaults_have_five_means_and_no_write_on_read(self):
        response=self.client.get('/api/local/challenge-preferences');self.assertEqual(response.status_code,200)
        data=response.json();self.assertEqual((data['profile'],data['limit']),('beginner',50))
        self.assertEqual(len(data['profiles']),5);self.assertFalse(self.path.exists())

    def test_save_and_read_preserves_unrelated_configuration(self):
        other=self.path.with_name('local-preferences.json');other.write_bytes(b'{"notifications":false}')
        result=self.client.post('/api/local/challenge-preferences',json={'profile':'adept','limit':100})
        self.assertEqual(result.status_code,200);self.assertEqual(preferences.read_challenge_preferences(),{'profile':'adept','limit':100})
        self.assertEqual(other.read_bytes(),b'{"notifications":false}')
        self.assertFalse(self.path.with_suffix('.tmp').exists())

    def test_invalid_api_values_never_replace_saved_preferences(self):
        preferences.save_challenge_preferences({'profile':'master','limit':50})
        before=self.path.read_bytes()
        for value in (True,50.5,'50',19,101):
            with self.subTest(value=value):
                self.assertEqual(self.client.post('/api/local/challenge-preferences',json={'profile':'master','limit':value}).status_code,422)
        for payload in ({'profile':'unknown','limit':50},{'profile':True,'limit':50},{'profile':'master','limit':50,'other':1}):
            self.assertEqual(self.client.post('/api/local/challenge-preferences',json=payload).status_code,422)
        self.assertEqual(self.path.read_bytes(),before)

    def test_bad_disk_values_fall_back_without_rewriting_them(self):
        for raw in ('null','[]','{bad','{"profile":"unknown"}','{"limit":true}'):
            self.path.write_text(raw,encoding='utf-8')
            self.assertEqual(preferences.read_challenge_preferences(),{'profile':'beginner','limit':50})
            self.assertEqual(self.path.read_text('utf-8'),raw)

    def test_write_failure_reports_failure_and_preserves_previous_value(self):
        preferences.save_challenge_preferences({'profile':'practiced','limit':50});before=self.path.read_bytes()
        with patch.object(Path,'replace',side_effect=OSError('offline write failure')):
            response=self.client.post('/api/local/challenge-preferences',json={'profile':'master','limit':100})
        self.assertEqual(response.status_code,503);self.assertEqual(self.path.read_bytes(),before)

    def test_multiplayer_never_samples_single_player_preferences(self):
        factory=fun_tests.FunChallengeTests()
        for mode,method in (('四人赛','four_player_answer'),('双人对战','two_player_answer')):
            value=factory.make_worker();value._open_fun_challenge.side_effect=lambda mode=mode:setattr(value,'_fun_mode',mode)
            setattr(value,method,Mock(return_value='multiplayer'))
            with patch('fun_challenge.challenge_length') as sample:
                self.assertEqual(value.funny_answer(),'multiplayer');sample.assert_not_called()

if __name__=='__main__':unittest.main()
