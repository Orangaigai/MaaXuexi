(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const execution=[...document.querySelectorAll('input[name="executionMode"]')];
  const radios=[...document.querySelectorAll('input[name="scheduleMode"]')];
  const times=[$('fixedTime'),$('windowStart'),$('windowEnd')];
  let health=null,saved=null,observedAt=0,loaded=false,busy=false,dirty=false,reading=false,revision=0;
  const mode=()=>radios.find(input=>input.checked).value;
  const automatic=()=>execution.find(input=>input.checked)?.value==='automatic';
  async function request(path,body){
    const response=await fetch(path,{cache:'no-store',...(body===undefined?{}:{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const result=await response.json();
    if(!response.ok||result.status==='failed'){
      const detail=Array.isArray(result.detail)?result.detail.map(item=>item.msg).join('；'):result.detail;
      throw Error(typeof detail==='string'?detail:result.message||'请求失败，请稍后重试。');
    }
    return result;
  }
  function controls(){
    for(const input of execution)input.disabled=!loaded||busy;
    const active=automatic();
    $('schedulePanel').hidden=!loaded||!active;$('manualPanel').hidden=!loaded||active;
    for(const input of radios)input.disabled=!loaded||busy||!active;
    $('fixedTime').disabled=busy||!active||mode()!=='fixed';
    $('windowStart').disabled=$('windowEnd').disabled=busy||!active||mode()!=='random_window';
    $('scheduleSave').disabled=!loaded||busy||!active||!health;
    $('scheduleSave').textContent=busy?'正在处理…':'保存定时';
    $('scheduleEditState').textContent=dirty?'有修改，尚未保存':loaded?'已保存':'读取中';
    $('scheduleEditState').classList.toggle('unsaved',dirty);
  }
  function edit(){
    dirty=true;$('windowEnd').setCustomValidity('');$('scheduleMessage').textContent='';controls();
  }
  for(const radio of radios)radio.addEventListener('change',edit);
  for(const input of times)input.addEventListener('input',edit);
  function countdown(){
    if(!saved?.enabled||saved.seconds_until_next_run==null){
      $('scheduleCountdown').textContent=saved?.enabled===false?'定时已关闭':'暂未读到计划';return;
    }
    const remaining=Math.max(0,Math.ceil(saved.seconds_until_next_run-(performance.now()-observedAt)/1000));
    if(!remaining){$('scheduleCountdown').textContent=health?.task_running?'任务执行中，正在更新下一次时间':'计划时间已到，等待系统启动';return;}
    const days=Math.floor(remaining/86400),hours=Math.floor(remaining%86400/3600);
    const minutes=Math.floor(remaining%3600/60),seconds=remaining%60;
    $('scheduleCountdown').textContent=`${days?days+'天 ':''}${hours}小时 ${minutes}分 ${seconds}秒后`;
  }
  function display(plan,fill=false){
    if(!plan.available)throw Error(plan.message||'定时状态未能读取。');
    saved=plan;observedAt=performance.now();
    for(const input of execution)input.checked=input.value===(plan.enabled?'automatic':'manual');
    if(!loaded||fill||!dirty){
      for(const radio of radios)radio.checked=radio.value===plan.mode;
      $('fixedTime').value=plan.time;$('windowStart').value=plan.window_start;$('windowEnd').value=plan.window_end;
      dirty=false;
    }
    loaded=true;$('runMode').textContent=plan.enabled?'定时自动执行':'手动执行';
    const timing=plan.mode==='random_window'?`每天 ${plan.window_start}～${plan.window_end} 内随机启动一次`:`每天 ${plan.time} 启动`;
    $('schedule').textContent=plan.enabled?`已保存：${timing}。`:'定时启动已关闭，时间设置保留。';
    const next=plan.enabled&&plan.next_run_at?new Date(plan.next_run_at):null;
    $('scheduleNextTime').textContent=next&&Number.isFinite(next.getTime())
      ?'预计 '+new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(next):'';
    $('scheduleCatchup').textContent='电脑需开机、登录 Windows、未休眠且联网；关闭网页不停止任务。'+
      (plan.start_when_available?'错过后恢复会自动补跑，可能超出所设时段。':'错过后不补跑。');
    countdown();controls();
  }
  async function refreshSchedule(){
    if(reading||busy)return;
    reading=true;const ticket=++revision;
    try{const plan=await request('/api/local/schedule');if(ticket===revision)display(plan);}
    catch(error){if(ticket===revision){$('executionMessage').textContent=error.message;if(!loaded)$('runMode').textContent='执行方式暂未读到';}}
    finally{reading=false;controls();}
  }
  for(const input of execution)input.addEventListener('change',async()=>{
    if(busy||!loaded)return;
    const chosen=input.value;busy=true;++revision;controls();$('executionMessage').textContent='正在切换执行方式…';
    try{const result=await request('/api/local/execution-mode',{mode:chosen});display(result);$('executionMessage').textContent=result.message;}
    catch(error){
      for(const radio of execution)radio.checked=radio.value===(saved.enabled?'automatic':'manual');
      $('executionMessage').textContent=error.message;
    }finally{busy=false;controls();}
  });
  $('scheduleSave').addEventListener('click',async()=>{
    if(busy||!automatic())return;
    $('windowEnd').setCustomValidity('');
    if(mode()==='random_window'&&$('windowEnd').value<=$('windowStart').value)
      $('windowEnd').setCustomValidity('结束时间须晚于开始时间，请设置同一天内的时间段。');
    if(!times.filter(input=>!input.disabled).every(input=>input.reportValidity()))return;
    busy=true;++revision;controls();
    try{
      const result=await request('/api/local/schedule',{mode:mode(),time:$('fixedTime').value,
        window_start:$('windowStart').value,window_end:$('windowEnd').value});
      display(result,true);$('scheduleMessage').textContent=result.message;health=await request('/api/status');
    }catch(error){$('scheduleMessage').textContent=error.message;}
    finally{busy=false;controls();}
  });
  window.addEventListener('maa:status',event=>{health=event.detail;controls();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshSchedule();});
  request('/api/status').then(value=>{health=value;controls();}).catch(()=>{});
  controls();refreshSchedule();setInterval(countdown,1000);setInterval(refreshSchedule,15000);
})();
