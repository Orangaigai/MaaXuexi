(() => {
  'use strict';
  const $=id=>document.getElementById(id),view=new window.MaaScheduleView($);
  const execution=[...document.querySelectorAll('input[name="executionMode"]')];
  const radios=[...document.querySelectorAll('input[name="scheduleMode"]')];
  const times=[$('fixedTime'),$('windowStart'),$('windowEnd')];
  let health=null,saved=null,loaded=false,busy=false,dirty=false,reading=false,readFailed=false,revision=0,pendingAction='';
  const mode=()=>radios.find(input=>input.checked).value;
  const automatic=()=>execution.find(input=>input.checked)?.value==='automatic';
  const draft=()=>({mode:mode(),time:$('fixedTime').value,window_start:$('windowStart').value,window_end:$('windowEnd').value});
  const edited=()=>Boolean(saved&&Object.entries(draft()).some(([name,value])=>saved[name]!==value));
  function fillFields(plan){
    for(const radio of radios)radio.checked=radio.value===plan.mode;
    $('fixedTime').value=plan.time;$('windowStart').value=plan.window_start;$('windowEnd').value=plan.window_end;
  }
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
    for(const input of execution)input.disabled=!loaded||busy||!health;
    const active=automatic();
    $('schedulePanel').hidden=!loaded||!active;$('manualPanel').hidden=!loaded||active;
    for(const input of radios)input.disabled=!loaded||busy||!active;
    $('fixedTime').disabled=busy||!active||mode()!=='fixed';
    $('windowStart').disabled=$('windowEnd').disabled=busy||!active||mode()!=='random_window';
    $('scheduleSave').disabled=!loaded||busy||!active||!health;
    $('scheduleSave').textContent=pendingAction==='save'?'保存中…':'保存定时';
    $('scheduleSave').classList.toggle('is-busy',pendingAction==='save');
    $('scheduleSave').setAttribute('aria-busy',String(pendingAction==='save'));
    document.querySelector('.execution-choices').setAttribute('aria-busy',String(pendingAction==='mode'));
    $('scheduleEditState').textContent=dirty?'有修改，尚未保存':loaded?'已保存':'读取中';
    $('scheduleEditState').classList.toggle('unsaved',dirty);
    $('scheduleReset').hidden=!dirty;$('scheduleReset').disabled=!dirty||busy||!active;
    $('scheduleNextLabel').textContent=dirty?'当前计划的下次执行':'距离下次执行';
    window.dispatchEvent(new CustomEvent('maa:schedule-edit',{detail:{dirty,busy}}));
  }
  function edit(){
    dirty=edited();$('windowEnd').setCustomValidity('');$('scheduleMessage').textContent='';controls();
  }
  $('scheduleReset').onclick=()=>{
    fillFields(saved);dirty=false;$('windowEnd').setCustomValidity('');$('scheduleMessage').textContent='';controls();
    radios.find(input=>input.checked).focus();
  };
  for(const radio of radios)radio.addEventListener('change',edit);
  for(const input of times)input.addEventListener('input',edit);
  function countdown(){view.render(health);}
  function display(plan,fill=false){
    view.set(plan);saved=plan;
    for(const input of execution)input.checked=input.value===(plan.enabled?'automatic':'manual');
    if(!loaded||fill||!dirty){
      fillFields(plan);
    }
    dirty=edited();
    loaded=true;
    $('scheduleCatchup').textContent='电脑需开机、登录 Windows、未休眠且联网；关闭网页不停止任务。'+
      (plan.start_when_available?'错过后恢复会自动补跑，可能超出所设时段。':'错过后不补跑。');
    countdown();controls();
  }
  async function refreshSchedule(){
    if(reading||busy)return;
    reading=true;const ticket=++revision;
    try{const plan=await request('/api/local/schedule');if(ticket===revision){display(plan);if(readFailed){$('executionMessage').textContent='';readFailed=false;}}}
    catch(error){if(ticket===revision){readFailed=true;$('executionMessage').dataset.tone='error';$('executionMessage').textContent=error.message;view.fail();}}
    finally{reading=false;controls();}
  }
  for(const input of execution)input.addEventListener('change',async()=>{
    if(busy||!loaded)return;
    const chosen=input.value;busy=true;pendingAction='mode';++revision;controls();$('executionMessage').dataset.tone='info';$('executionMessage').textContent='正在切换执行方式…';
    try{const result=await request('/api/local/execution-mode',{mode:chosen});display(result);$('executionMessage').dataset.tone='success';$('executionMessage').textContent=result.message;}
    catch(error){
      for(const radio of execution)radio.checked=radio.value===(saved.enabled?'automatic':'manual');
      $('executionMessage').dataset.tone='error';$('executionMessage').textContent=error.message;
    }finally{busy=false;pendingAction='';controls();}
  });
  $('scheduleSave').addEventListener('click',async()=>{
    if(busy||!automatic())return;
    $('windowEnd').setCustomValidity('');
    if(mode()==='random_window'&&$('windowEnd').value<=$('windowStart').value)
      $('windowEnd').setCustomValidity('结束时间须晚于开始时间，请设置同一天内的时间段。');
    if(!times.filter(input=>!input.disabled).every(input=>input.reportValidity()))return;
    busy=true;pendingAction='save';++revision;controls();$('scheduleMessage').textContent='';
    try{
      const result=await request('/api/local/schedule',draft());
      display(result,true);$('scheduleMessage').dataset.tone='success';$('scheduleMessage').textContent=result.message;health=await request('/api/status');
    }catch(error){$('scheduleMessage').dataset.tone='error';$('scheduleMessage').textContent=error.message;}
    finally{busy=false;pendingAction='';controls();}
  });
  window.addEventListener('maa:status',event=>{health=event.detail;controls();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshSchedule();});
  window.addEventListener('pageshow',event=>{if(event.persisted)refreshSchedule();});
  request('/api/status').then(value=>{health=value;controls();}).catch(()=>{});
  controls();refreshSchedule();setInterval(countdown,1000);setInterval(refreshSchedule,15000);
})();
