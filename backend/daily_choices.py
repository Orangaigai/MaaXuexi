"""Read long daily-choice pages without dropping image options between screens."""
import re
import numpy as np
from task_support import TaskIncomplete


def visible_options(records):
    options={}
    for row in records:
        x,y,w,h=row['box']
        match=re.match(r'^([A-F])[.．、:：]\s*(.*)$',row['text'].strip())
        if match and 60<=x<=165 and 130<=y<1190 and 23<=h<=65:
            options[match[1]]=(match[2].strip(),[x,y,min(w,40),h])
    return options


def hint_visible(records):
    return any('查看提示' in row['text'] and row['box'][0]>450
               and 300<row['box'][1]<1220 for row in records)


def scan_to_hint(worker,records,image,options):
    """Keep overlapping full frames until the real hint is visible, at most 8 swipes."""
    images=[image.copy()]
    options=dict(options)
    for page in range(9):
        worker._check_active()
        if worker._captcha_present(records):
            worker._guard_verification()
            raise TaskIncomplete('题面读取期间出现访问验证，保留当前题目')
        options.update(visible_options(records))
        if hint_visible(records):
            letters=sorted(options)
            complete=2<=len(letters)<=6 and letters==list('ABCDEF'[:len(letters)])
            return images,options,complete,page
        if page==8:break
        worker.tasker.controller.post_swipe(350,1100,350,400,450).wait()
        worker._sleep(.2)
        records=worker._screen_records()
        frame=getattr(worker,'_screen_ocr_cache',None)
        if not isinstance(frame,tuple):break
        image=frame[0]
        difference=np.abs(image[150:1200,50:670].astype(np.int16)
                          -images[-1][150:1200,50:670].astype(np.int16))
        if np.mean(np.any(difference>8,axis=2))<.001:break
        images.append(image.copy())
    raise TaskIncomplete('长题目已翻阅到末尾，但未识别到查看提示，已保留题面')


def select_scrolled_choices(worker,answer,pages):
    """From the hint at the bottom, select each answer once while moving upward."""
    remaining=set(answer)
    for page in range(pages+2):
        worker._guard_verification()
        for letter in sorted(remaining):
            job=worker.tasker.post_task(f'选项{letter}',{
                f'选项{letter}':{'action':'Click','timeout':80}}).wait().get()
            if job.status.succeeded:
                remaining.remove(letter)
                worker._sleep(.15)
        if not remaining:return True
        if page==pages+1:break
        worker.tasker.controller.post_swipe(350,400,350,1100,450).wait()
        worker._sleep(.2)
    raise TaskIncomplete('长图片题仍有选项未能定位，未提交答案：'+''.join(sorted(remaining)))
