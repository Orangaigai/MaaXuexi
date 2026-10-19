(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const names=['登录','选读文章','视听学习','本地频道','趣味答题','订阅','发表观点','每日答题'];
  const verificationTasks=new Set(['趣味答题','订阅','发表观点','每日答题']);
  const aiTasks=new Set(['趣味答题','发表观点','每日答题']);
  const inputs=new Map();
  let commentEnabled=false,loaded=false,busy=false,health=null,provider=null,deviceList=[],savedTasks=null;
  let refreshing=null,refreshController=null,refreshRevision=0;
  for(const name of names){
    const label=document.createElement('label'),input=document.createElement('input');
    label.className='task-option';input.type='checkbox';input.disabled=true;
    const copy=document.createElement('span'),title=document.createElement('span');
    copy.className='task-copy';title.textContent=name;copy.append(title);
    label.append(input,copy);
    $(verificationTasks.has(name)?'verificationChoices':'basicChoices').append(label);inputs.set(name,input);
    input.addEventListener('change',renderSavedState);
  }
  async function request(path,body,signal){
    const response=await fetch(path,{cache:'no-store',...(body===undefined?{}:{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),
      ...(signal?{signal}:body===undefined?{signal:AbortSignal.timeout(path.endsWith('/diagnostics')?25000:10000)}:{})});
    const result=await response.json();
    if(!response.ok||result.status==='failed')throw Error(typeof result.detail==='string'?result.detail:result.message||'请求未成功，请稍后重试。');
    return result;
  }
  const plan=()=>({tasklist:[...inputs].filter(([,input])=>input.checked).map(([name])=>name),fast_answer:false,debug:false});
  const selectionKey=tasks=>[...tasks].sort().join('|');
  function renderActionEmphasis(){
    const configureFirst=provider?.configured===false&&plan().tasklist.some(name=>aiTasks.has(name));
    const continueFirst=Boolean(health?.can_continue);
    $('start').classList.toggle('primary',!configureFirst&&!continueFirst);
    $('start').classList.toggle('accent',configureFirst||continueFirst);
    $('providerSetup').classList.toggle('primary',configureFirst);
  }
  function renderSavedState(){
    const changed=savedTasks!==null&&selectionKey(plan().tasklist)!==selectionKey(savedTasks);
    $('taskSelectionCount').textContent=loaded?`已选 ${plan().tasklist.length} 项`:'';
    $('taskEditState').textContent=!loaded?'读取中':changed?'有修改，尚未保存':'已保存';
    $('taskEditState').classList.toggle('unsaved',changed);
    renderActionEmphasis();
  }
  function controls(){
    const running=Boolean(health?.task_running),paused=Boolean(health?.paused),requested=Boolean(health?.pause_requested);
    $('runtimeCard').classList.toggle('is-running',running&&!paused);
    $('runtimeCard').classList.toggle('is-waiting',Boolean(paused||health?.can_continue));
    $('runtimeCard').classList.toggle('is-complete',!running&&health?.run_summary?.status==='completed');
    for(const[name,input]of inputs)input.disabled=!loaded||busy||running||(name==='发表观点'&&!commentEnabled);
    $('start').disabled=!loaded||busy||running||!health;
    $('save').disabled=!loaded||busy||running;
    $('saveBase').disabled=!loaded||busy||running;
    $('pause').hidden=!running||paused;$('pause').disabled=busy||requested;
    $('pause').textContent=requested?'正在暂停…':'暂停';
    $('resume').hidden=!health?.can_continue;$('resume').disabled=busy||!health;
    $('stop').hidden=!running;$('stop').disabled=busy;
    $('diagnose').disabled=diagnosing||running||!health;
    $('connect').hidden=true;
    renderSavedState();window.dispatchEvent(new CustomEvent('maa:status',{detail:health}));
  }
  async function act(path,body,message){
    busy=true;controls();
    const target=['/api/pause','/api/continue','/api/stop'].includes(path)?$('runMessage'):$('actionMessage');
    try{const result=await request(path,body);target.textContent=result.message||message;await refresh(true);return result;}
    catch(error){target.textContent=error.message;return null;}
    finally{busy=false;controls();}
  }
  $('start').onclick=()=>{
    const body=plan();
    if(!body.tasklist.length){$('actionMessage').textContent='请至少选择一项任务。';return;}
    if(health?.api_key_configured===false&&body.tasklist.some(name=>aiTasks.has(name))){
      $('actionMessage').textContent='请先配置 DeepSeek 密钥，或先执行基础任务。';$('providerSetup').focus();return;
    }
    act('/api/start',body,'已开始执行所选任务。');
  };
  async function savePlan(body){
    const result=await act('/api/local/task-plan',body,'任务清单已保存。');
    if(result){
      savedTasks=[...result.tasklist];
      for(const[name,input]of inputs)input.checked=result.tasklist.includes(name);
      renderSavedState();
    }
  }
  $('save').onclick=()=>{
    const body=plan();
    if(!body.tasklist.length){$('actionMessage').textContent='请至少选择一项任务。';return;}
    savePlan(body);
  };
  $('saveBase').onclick=()=>savePlan({tasklist:names.filter(name=>!verificationTasks.has(name)),fast_answer:false,debug:false});
  $('pause').onclick=()=>act('/api/pause',{},'已请求暂停。');
  $('stop').onclick=()=>act('/api/stop',{},'已请求中止。');
  $('resume').onclick=()=>act('/api/continue',{},'已开始续做，将核对积分并跳过已完成项目。');
  $('connect').onclick=async()=>{
    try{
      deviceList=(await request('/api/get_device')).devices||[];
      if(!deviceList.length)throw Error('未找到模拟器，请重新启动 MaaXuexi。');
      if(deviceList.length>1&&$('device').hidden){
        $('device').replaceChildren(...deviceList.map((item,index)=>{
          const option=document.createElement('option');option.value=index;option.textContent=item.name+' · '+item.address;return option;
        }));$('device').hidden=false;return;
      }
      await act('/api/connect_device',deviceList[Number($('device').value||0)],'模拟器已连接。');
    }catch(error){$('actionMessage').textContent=error.message;}
  };
  function providerFailure(run){
    const reason=Object.values(run?.tasks||{}).map(row=>row.reason||'').find(value=>/http_(401|402|429|502|503|504)|read_timeout|connection_failed/.test(value));
    if(!reason)return null;
    if(/http_401/.test(reason))return '密钥需重新核对';
    if(/http_402/.test(reason))return 'API 余额不足';
    return /read_timeout/.test(reason)?'连接超时，请稍后再试':'答题服务暂不可用';
  }
  function renderProvider(){
    const run=health?.run_summary,checked=Date.parse(provider?.validation?.checked_at||''),
      occurred=Date.parse(run?.finished_at||run?.started_at||'');
    const issue=Number.isFinite(occurred)&&Number.isFinite(checked)&&occurred>checked?providerFailure(run):null;
    $('provider').textContent=provider?.validated?'已验证':provider?.configured?'待验证':provider?'待配置':'读取中';
    $('provider').classList.toggle('ready',Boolean(provider?.validated));
    $('provider').classList.toggle('attention',Boolean(provider&&!provider.validated));
    $('providerHint').textContent=issue?'最近任务：'+issue+'。详情见每日积分。':provider?.configured?
      '用于自动答题；开启观点时也会生成短评。':'自动答题需要先配置 DeepSeek API 密钥，也可先使用基础任务。';
    $('providerSetup').textContent=provider?.configured?'管理密钥':'配置密钥';
    renderActionEmphasis();
  }
  async function refreshProvider(){
    try{provider=await request('/api/local/provider');renderProvider();}
    catch{$('provider').textContent='暂未连接';}
  }
  function refresh(force=false){
    if(refreshing&&!force)return refreshing;
    const ticket=++refreshRevision;
    refreshController?.abort();const controller=refreshController=new AbortController();
    const timer=setTimeout(()=>controller.abort(),10000);
    refreshing=(async()=>{
    try{
      const response=await request('/api/status',undefined,controller.signal);
      if(ticket!==refreshRevision)return;
      health=response;$('connection').textContent=health.connected?'模拟器已连接':'执行时自动连接';
      $('version').textContent=health.local_version;
      const run=health.run_summary;let text='选择任务后即可开始。';
      if(health.task_running){
        if(health.pause_requested)text='正在暂停，当前操作结束后将停下。';
        else if(health.paused)text=health.manual_paused?'任务已暂停，点击“继续执行”恢复。':'请在模拟器处理验证或提示，完成后会自动继续。';
        else text=run?.phase||'正在准备任务…';
      }else if(run?.status==='completed')text='本轮任务已结束，积分已核对。';
      else if(health.can_continue)text='还有任务未完成，可点击“继续执行”补做。';
      else if(health.last_error)text='任务未完成，请查看每日积分中的任务详情。';
      $('status').textContent=text;renderProvider();
      document.title=(health.task_running?(health.paused?'[已暂停] ':'[运行中] '):health.can_continue?'[待继续] ':'')+'MaaXuexi · 任务控制台';
    }catch{if(ticket!==refreshRevision)return;health=null;$('status').textContent='后台暂未连接，正在等待恢复…';}
    controls();
    })().finally(()=>{clearTimeout(timer);if(ticket===refreshRevision)refreshing=null;});
    return refreshing;
  }
  async function initialize(){
    await refresh();
    try{
      const policy=await request('/api/local/comment-policy');commentEnabled=policy.enabled;
    }catch{commentEnabled=false;}
    inputs.get('发表观点').title=commentEnabled?'':'发表观点尚未授权或设置暂不可用。';
    try{
      const saved=await request('/api/local/task-plan');
      savedTasks=[...saved.tasklist];
      for(const[name,input]of inputs)input.checked=saved.tasklist.includes(name);loaded=true;
    }catch(error){$('actionMessage').textContent=error.message;}
    controls();await refreshProvider();
  }
  let lines=[],logSession='',logCursor=-1,logPaint=false;
  function paintLogs(){
    if(logPaint||document.hidden||!$('logPanel').open)return;
    logPaint=true;requestAnimationFrame(()=>{logPaint=false;$('logs').textContent=lines.join('\n');$('logs').scrollTop=$('logs').scrollHeight;});
  }
  function openLog(){
    const query=new URLSearchParams({v:'2',session:logSession,after:String(logCursor)});
    const socket=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/api/ws?'+query);
    socket.onmessage=event=>{
      let value;try{value=JSON.parse(event.data);}catch{value=null;}
      if(value?.session){
        if(value.session!==logSession){logSession=value.session;logCursor=-1;}
        if(value.kind==='hello')return;
        if(!Number.isSafeInteger(value.sequence)||value.sequence<=logCursor)return;
        logCursor=value.sequence;
      }
      lines.push(value?.session?String(value.message):String(event.data));lines=lines.slice(-600);paintLogs();
    };
    socket.onclose=()=>setTimeout(openLog,4000);socket.onerror=()=>socket.close();
  }
  $('clearLog').onclick=()=>{lines=[];$('logs').textContent='';};
  let diagnosing=false;
  $('diagnose').onclick=async()=>{
    if(diagnosing||health?.task_running)return;
    diagnosing=true;$('diagnose').disabled=true;$('diagnosticsMessage').textContent='正在检查…';
    try{
      const result=await request('/api/local/diagnostics');$('diagnosticsResults').replaceChildren();
      const labels={ok:'正常',warn:'提示',error:'需处理'};
      for(const row of result.checks){
        const item=document.createElement('li'),name=document.createElement('span'),message=document.createElement('span');
        name.textContent=row.name;message.textContent=`${labels[row.state]} · ${row.message}`;
        message.className=row.state;item.append(name,message);$('diagnosticsResults').append(item);
      }
      $('diagnosticsMessage').textContent=result.summary.error?`检查完成，${result.summary.error}项需要处理。`:'检查完成。';
    }catch(error){$('diagnosticsMessage').textContent=error.message;}
    finally{diagnosing=false;$('diagnose').disabled=Boolean(health?.task_running);}
  };
  $('logPanel').addEventListener('toggle',paintLogs);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){refresh(true);refreshProvider();paintLogs();}});
  async function poll(){await refresh();setTimeout(poll,document.hidden?15000:3000);}
  initialize().finally(()=>setTimeout(poll,3000));openLog();
})();
