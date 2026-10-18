"""Rearm the native daily trigger before the current learning run starts."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from schedule_support import rearm_for_run

if __name__=='__main__':
    plan=rearm_for_run()
    print('Next automatic run: '+plan['next_run_at'] if plan else 'No editable schedule configured; retained the native trigger.')
