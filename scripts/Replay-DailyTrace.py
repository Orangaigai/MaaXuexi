"""Replay saved daily-stage evidence locally. No ADB, clicks or AI requests."""
import argparse,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT);sys.path.insert(0,str(ROOT/'backend'))
from diagnostics import load_replay
from daily_flow import daily_scene

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('index',type=Path)
    parser.add_argument('--native-ocr',action='store_true',help='Re-read saved PNGs with the installed Maa OCR; no device input')
    args=parser.parse_args()
    if args.native_ocr:
        import numpy as np
        from PIL import Image
        from maa.controller import CustomController
        from maa.resource import Resource
        from maa.tasker import Tasker
        from maa.pipeline import JOCR
        class MemoryScreen(CustomController):
            def connect(self):return True
            def connected(self):return True
            def request_uuid(self):return 'offline-daily-replay'
            def get_features(self):return 0
            def screencap(self):return self.frame.copy()
            def forbidden(self,*args):raise AssertionError('No Android input in offline replay')
            click=swipe=start_app=stop_app=touch_down=touch_move=touch_up=click_key=input_text=key_down=key_up=forbidden
        Tasker.set_log_dir(str(ROOT/'logs/replay'));Tasker.set_save_draw(False)
        resource=Resource();resource.set_cpu()
        assert resource.post_ocr_model(str(ROOT/'resource/model/ocr')).wait().succeeded
        controller=MemoryScreen();assert controller.post_connection().wait().succeeded
        tasker=Tasker();assert tasker.bind(resource,controller)
    results=[]
    for frame,path in load_replay(args.index):
        records=frame['records']
        if args.native_ocr:
            controller.frame=np.ascontiguousarray(np.asarray(Image.open(path).convert('RGB'))[:,:,::-1])
            detail=tasker.post_recognition('OCR',JOCR(roi=[0,35,720,1205]),controller.frame).wait().get()
            records=[dict(text=row.text,box=list(row.box)) for row in detail.nodes[0].recognition.all_results]
        actual=daily_scene(records,pending=True)
        results.append(dict(file=path.name,expected=frame['scene'],actual=actual,matched=actual==frame['scene']))
    print(json.dumps(dict(no_android_input=True,no_model_calls=True,frames=results),ensure_ascii=False,indent=2))
    return 0 if all(row['matched'] for row in results) else 1

if __name__=='__main__':raise SystemExit(main())
