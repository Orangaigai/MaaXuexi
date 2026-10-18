(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const names=['登录','选读文章','视听学习','本地频道','趣味答题','订阅','发表观点','每日答题'];
  const verificationTasks=new Set(['趣味答题','订阅','发表观点','每日答题']);
  const aiTasks=new Set(['趣味答题','发表观点','每日答题']);
  const labels={pending:'等待',running:'执行中',skipped_full:'已满分，跳过',skipped_mode:'按设置跳过',verified:'积分已核实',
    credited:'首局积分已领取',unavailable:'暂不可用',unverified:'积分待核实',incomplete:'尚未满分',failed:'未完成',
    stopped:'已中止',needs_human:'需本人处理',deferred:'待继续'};
  const inputs=new Map();
  let commentEnabled=false,loaded=false,busy=false,health=null,provider=null,deviceList=[];
  for(const name of names){
    const label=document.createElement('label'),input=document.createElement('input');
    label.className='task-option';input.type='checkbox';input.disabled=true;
    const copy=document.createElement('span'),title=document.createElement('span');
    copy.className='task-copy';title.textContent=name;copy.append(title);
    if(verificationTasks.has(name)){
      const badge=document.createElement('span');badge.className='verification-tag';badge.textContent='可能需验证';copy.append(badge);
    }
    label.append(input,copy);$('choices').append(label);inputs.set(name,input);
  }
  async function request(path,body){
    const response=await fetch(path,{cache:'no-store',...(body===undefined?{}:{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const result=await response.json();
    if(!response.ok||result.status==='failed')throw Error(typeof result.detail==='string'?result.detail:result.message||'请求未成功，请稍后重试。');
    return result;
  }
  const plan=()=>({tasklist:[...inputs].filter(([,input])=>input.checked).map(([name])=>name),fast_answer:false,debug:false});
  function controls(){
    const running=Boolean(health?.task_running),paused=Boolean(health?.paused),requested=Boolean(health?.pause_requested);
    for(const[name,input]of inputs)input.disabled=!loaded||busy||running||(name==='发表观点'&&!commentEnabled);
    $('start').disabled=!loaded||busy||running||!health;
    $('save').disabled=!loaded||busy||running;
    $('saveBase').disabled=!loaded||busy||running;
    $('pause').hidden=!running||paused;$('pause').disabled=busy||requested;
    $('pause').textContent=requested?'正在暂停…':'暂停';
    $('resume').hidden=!health?.can_continue;$('resume').disabled=busy||!health;
    $('stop').hidden=!running;$('stop').disabled=busy;
    $('connect').hidden=true;
    window.dispatchEvent(new CustomEvent('maa:status',{detail:health}));
  }
  async function act(path,body,message){
    busy=true;controls();
    const target=['/api/pause','/api/continue','/api/stop'].includes(path)?$('runMessage'):$('actionMessage');
    try{const result=await request(path,body);target.textContent=result.message||message;await refresh();return result;}
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
    if(result)for(const[name,input]of inputs)input.checked=result.tasklist.includes(name);
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
    const issue=providerFailure(health?.run_summary);
    $('provider').textContent=issue||(provider?.validated?'已连接':provider?.configured?'待验证':provider?'待配置':'读取中');
    $('provider').classList.toggle('ready',Boolean(provider?.validated&&!issue));
    $('provider').classList.toggle('attention',Boolean(issue||provider&&!provider.validated));
    $('providerHint').textContent=provider?.configured?'用于自动答题；开启观点时也会生成短评。':'自动答题需要先配置 DeepSeek API 密钥，也可先使用基础任务。';
    $('providerSetup').textContent=provider?.configured?'管理密钥':'配置密钥';
    $('providerSetup').classList.toggle('primary',provider?.configured===false);
  }
  async function refreshProvider(){
    try{provider=await request('/api/local/provider');renderProvider();}
    catch{$('provider').textContent='暂未连接';}
  }
  function displayReason(reason=''){
    return !health?.task_running&&health?.run_summary?.status==='needs_human'
      ?reason.replace(/；正常页面恢复后(?:会自动收尾|自动继续本轮)/g,''):reason;
  }
  function renderResults(run){
    $('results').replaceChildren();
    for(const[name,row]of Object.entries(run?.tasks||{})){
      if(!names.includes(name))continue;
      const tr=document.createElement('tr'),score=row.after||row.before;
      const mode=name==='趣味答题'&&['挑战答题','双人对战','四人赛'].includes(row.metrics?.mode)?row.metrics.mode:null;
      const values=[mode?`${name}（${mode}）`:name,score?`${score.value}/${score.maximum}`:'待核对',
        (labels[row.state]||row.state)+(row.reason?'；'+displayReason(row.reason):'')];
      for(const value of values){const td=document.createElement('td');td.textContent=value;tr.append(td);}
      $('results').append(tr);
    }
    if(!$('results').children.length){
      const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=3;td.textContent='开始执行后，这里显示任务结果。';tr.append(td);$('results').append(tr);
    }
  }
  async function refresh(){
    try{
      health=await request('/api/status');$('connection').textContent=health.connected?'模拟器已连接':'执行时自动连接';
      $('version').textContent=health.local_version;
      const run=health.run_summary;let text='选择任务后即可开始。';
      if(health.task_running){
        if(health.pause_requested)text='正在暂停，当前操作结束后将停下。';
        else if(health.paused)text=health.manual_paused?'任务已暂停，点击“继续执行”恢复。':'请在模拟器处理验证或提示，完成后会自动继续。';
        else text=run?.phase||'正在准备任务…';
      }else if(run?.status==='completed')text='本轮任务已结束，积分已核对。';
      else if(health.can_continue)text='还有任务未完成，可点击“继续执行”补做。';
      else if(health.last_error)text='任务未完成，请查看下方结果。';
      $('status').textContent=text;renderProvider();renderResults(run);
      document.title=(health.task_running?(health.paused?'[已暂停] ':'[运行中] '):health.can_continue?'[待继续] ':'')+'MaaXuexi · 任务控制台';
    }catch{health=null;$('status').textContent='后台未连接，请重新启动 MaaXuexi。';}
    controls();
  }
  async function initialize(){
    await refresh();
    try{
      const policy=await request('/api/local/comment-policy');commentEnabled=policy.enabled;
    }catch{commentEnabled=false;}
    inputs.get('发表观点').title=commentEnabled?'':'发表观点尚未授权或设置暂不可用。';
    try{
      const saved=await request('/api/local/task-plan');
      for(const[name,input]of inputs)input.checked=saved.tasklist.includes(name);loaded=true;
    }catch(error){$('actionMessage').textContent=error.message;}
    controls();await refreshProvider();
  }
  let lines=[];
  function openLog(){
    const socket=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/api/ws');
    socket.onmessage=event=>{lines.push(String(event.data));lines=lines.slice(-600);$('logs').textContent=lines.join('\n');$('logs').scrollTop=$('logs').scrollHeight;};
    socket.onclose=()=>setTimeout(openLog,4000);socket.onerror=()=>socket.close();
  }
  $('clearLog').onclick=()=>{lines=[];$('logs').textContent='';};
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){refresh();refreshProvider();}});
  initialize();setInterval(refresh,3000);openLog();
})();
