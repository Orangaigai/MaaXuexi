"""Read the existing phone client's history, reconciling each ordered day twice."""
import re
import time
import unicodedata

from history_ledger import checked_ledger
from task_support import TaskIncomplete


class LedgerReadError(TaskIncomplete):
    pass


def clean_text(value):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',value)).replace('：',':').replace('−','-')


def clean_name(value):
    # OCR may omit or change a decorative closing quote at a crop edge.
    # Keep words, digits and other punctuation; never fuzzy-match task names.
    return clean_text(value).translate(str.maketrans('', '', '\"\'“”‘’「」『』'))


def parse_ledger_frame(records, *, bottom=1250):
    """Require a complete timestamp, description and score on the same visible row."""
    # A partly clipped next timestamp can look like "01.11". It still ends
    # the previous row, but is never accepted as a verified transaction time.
    anchors = sorted((row for row in records if 20<=row['box'][0]<230
                      and 108<=row['box'][1]<bottom
                      and re.fullmatch(r'\d{1,2}[:.]\d{0,2}',clean_text(row['text']))),
                     key=lambda row:row['box'][1])
    result = []
    for index,clock in enumerate(anchors):
        if (clock['box'][1]>=min(1225,bottom-25)
                or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',clean_text(clock['text']))):
            continue
        start = clock['box'][1]+clock['box'][3]-4
        end = anchors[index+1]['box'][1]-4 if index+1<len(anchors) else bottom
        names = sorted((row for row in records if 25<=row['box'][0]<550
                        and start<=row['box'][1] and row['box'][1]+row['box'][3]<=end
                        and not re.fullmatch(r'\d{1,2}[:.]\d{0,2}',clean_text(row['text']))),
                       key=lambda row:(row['box'][1],row['box'][0]))
        amounts = sorted((row for row in records if row['box'][0]>=550
                          and start<=row['box'][1] and row['box'][1]+row['box'][3]<=end),
                         key=lambda row:row['box'][0])
        if any(row['box'][1]+row['box'][3]>bottom-24 for row in [*names,*amounts]):
            # Edge-clipped Chinese can still produce plausible but wrong words.
            # Read this row after the next scroll, with all glyphs in view.
            continue
        amount = clean_text(''.join(row['text'] for row in amounts))
        parts,merged = [],[]
        for row in names:
            value=clean_text(row['text'])
            match=re.fullmatch(r'(.+?)([+-]\d{1,3}分)',value) if row['box'][0]+row['box'][2]>600 else None
            if match:
                parts.append(match.group(1));merged.append(match.group(2))
            else:
                parts.append(value)
        if merged:
            if len(set(merged))!=1 or amount and amount!=merged[0]:
                continue
            amount=merged[0]
        match = re.fullmatch(r'([+-]?\d{1,3})分',amount)
        name = clean_name(''.join(parts))
        if not match or not name or re.search(r'积分明细获取|网络开小差|暂无',name):
            continue
        result.append(dict(time=clean_text(clock['text']),name=name,points=int(match.group(1)),
                           only_total='只计入总积分' in name,y=clock['box'][1]))
    return result


def signature(row):
    return (row['time'],row['name'],row['points'],row['only_total'])


def join_ledger_crops(chunks):
    """Use each crop's clear interior, so edge-clipped text cannot replace a row."""
    return [row for index,(records,_) in enumerate(chunks) for row in records
            if (row['box'][1]+row['box'][3]/2<740)==(index==0)]


def merge_frames(previous,current,*,forward):
    if not previous:
        return list(current)
    left,right = (previous,current) if forward else (current,previous)
    for overlap in range(min(len(left),len(right)),0,-1):
        if [signature(row) for row in left[-overlap:]]==[signature(row) for row in right[:overlap]]:
            return left+right[overlap:]
    raise LedgerReadError('相邻流水画面没有确认重叠，未猜测或拼接该日记录。')


class LedgerReading:
    def _ledger_header_points(self):
        """A small isolated score (especially zero) can be missed in full-screen OCR."""
        from maa.pipeline import JOCR
        image=self.tasker.controller.cached_image
        crop=image[580:650,470:710].copy()
        detail=self.tasker.post_recognition('OCR',JOCR(roi=[0,0,240,70]),crop).wait().get()
        if not detail or not detail.status.succeeded or not detail.nodes:
            return None
        records=sorted(detail.nodes[0].recognition.all_results,key=lambda row:row.box[0])
        match=re.fullmatch(r'当日积分[:：]?(\d{1,3})',clean_text(''.join(row.text for row in records)))
        return int(match.group(1)) if match else None

    def _ledger_records(self):
        """OCR only the list, at its native resolution; keep all row fields in view."""
        from maa.pipeline import JOCR
        import numpy as np
        self._check_active()
        self._history_records()
        image = self.tasker.controller.cached_image
        if not isinstance(image,np.ndarray) or image.shape[:2]!=(1280,720):
            raise TaskIncomplete('历史流水截图尺寸未确认，已停止本次读取。')
        chunks = []
        # The whole H5 page scrolls. Overlapping crops keep full rows readable
        # both below the initial chart and after the chart has scrolled away.
        for top,bottom in ((108,850),(630,1255)):
            crop = image[top:bottom].copy()
            detail = self.tasker.post_recognition('OCR',JOCR(roi=[0,0,720,bottom-top]),crop).wait().get()
            if not detail or not detail.status.succeeded or not detail.nodes:
                raise LedgerReadError('本机 OCR 没有返回历史流水。')
            records = [dict(text=row.text,box=[row.box[0],row.box[1]+top,row.box[2],row.box[3]])
                       for row in detail.nodes[0].recognition.all_results]
            if any('积分明细获取中' in row['text'] for row in records):
                raise LedgerReadError('平台尚未返回当天的积分流水。')
            chunks.append((records,bottom))
        return chunks

    def _ledger_view(self):
        chunks = self._ledger_records()
        records = join_ledger_crops(chunks)
        rows = parse_ledger_frame(records,bottom=1255)
        fingerprint = tuple(sorted((clean_text(row['text']),tuple(row['box'])) for row in records))
        return rows,fingerprint

    def _ledger_swipe(self,forward,step=270):
        self._check_active()
        start,end = (1140,1140-step) if forward else (780,780+step)
        if not self.tasker.controller.post_swipe(360,start,360,end,320).wait().succeeded:
            raise TaskIncomplete('历史流水滚动未成功，已停止本次读取。')
        self._sleep(.2)

    def _ledger_top(self):
        previous = None
        for _ in range(30):
            rows,key = self._ledger_view()
            if key==previous:
                return rows,key
            previous = key
            self._ledger_swipe(False,350)
        raise LedgerReadError('未能确认当天流水的起点。')

    def _ledger_pass(self,forward,expected):
        combined,previous = [],None
        for _ in range(60):
            rows,key = self._ledger_view()
            if rows:
                try:
                    combined = merge_frames(combined,rows,forward=forward)
                except LedgerReadError:
                    # Read this same stationary screen once more, without scrolling.
                    self._sleep(.3)
                    rows,key = self._ledger_view()
                    combined = merge_frames(combined,rows,forward=forward)
            if key==previous:
                if not combined and expected!=0:
                    raise LedgerReadError('没有完整读取到当天流水，未将空白当作零分。')
                return [{key:value for key,value in row.items() if key!='y'} for row in combined]
            previous = key
            self._ledger_swipe(forward)
        raise LedgerReadError('当天流水未到达可确认的边界。')

    def _ledger_select(self,day):
        from history_reader import _chart_labels,history_page,parse_day_summary
        for _ in range(12):
            rows = self._history_records()
            if not history_page(rows):
                raise TaskIncomplete('已离开积分明细，未继续点击历史日期。')
            labels = _chart_labels(rows,self.history_date)[0]
            target = next((x for x,value in labels if value.isoformat()==day and 30<x<690),None)
            if target is not None:
                self.tasker.controller.post_click(round(target),145).wait()
                deadline = time.monotonic()+12
                while time.monotonic()<deadline:
                    self._sleep(.25)
                    selected,points = parse_day_summary(self._history_records())
                    if selected and selected.isoformat()==day:
                        return points if points is not None else self._ledger_header_points()
                raise LedgerReadError('所选历史日期没有确认，未关联当天流水。')
            if not labels:
                self._ledger_top()
                continue
            forward = day>max(value.isoformat() for _,value in labels)
            start,end = (650,100) if forward else (100,650)
            self.tasker.controller.post_swipe(start,300,end,300,500).wait()
            self._sleep(.3)
        raise LedgerReadError('未定位到待导入的历史日期。')

    def _read_day_ledger(self,day,points):
        if self._ledger_select(day)!=points:
            raise LedgerReadError('所选日期总分与图表不一致，未导入该日流水。')
        self._sleep(.4)
        self._ledger_top()
        forward = self._ledger_pass(True,points)
        reverse = self._ledger_pass(False,points)
        if forward!=reverse:
            raise LedgerReadError('两轮读取的流水不一致，未保存该日记录。')
        try:
            return checked_ledger(day,points,forward)
        except ValueError as error:
            raise LedgerReadError(str(error)) from error

    def _read_history_ledgers(self,totals):
        verified,unconfirmed = [],[]
        cached = getattr(self,'ledger_cache',{})
        skipped = 0
        targets = [(day,points) for day,points in sorted(totals.items()) if day<self.history_date.isoformat()]
        for index,(day,points) in enumerate(targets):
            self.history_progress('checking',f'正在读取 {day} 的积分流水',
                detail_days=index,detail_total=len(targets),detail_entries=sum(len(row['entries']) for row in verified))
            if cached.get(day,{}).get('points')==points:
                skipped += 1
                continue
            for attempt in range(2):
                try:
                    verified.append(self._read_day_ledger(day,points))
                    break
                except LedgerReadError as error:
                    if attempt==0:
                        self.history_progress('checking',f'{day} 的识别尚未一致，正在重新核对一次')
                    else:
                        unconfirmed.append(dict(day=day,reason=str(error)))
                        self.send_log(f'历史流水 {day} 未核实：{error}')
        self.history_progress('checking','历史积分流水读取结束',detail_days=len(targets),
            detail_total=len(targets),detail_entries=sum(len(row['entries']) for row in verified))
        return dict(ledgers=verified,unconfirmed_ledgers=unconfirmed,cached_ledger_days=skipped)
