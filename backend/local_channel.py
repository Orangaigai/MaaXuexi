"""Open the current local-channel task and verify its own actual learning point."""
import json,re,subprocess
from pathlib import Path
from PIL import Image
from task_support import TaskIncomplete,is_complete
from daily_flow import daily_scene,profile_page,verification_visible
from home_navigation import home_ready

REGION_LABELS=set('北京 天津 河北 山西 内蒙古 辽宁 吉林 黑龙江 上海 江苏 浙江 安徽 福建 江西 山东 河南 湖北 湖南 广东 广西 海南 重庆 四川 贵州 云南 西藏 陕西 甘肃 青海 宁夏 新疆 香港 澳门 台湾'.split())


def local_home_entry(records):
    if not home_ready(records) or verification_visible(records):return None
    regions=[r for r in records if r['text'].strip() in REGION_LABELS
             and 100 <= r['box'][1] < 180]
    return regions[0] if len(regions)==1 else None


def local_platform_entry(records):
    if verification_visible(records) or any(r['text'].strip()=='权限申请目的说明' for r in records):return None
    if not any(r['text'].strip()=='切换地区' and 150 <= r['box'][1] < 280 for r in records):
        return None
    entries=[r for r in records if re.fullmatch(r'.{2,16}学习平台',r['text'].strip())
             and r['box'][0]<240 and 250 <= r['box'][1] < 420]
    return entries[0] if len(entries)==1 else None


class LocalChannelTasks:
    def _local_click(self,row):
        self._check_active();self._guard_verification()
        if not self._app_in_foreground():
            raise TaskIncomplete('本地频道操作时应用不在前台')
        x,y,w,h=row['box']
        x,y=round(x+w/2),round(y+h/2)
        if not self.tasker.controller.post_click(x,y).wait().succeeded:
            raise TaskIncomplete('本地频道触控失败')
        self._sleep(.5)

    def _local_clear_location_prompt(self):
        for _ in range(4):
            records=self._screen_records();self._guard_verification(records)
            texts={re.sub(r'\s+','',r['text']) for r in records}
            if {'权限申请目的说明','地理位置'} <= texts:
                buttons=[r for r in records if r['text'].strip()=='好的' and 700 <= r['box'][1] < 1000]
                if len(buttons)!=1 or not self._app_in_foreground():
                    raise TaskIncomplete('本地定位说明入口不明确')
                self._local_click(buttons[0])
                self._sleep(1)
                continue
            prompt=''.join(texts)
            if '学习强国' in prompt and ('位置' in prompt or '定位' in prompt):
                deny=[r for r in records if r['text'].strip()=='不允许']
                if deny:
                    command=getattr(self,'_adb_command',None)
                    if not command:raise TaskIncomplete('定位权限弹窗来源无法确认')
                    output=subprocess.run([*command,'shell','dumpsys','activity','activities'],check=True,
                        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=8,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout
                    system=any(re.search(r'(?:topResumedActivity|mResumedActivity)=',line) and
                        re.search(r'com\.(?:google\.)?android\.permissioncontroller/',line) for line in output.splitlines())
                    if not system or len(deny)!=1:
                        raise TaskIncomplete('定位权限不是已确认的系统弹窗，保留现场')
                    # Denial needs no new location permission; use the already selected region.
                    x,y,w,h=deny[0]['box']
                    if not self.tasker.controller.post_click(round(x+w/2),round(y+h/2)).wait().succeeded:
                        raise TaskIncomplete('定位权限拒绝未成功')
                    self.send_log('本地频道：拒绝新增定位权限，继续使用App已选地区')
                    self._sleep(.6)
                    continue
            if '权限申请目的说明' in texts:
                raise TaskIncomplete('当前是其他权限说明，未点击页面底层内容')
            return records
        raise TaskIncomplete('本地定位说明未关闭，保留页面')

    def _local_evidence(self,tag,records):
        report=getattr(self,'run_report',None)
        if report is None:return
        folder=Path('logs')/('local-channel-'+report.snapshot()['run_id'])
        try:
            folder.mkdir(parents=True,exist_ok=True)
            (folder/(tag+'.json')).write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            Image.fromarray(self.tasker.controller.cached_image[:,:,::-1]).save(folder/(tag+'.png'))
        except (OSError,ValueError,TypeError,AttributeError):pass

    def _local_task_entry(self):
        records=self._local_clear_location_prompt()
        for _ in range(2):
            if daily_scene(records)!='score' and not profile_page(records):break
            self._check_active()
            if not self._app_in_foreground():raise TaskIncomplete('返回本地频道入口时应用不在前台')
            if self.tasker.post_task('返回').wait().get().status.failed:
                raise TaskIncomplete('返回本地频道首页失败')
            self._sleep(.4)
            records=self._local_clear_location_prompt()
        if any(r['text'].strip()=='切换地区' and 150 <= r['box'][1] < 280 for r in records):return
        if not home_ready(records):
            self._reset_home()
            records=self._local_clear_location_prompt()
        region=local_home_entry(records)
        if region is None:
            self._local_evidence('home-entry-unrecognized',records)
            raise TaskIncomplete('首页未找到唯一地区频道，未按固定位置猜测地区')
        self._local_evidence('before-entry',records)
        self._local_click(region)

    def _local_platform_target(self,records):
        entry=local_platform_entry(records)
        if entry is not None:return entry
        if not any(r['text'].strip()=='切换地区' and 150 <= r['box'][1] < 280 for r in records):return None
        self._check_active();self._guard_verification(records)
        if not self._app_in_foreground():raise TaskIncomplete('地区平台入口识别时应用不在前台')
        # Full-screen OCR can merge the first two icon captions; isolate the first.
        name='本地学习平台入口'
        detail=self.tasker.post_task(name,{name:{
            'recognition':'OCR','expected':['^.{2,16}学习平台$'],'roi':[0,330,145,75],
            'action':'DoNothing','timeout':2500,'pre_delay':0,'post_delay':0,
        }}).wait().get()
        if detail is None or detail.status.failed or not detail.nodes:return None
        local=[dict(text=r.text,box=list(r.box)) for r in getattr(detail.nodes[0].recognition,'all_results',[])]
        return local_platform_entry(local+[r for r in records if r['text'].strip()=='切换地区'])

    def learn_local_channel(self,before=None):
        self._local_clear_location_prompt()
        before=before or self.read_learning_score(['本地频道'])['本地频道']
        if is_complete(before):return before
        if before['maximum']!=1:
            raise TaskIncomplete('本地频道计分规则已变化，需要核实新规则')
        self.send_log('本地频道：从首页当前地区进入，再打开地区学习平台')
        self._local_task_entry()
        self._sleep(3)
        records=self._local_clear_location_prompt()
        self._local_evidence('landing',records)
        entry=self._local_platform_target(records)
        if entry is None:
            raise TaskIncomplete('本地频道尚未确认地区学习平台入口，已保留画面')
        self.send_log('本地频道：打开当前地区的'+entry['text'])
        self._local_click(entry)
        self._sleep(3)
        records=self._local_clear_location_prompt()
        self._local_evidence('platform',records)
        self._score_refresh_required=True
        after=self.read_learning_score(['本地频道'])['本地频道']
        if getattr(self,'run_report',None):self.run_report.task('本地频道',after=after)
        self._local_evidence('score-after',self._screen_records())
        if after['maximum']!=before['maximum'] or after['value']<before['value']:
            raise TaskIncomplete('本地频道积分发生异常变化')
        self.send_log(f"本地频道核分：{before['value']}/{before['maximum']} → {after['value']}/{after['maximum']}")
        return after
