(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const names=['登录','选读文章','视听学习','本地频道','每日答题','订阅','发表观点','趣味答题'];
  const verificationTasks=new Set(['趣味答题','订阅','发表观点','每日答题']);
  const aiTasks=new Set(['趣味答题','发表观点','每日答题']);
  const inputs=new Map();
  let commentEnabled=false,loaded=false,busy=false,health=null,provider=null,deviceList=[],savedTasks=null,savedOrder=null;
  let refreshing=null,refreshController=null,refreshRevision=0,pendingButton=null,lastRun=null;
  const taskOrder=new window.MaaTaskOrder($('verificationChoices'),names.filter(name=>verificationTasks.has(name)),message=>{
    $('actionMessage').dataset.tone='info';$('actionMessage').textContent=message;renderSavedState();
  });
  const buttonLabels={save:'保存所选任务',saveBase:'保存基础任务',start:'立即执行',resume:'继续执行',stop:'中止任务'};
  for(const name of names){
    const label=document.createElement('label'),input=document.createElement('input');
    label.className='task-option';input.type='checkbox';input.disabled=true;
    const copy=document.createElement('span'),title=document.createElement('span');
    copy.className='task-copy';title.textContent=name;copy.append(title);
    label.append(input,copy);
    if(verificationTasks.has(name))taskOrder.append(name,label);else $('basicChoices').append(label);
    inputs.set(name,input);
    input.addEventListener('change',()=>{$('actionMessage').textContent='';renderSavedState();});
  }
  async function request(path,body,signal){
    const response=await fetch(path,{cache:'no-store',...(body===undefined?{}:{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),
      ...(signal?{signal}:body===undefined?{signal:AbortSignal.timeout(path.endsWith('/diagnostics')?25000:10000)}:{})});
    const result=await response.json();
    if(!response.ok||result.status==='failed')throw Error(typeof result.detail==='string'?result.detail:result.message||'请求未成功，请稍后重试。');
    return result;
  }
  const plan=()=>({tasklist:[...names.filter(name=>!verificationTasks.has(name)),...taskOrder.value()].filter(name=>inputs.get(name).checked),fast_answer:false,debug:false});
  const selectionKey=tasks=>[...tasks].sort().join('|');
  const taskChanged=()=>savedTasks!==null&&(selectionKey(plan().tasklist)!==selectionKey(savedTasks)||taskOrder.value().join('|')!==savedOrder.join('|'));
  function renderActionEmphasis(){
    const configureFirst=(provider?.configured===false||health?.api_key_configured===false)&&plan().tasklist.some(name=>aiTasks.has(name));
    const continueFirst=Boolean(health?.can_continue);
    $('start').classList.toggle('primary',!configureFirst&&!continueFirst);
    $('start').classList.toggle('accent',configureFirst||continueFirst);
    $('providerSetup').classList.toggle('primary',configureFirst);
  }
  function renderSavedState(){
    const changed=taskChanged();
    $('taskSelectionCount').textContent=loaded?`已选 ${plan().tasklist.length} 项`:'';
    $('taskEditState').textContent=!loaded?'读取中':changed?'有修改，尚未保存':'已保存';
    $('taskEditState').classList.toggle('unsaved',changed);
    $('taskReset').hidden=!changed;$('taskReset').disabled=!changed||busy||Boolean(health?.task_running);
    renderActionEmphasis();
  }
  $('taskReset').onclick=()=>{
    for(const[name,input]of inputs)input.checked=savedTasks.includes(name);
    taskOrder.set(savedOrder);
    $('actionMessage').textContent='';renderSavedState();
    [...inputs.values()].find(input=>!input.disabled)?.focus();
  };
  function renderProgress(){
    const run=health?.run_summary||lastRun,rows=names.filter(name=>run?.tasks?.[name]).map(name=>run.tasks[name]);
    const confirmed=rows.filter(row=>['verified','skipped_full','credited','skipped_mode'].includes(row.state)).length;
    $('runProgressBlock').hidden=!rows.length;
    $('runProgressLabel').textContent=health?'本轮进度':'上次确认的进度';
    $('runProgress').max=Math.max(1,rows.length);$('runProgress').value=confirmed;
    $('runProgress').setAttribute('aria-valuetext',`已确认 ${confirmed} 项，共 ${rows.length} 项`);
    $('runProgressCount').textContent=`${confirmed} / ${rows.length} 项已确认`;
    const stamp=Date.parse(run?.finished_at||run?.started_at||'');
    $('runMeta').textContent=Number.isFinite(stamp)?
      new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(stamp)+
      (run.finished_at?' 结束':' 开始'):'';
  }
  function controls(){
    const running=Boolean(health?.task_running),paused=Boolean(health?.paused),requested=Boolean(health?.pause_requested);
    const importing=Boolean(health?.history_import?.active);
    const run=health?.run_summary;
    const state=!health?(loaded?'offline':'connecting'):importing?'importing':running?
      requested?'pausing':paused?(health.manual_paused||run?.pause_kind==='manual'?'paused':'verification'):'running':
      run?.status==='completed'?'complete':health.can_continue?'incomplete':
      health.last_error||['failed','error'].includes(run?.status)?'error':['cancelled','stopped'].includes(run?.status)?'stopped':'idle';
    const labels={connecting:['·','连接中'],offline:['!','后台未连接'],idle:['·','待执行'],
      importing:['↻','导入中'],running:['↻','执行中'],pausing:['Ⅱ','正在暂停'],paused:['Ⅱ','手动暂停'],
      verification:['!','等待本人处理'],incomplete:['…','待补做'],complete:['✓','本轮完成'],error:['!','需要处理'],stopped:['■','已中止']};
    $('runtimeCard').dataset.state=state;
    $('runtimeSymbol').textContent=labels[state][0];$('runtimeState').textContent=labels[state][1];
    $('runtimeCard').classList.toggle('is-running',['running','importing'].includes(state));
    $('runtimeCard').classList.toggle('is-waiting',state==='verification');
    $('runtimeCard').classList.toggle('is-complete',state==='complete');
    $('runtimeCard').classList.toggle('is-offline',state==='offline');
    for(const[name,input]of inputs)input.disabled=!loaded||busy||running||(name==='发表观点'&&!commentEnabled);
    taskOrder.disable(!loaded||busy||running);
    $('start').disabled=!loaded||busy||running||importing||!health;
    $('save').disabled=!loaded||busy||running||!health;
    $('saveBase').disabled=!loaded||busy||running||!health;
    $('pause').hidden=!running||paused;$('pause').disabled=busy||requested;
    $('pause').textContent=requested?'正在暂停…':'暂停';
    $('resume').hidden=!health?.can_continue;$('resume').disabled=busy||importing||!health;
    $('stop').hidden=!running;$('stop').disabled=busy;
    $('connect').hidden=true;
    if(pendingButton==='pause')$('pause').textContent='正在暂停…';
    for(const[id,label]of Object.entries(buttonLabels)){
      $(id).classList.toggle('is-busy',pendingButton===id);$(id).setAttribute('aria-busy',String(pendingButton===id));
      $(id).textContent=pendingButton===id?(id.startsWith('save')?'保存中…':id==='start'?'正在启动…':id==='resume'?'正在继续…':'正在中止…'):label;
    }
    $('pause').classList.toggle('is-busy',pendingButton==='pause');$('pause').setAttribute('aria-busy',String(pendingButton==='pause'));
    renderProgress();
    renderSavedState();window.dispatchEvent(new CustomEvent('maa:status',{detail:health}));
  }
  window.addEventListener('maa:history-import',event=>{
    if(health){health.history_import=event.detail;controls();}
  });
  async function act(path,body,message,button=null){
    busy=true;pendingButton=button;controls();
    const target=['/api/pause','/api/continue','/api/stop'].includes(path)?$('runMessage'):$('actionMessage');
    target.textContent='';
    try{const result=await request(path,body);target.dataset.tone='success';target.textContent=result.message||message;await refresh(true);return result;}
    catch(error){target.dataset.tone='error';target.textContent=error.message;return null;}
    finally{busy=false;pendingButton=null;controls();}
  }
  $('start').onclick=()=>{
    const body=plan();
    if(!body.tasklist.length){$('actionMessage').dataset.tone='error';$('actionMessage').textContent='请至少选择一项任务。';return;}
    if(health?.api_key_configured===false&&body.tasklist.some(name=>aiTasks.has(name))){
      $('actionMessage').dataset.tone='info';$('actionMessage').textContent='请先配置 DeepSeek 密钥，或先执行基础任务。';$('providerNotice').hidden=false;$('providerSetup').focus();return;
    }
    act('/api/start',body,'已开始执行所选任务。','start');
  };
  async function savePlan(body,button){
    const result=await act('/api/local/task-plan',{...body,task_order:taskOrder.value()},'任务清单及顺序已保存。',button);
    if(result){
      savedTasks=[...result.tasklist];
      taskOrder.set(result.task_order);savedOrder=taskOrder.value();
      for(const[name,input]of inputs)input.checked=result.tasklist.includes(name);
      renderSavedState();
    }
  }
  $('save').onclick=()=>{
    const body=plan();
    if(!body.tasklist.length){$('actionMessage').dataset.tone='error';$('actionMessage').textContent='请至少选择一项任务。';return;}
    savePlan(body,'save');
  };
  $('saveBase').onclick=()=>savePlan({tasklist:names.filter(name=>!verificationTasks.has(name)),fast_answer:false,debug:false},'saveBase');
  $('pause').onclick=()=>act('/api/pause',{},'已请求暂停。','pause');
  $('stop').onclick=()=>act('/api/stop',{},'已请求中止。','stop');
  $('resume').onclick=()=>act('/api/continue',{},'已开始续做，将核对积分并跳过已完成项目。','resume');
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
    }catch(error){$('actionMessage').dataset.tone='error';$('actionMessage').textContent=error.message;}
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
    const needsSetup=Boolean(provider&&!provider.validated)||health?.api_key_configured===false;
    $('providerNotice').hidden=!issue&&!needsSetup;
    $('provider').textContent=issue?'答题服务需要处理':provider?.configured?'答题服务待验证':'答题服务待配置';
    $('provider').classList.toggle('ready',Boolean(provider?.validated));
    $('provider').classList.toggle('attention',Boolean(provider&&!provider.validated));
    $('providerHint').textContent=issue?'最近任务：'+issue+'。详情见每日积分。':provider?.configured?
      '用于自动答题；开启观点时也会生成短评。':'自动答题需要先配置 DeepSeek API 密钥，也可先使用基础任务。';
    $('providerSetup').textContent=provider?.configured?'检查答题服务':'配置答题服务';
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
      health=response;lastRun=health.run_summary||null;$('connection').textContent=health.connected?'模拟器已连接':'本机服务已连接';
      $('connection').dataset.state=health.connected?'device':'online';
      $('connection').title=health.connected?'当前模拟器已连接。':'执行任务时会自动准备模拟器。';
      const run=health.run_summary;let text='选择任务后即可开始。';
      if(health.history_import?.active)text='正在导入历史积分，学习任务将在导入结束后可用。';
      else if(health.task_running){
        if(health.pause_requested)text='正在暂停，当前操作结束后将停下。';
        else if(health.paused)text=health.manual_paused?'任务已暂停，点击“继续执行”恢复。':'请在模拟器处理验证或提示，完成后会自动继续。';
        else text=run?.phase||'正在准备任务…';
      }else if(run?.status==='completed')text='本轮所选任务已完成，积分已核对。';
      else if(health.can_continue){
        const remaining=(run?.unfinished_tasks||[]).join('、');
        text='本轮已结束'+(remaining?'；待补做：'+remaining:'，还有任务未完成')+'。点击“继续执行”补做，已满分项目会跳过。';
      }
      else if(health.last_error||['failed','error'].includes(run?.status))text='任务未完成，请查看每日积分中的任务详情。';
      else if(['cancelled','stopped'].includes(run?.status))text='本轮已中止，可查看积分记录和任务详情。';
      $('status').textContent=text;renderProvider();
      document.title=(health.history_import?.active?'[导入中] ':health.task_running?(health.paused?'[已暂停] ':'[运行中] '):health.can_continue?'[待补做] ':'')+'MaaXuexi · 任务控制台';
    }catch{if(ticket!==refreshRevision)return;health=null;$('status').textContent='后台暂未连接，正在等待恢复…';
      document.title='[未连接] MaaXuexi · 任务控制台';
      $('connection').textContent='正在重连后台';$('connection').dataset.state='offline';$('connection').title='暂未读到新状态，页面会自动重连。';}
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
    if(!commentEnabled){
      const input=inputs.get('发表观点'),note=document.createElement('small');note.className='task-unavailable';note.textContent='未启用';
      input.closest('label').title=input.title;input.nextElementSibling.append(note);
    }
    try{
      const saved=await request('/api/local/task-plan');
      savedTasks=[...saved.tasklist];
      taskOrder.set(saved.task_order);savedOrder=taskOrder.value();
      for(const[name,input]of inputs)input.checked=saved.tasklist.includes(name);loaded=true;
    }catch(error){$('actionMessage').dataset.tone='error';$('actionMessage').textContent=error.message;}
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
  function openLinkedLog(){
    if(location.hash==='#logPanel'){$('logPanel').open=true;paintLogs();}
  }
  window.addEventListener('hashchange',openLinkedLog);openLinkedLog();
  window.addEventListener('beforeunload',event=>{
    if(taskChanged()){
      event.preventDefault();event.returnValue='';
    }
  });
  $('logPanel').addEventListener('toggle',paintLogs);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){refresh(true);refreshProvider();paintLogs();}});
  async function poll(){await refresh();setTimeout(poll,document.hidden?15000:3000);}
  initialize().finally(()=>setTimeout(poll,3000));openLog();
})();
