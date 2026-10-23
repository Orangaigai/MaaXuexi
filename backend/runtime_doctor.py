"""Read-only, aggregate local checks. Never starts an app or tests a paid API."""
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import subprocess
from task_support import now,VERSION


def diagnose(root,*,health=None,provider=None,schedule=None,adb_command=None,screen_metrics=None):
    root=Path(root).resolve();checks=[]
    def add(name,state,message):checks.append(dict(name=name,state=state,message=message))
    for label,relative in [('Python环境','.venv/Scripts/python.exe'),('启动入口','scripts/Start-MaaXuexi.ps1'),
                           ('OCR资源','resource/model/ocr/rec.onnx')]:
        exists=(root/relative).is_file()
        add(label,'ok' if exists else 'error','已找到' if exists else '文件缺失，请检查安装目录')
    try:
        manifest=json.loads((root/'config/source-manifest.json').read_text('utf-8-sig'))
        if not isinstance(manifest,dict):raise ValueError()
        changed=[]
        for relative,digest in manifest.items():
            file=(root/relative).resolve()
            if not file.is_relative_to(root) or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
                changed.append(relative)
        add('程序文件','warn' if changed else 'ok',f'{len(changed)}项与安装记录不同' if changed else f'{len(manifest)}项校验一致')
    except (OSError,ValueError,TypeError):add('程序文件','warn','安装校验记录暂不可读')
    try:add('Maa框架','ok','版本 '+importlib.metadata.version('MaaFw'))
    except importlib.metadata.PackageNotFoundError:add('Maa框架','error','未找到运行依赖')
    try:
        config=json.loads((root/'config/emulator.json').read_text('utf-8-sig'))
        if not isinstance(config,dict):raise ValueError()
        missing=[key for key in ('mumu_cli','adb_path') if not Path(config.get(key,'')).is_file()]
        add('模拟器配置','error' if missing else 'ok','MuMu或ADB路径不可用' if missing else 'MuMu与ADB路径可用')
    except (OSError,ValueError,TypeError):add('模拟器配置','error','配置缺失或格式不正确')
    health=health or {}
    add('本机后台','ok' if health.get('local_version')==VERSION else 'warn',
        '当前版本 '+VERSION if health.get('local_version')==VERSION else '后台暂未连接或需要重新启动')
    connected=health.get('connected') is True
    add('设备连接','ok' if connected else 'warn','ADB在线' if connected else '暂未连接，执行任务时会自动准备设备')
    if connected and adb_command:
        for label,command in [('学习强国',['pm','path','cn.xuexi.android']),('显示环境',['wm','size'])]:
            try:
                value=subprocess.run([*adb_command,'shell',*command],capture_output=True,text=True,
                    encoding='utf-8',errors='replace',timeout=3,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                ok=value.returncode==0 and bool(value.stdout.strip())
                message=('已安装' if label=='学习强国' else value.stdout.strip().replace('\n','；')[:130]) if ok else '未确认，请检查模拟器'
                add(label,'ok' if ok else 'warn',message)
            except (OSError,subprocess.SubprocessError):add(label,'warn','设备响应超时，未发送界面操作')
    metrics=screen_metrics if isinstance(screen_metrics,dict) else {}
    seconds=metrics.get('seconds')
    if isinstance(seconds,(float,int)) and not isinstance(seconds,bool) and math.isfinite(seconds) and seconds>=0:
        add('截图耗时','ok',f"最近一次 {seconds*1000:.0f} ms；采样时间 {metrics.get('sampled_at','未记录')}。仅含截图，不含OCR和模型答题。")
    else:
        add('截图耗时','warn','暂无截图样本；执行任务后再检查。自检不会启动模拟器或额外截图。')
    provider=provider or {}
    add('DeepSeek配置','ok' if provider.get('validated') else 'warn',
        '已配置，最近验证通过' if provider.get('validated') else '已配置，尚待验证' if provider.get('configured') else '未配置，可先执行基础任务')
    schedule=schedule or {}
    add('自动计划','ok' if schedule.get('available') else 'warn',
        ('已启用' if schedule.get('enabled') else '已关闭，当前为手动执行') if schedule.get('available') else '暂未读取到Windows计划')
    return dict(checked_at=now(),version=VERSION,checks=checks,paid_requests=0,device_input=False,
                summary={state:sum(row['state']==state for row in checks) for state in ('ok','warn','error')})
