"""Automatic triggers must be due, online, enabled and unclaimed."""
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import catchup_support as catchup


def clock(value):
    return datetime.fromisoformat(value+'+08:00')


class CatchUpTests(unittest.TestCase):
    def setUp(self):
        self.native = dict(enabled=True,start_boundary='2026-10-27T08:17:02+08:00',random_delay_seconds=0)
        for name, value in (('daily_started_today',False),('_native',self.native),
                            ('now_china',clock('2026-10-27T11:08:00'))):
            mock = patch.object(catchup,name,return_value=value)
            setattr(self,name,mock.start());self.addCleanup(mock.stop)

    def test_late_login_is_due_and_does_not_advance_or_claim_schedule(self):
        self.assertTrue(catchup.automatic_due()[0])
        self._native.assert_called_once_with()

    def test_before_chosen_time_is_not_due(self):
        self.assertFalse(catchup.automatic_due(clock('2026-10-27T08:16:59'))[0])

    def test_a_stale_daily_boundary_uses_todays_clock_instead_of_running_early(self):
        self.native['start_boundary']='2026-10-24T08:17:02+08:00'
        self.assertFalse(catchup.automatic_due(clock('2026-10-27T07:00:00'))[0])
        self.assertTrue(catchup.automatic_due()[0])

    def test_future_date_is_not_due_even_if_its_time_of_day_has_passed(self):
        self.native['start_boundary']='2026-10-28T07:36:18+08:00'
        self.assertFalse(catchup.automatic_due()[0])

    def test_already_started_skips_even_native_query(self):
        self.daily_started_today.return_value=True
        self.assertFalse(catchup.automatic_due()[0]);self._native.assert_not_called()

    def test_manual_mode_skips(self):
        self.native['enabled']=False
        self.assertFalse(catchup.automatic_due()[0])

    def test_legacy_random_delay_is_not_used_as_permission_to_run_early(self):
        self.native['random_delay_seconds']=300
        self.assertFalse(catchup.automatic_due(clock('2026-10-27T08:20:00'))[0])
        self.assertTrue(catchup.automatic_due(clock('2026-10-27T08:23:00'))[0])

    def test_offline_then_online_starts_without_opening_any_app_during_wait(self):
        with patch.object(catchup,'internet_ready',side_effect=[False,True]) as net, patch.object(catchup.time,'sleep') as sleep:
            self.assertTrue(catchup.wait_for_automatic_start(Mock()))
        self.assertEqual(net.call_count,2);sleep.assert_called_once()
        self.assertEqual(self._native.call_count,2)

    def test_manual_switch_while_waiting_is_rechecked_before_launch(self):
        self._native.side_effect=[self.native,dict(self.native,enabled=False)]
        with patch.object(catchup,'internet_ready',side_effect=[False,True]),patch.object(catchup.time,'sleep'):
            self.assertFalse(catchup.wait_for_automatic_start(Mock()))

    def test_other_trigger_claiming_today_during_wait_skips(self):
        self.daily_started_today.side_effect=[False,False,True]
        with patch.object(catchup,'internet_ready',return_value=False) as net,patch.object(catchup.time,'sleep'):
            self.assertFalse(catchup.wait_for_automatic_start(Mock()))
        net.assert_called_once()

    def test_network_wait_is_bounded_without_advancing_schedule(self):
        with patch.object(catchup,'internet_ready',return_value=False),patch.object(catchup.time,'monotonic',side_effect=[0,0,5]),patch.object(catchup.time,'sleep'):
            self.assertFalse(catchup.wait_for_automatic_start(Mock(),timeout=5))
        self._native.assert_called_once_with()

    def test_midnight_wait_does_not_turn_yesterdays_missed_run_into_an_early_run(self):
        self.now_china.side_effect=[clock('2026-10-27T23:59:00'),clock('2026-10-28T00:01:00')]
        with patch.object(catchup,'internet_ready',side_effect=[False,True]),patch.object(catchup.time,'sleep'):
            self.assertFalse(catchup.wait_for_automatic_start(Mock()))

    def test_failed_native_status_does_not_start_or_probe_network(self):
        self._native.side_effect=subprocess.TimeoutExpired('powershell',20)
        with patch.object(catchup,'internet_ready') as net:
            self.assertFalse(catchup.wait_for_automatic_start(Mock()))
        net.assert_not_called()

    def test_public_probe_has_no_auth_or_model_call_and_accepts_http_error_response(self):
        with patch.object(catchup,'urlopen',side_effect=HTTPError('https://www.xuexi.cn/',403,'denied',{},None)) as request:
            self.assertTrue(catchup.internet_ready())
        arg=request.call_args.args[0]
        self.assertEqual(arg.get_method(),'HEAD');self.assertEqual(arg.header_items(),[])
        with patch.object(catchup,'urlopen',side_effect=URLError('offline')):
            self.assertFalse(catchup.internet_ready())


if __name__=='__main__':unittest.main()
