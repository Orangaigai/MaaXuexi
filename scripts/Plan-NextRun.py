"""Rearm the native daily trigger before the current learning run starts."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from schedule_support import rearm_for_run, daily_started_today

if __name__=='__main__':
    plan=rearm_for_run()
    if plan and not plan['enabled']:
        print('Manual execution selected; automatic launcher skipped.')
        sys.exit(4)
    if daily_started_today() and '--acceptance' not in sys.argv[1:]:
        print('Already started today; automatic launch skipped. Manual continuation is available.')
        sys.exit(5)
    print('Next automatic run: '+plan['next_run_at'] if plan else 'No editable schedule configured; retained the native trigger.')
