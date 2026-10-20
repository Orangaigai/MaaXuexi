(() => {
  'use strict';
  const $=id=>document.getElementById(id),form=$('preferencesForm');
  let saved=null,pending=false,health=null,loaded=false,closing=false;
  const navLinks=[...document.querySelectorAll('.settings-nav a')];
  function selectSection(){
    const current=[...navLinks].reverse().find(link=>document.querySelector(link.hash).getBoundingClientRect().top<=130)||navLinks[0];
    navLinks.forEach(link=>{if(link===current)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');});
  }
  window.addEventListener('scroll',selectSection,{passive:true});selectSection();
  const fields=()=>({window_mode:form.querySelector('[name=windowMode]:checked').value,
    show_on_attention:$('showOnAttention').checked,notifications:$('desktopNotifications').checked,
    open_console_on_schedule:$('openConsoleOnSchedule').checked});
  const same=(a,b)=>a&&b&&Object.keys(b).every(key=>a[key]===b[key]);
  function update(){
    const dirty=saved&&!same(saved,fields());
    $('preferencesState').textContent=!saved?'正在读取设置…':pending?'正在保存…':dirty?'有未保存的更改':'已保存到本机';
    $('savePreferences').disabled=!saved||pending||!dirty;
    form.querySelectorAll('input').forEach(input=>{input.disabled=!loaded||pending;});
    $('settingsAutoClose').disabled=!loaded||closing;
    $('showEmulator').disabled=pending;
  }
  function message(id,text,error=false){$(id).textContent=text;$(id).dataset.tone=error?'error':'success';}
  async function request(path,body){
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(10000),
      ...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const result=await response.json();
    if(!response.ok||result.status==='failed')throw Error(typeof result.detail==='string'?result.detail:result.message||'暂时无法保存，请稍后重试。');
    return result;
  }
  form.addEventListener('change',()=>{message('preferencesMessage','');update();});
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(pending||!loaded)return;
    const value=fields();pending=true;update();message('preferencesMessage','');
    try{const result=await request('/api/local/preferences',value);saved=value;message('preferencesMessage',result.message);}
    catch(error){message('preferencesMessage',error.message,true);}
    finally{pending=false;update();}
  });
  $('showEmulator').onclick=async()=>{
    $('showEmulator').disabled=true;
    try{await request('/api/local/window/show',{});message('preferencesMessage','已显示模拟器；点击其他窗口即可盖住。');}
    catch(error){message('preferencesMessage',error.message,true);}
    finally{update();}
  };
  $('settingsAutoClose').onchange=async()=>{
    const requested=$('settingsAutoClose').checked;closing=true;update();
    try{await request('/api/local/completion-options',{auto_close:requested});message('finishMessage',requested?'已开启，完成后自动关闭。':'已关闭，任务结束后保留窗口。');}
    catch(error){$('settingsAutoClose').checked=!requested;message('finishMessage',error.message,true);}
    finally{closing=false;update();}
  };
  window.addEventListener('beforeunload',event=>{if(pending||saved&&!same(saved,fields())){event.preventDefault();event.returnValue='';}});
  let stopped=false,timer=null;
  async function poll(){
    try{
      health=await request('/api/status');$('settingsConnection').textContent=health.task_running?'任务执行中':'后台已连接';
      $('settingsConnection').className='badge ready';
      window.dispatchEvent(new CustomEvent('maa:status',{detail:health}));
    }catch{health=null;$('settingsConnection').textContent='后台未连接';$('settingsConnection').className='badge attention';window.dispatchEvent(new CustomEvent('maa:status',{detail:null}));}
    if(!stopped)timer=setTimeout(poll,2000);
  }
  window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);});
  window.addEventListener('pageshow',event=>{if(event.persisted&&stopped){stopped=false;poll();}});
  request('/api/local/preferences').then(value=>{
    saved={window_mode:value.window_mode,show_on_attention:value.show_on_attention,notifications:value.notifications,
      open_console_on_schedule:value.open_console_on_schedule};
    form.querySelector(`[name=windowMode][value="${value.window_mode}"]`).checked=true;
    $('showOnAttention').checked=value.show_on_attention;$('desktopNotifications').checked=value.notifications;
    $('openConsoleOnSchedule').checked=value.open_console_on_schedule;
    $('settingsAutoClose').checked=value.completion.auto_close;loaded=true;update();
  }).catch(error=>{message('preferencesMessage',error.message,true);$('preferencesState').textContent='读取失败，请刷新重试';});
  request('/api/local/provider').then(value=>{$('settingsProvider').textContent=value.configured?'已配置官方 DeepSeek API 密钥。':'尚未配置，自动答题前请先添加密钥。';}).catch(()=>{$('settingsProvider').textContent='暂时无法读取，请稍后刷新。';});
  poll();
})();
