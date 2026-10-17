"""Full-content OCR boundaries and exact-frame reuse; no question-height guesses."""
import copy
import time
import numpy as np
from maa.pipeline import JOCR
from task_support import TaskIncomplete
from diagnostics import record_time


def content_roi(image):
    """Only trim uniform outer rows, with padding; keep the complete width."""
    height,width=image.shape[:2]
    top,bottom=(35,min(height,1240)) if (height,width)==(1280,720) else (0,height)
    region=image[top:bottom,:,:3]
    nonuniform=np.any(region!=region[:,:1],axis=(1,2))
    rows=np.flatnonzero(nonuniform)
    if not rows.size:
        return [0,top,width,bottom-top]
    first=max(0,int(rows[0])-24);last=min(bottom-top,int(rows[-1])+25)
    return [0,top+first,width,last-first]


def read_screen(worker):
    """Capture afresh each call. Cache only byte-identical complete frames."""
    started=time.monotonic()
    image=worker.tasker.controller.post_screencap().wait().get()
    record_time(worker,'截图',time.monotonic()-started)
    if not isinstance(image,np.ndarray) or image.ndim!=3 or image.shape[2]!=3 or not image.size:
        worker.connected=False
        raise TaskIncomplete('截图控制器未就绪或模拟器已断开，请重新执行以连接设备')
    previous=getattr(worker,'_screen_ocr_cache',None)
    if previous and np.array_equal(previous[0],image):
        worker._last_screen_ocr_cached=True
        return copy.deepcopy(previous[1])
    worker._last_screen_ocr_cached=False
    roi=content_roi(image)
    started=time.monotonic()
    detail=worker.tasker.post_recognition('OCR',JOCR(roi=roi),image).wait().get()
    if detail is None:
        worker._screen_ocr_cache=None
        raise TaskIncomplete('本机OCR未返回识别结果，已保留当前画面')
    records=[]
    if detail.status.succeeded and detail.nodes:
        records=[dict(text=row.text,box=list(row.box))
                 for row in getattr(detail.nodes[0].recognition,'all_results',[])]
    # A miss on trimmed borders is checked once on the original full app area.
    full=[0,35,720,1205] if image.shape[:2]==(1280,720) else [0,0,image.shape[1],image.shape[0]]
    if not records and roi!=full:
        detail=worker.tasker.post_recognition('OCR',JOCR(roi=full),image).wait().get()
        if detail and detail.status.succeeded and detail.nodes:
            records=[dict(text=row.text,box=list(row.box))
                     for row in getattr(detail.nodes[0].recognition,'all_results',[])]
    record_time(worker,'OCR',time.monotonic()-started)
    worker._screen_ocr_cache=(image.copy(),copy.deepcopy(records)) if records else None
    worker._last_screen_ocr_roi=roi
    return records
