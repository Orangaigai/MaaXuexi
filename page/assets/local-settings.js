(() => {
  'use strict';
  const $=id=>document.getElementById(id),form=$('preferencesForm');
  let saved=null,pending=false,health=null,loaded=false,closing=false,diagnosing=false,scheduleEdited=false,scheduleBusy=false;
  const navLinks=[...document.querySelectorAll('.settings-nav a')];
  const sections=navLinks.map(link=>({link,target:document.getElementById(link.hash.slice(1))})).filter(item=>item.target);
  let clickedSection=sections.find(item=>item.link.hash===location.hash),navigating=Boolean(clickedSection),navFrame=0,scrollTimer,lastScrollY=scrollY;
  function selectSection(){
    navFrame=0;
    const maximum=Math.max(0,document.documentElement.scrollHeight-innerHeight);
    const step=Math.min(72,maximum/Math.max(1,sections.length-1));
    // Short final sections share a clamped anchor position. Reserve a small
    // scroll interval for each, so ordinary scrolling can activate all of them.
    let limit=maximum;
    const stops=sections.map(item=>Math.max(0,scrollY+item.target.getBoundingClientRect().top-Math.min(130,innerHeight/4)));
    for(let index=stops.length-1;index>=0;index--){stops[index]=Math.max(0,Math.min(stops[index],limit));limit=stops[index]-step;}
    const current=clickedSection||sections.findLast((item,index)=>scrollY>=stops[index]-1)||sections[0];
    navLinks.forEach(link=>{if(link===current?.link)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');});
  }
  const refreshSection=()=>{if(!navFrame)navFrame=requestAnimationFrame(selectSection);};
  function releaseSection(){clickedSection=null;navigating=false;refreshSection();}
  function goToSection(item){
    clickedSection=item;
    const top=scrollY+item.target.getBoundingClientRect().top-parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop||0);
    const destination=Math.max(0,Math.min(top,document.documentElement.scrollHeight-innerHeight));
    navigating=Math.abs(scrollY-destination)>2;
    item.target.scrollIntoView({block:'start'});
    selectSection();
  }
  navLinks.forEach(link=>link.addEventListener('click',event=>{
    if(event.button||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    const item=sections.find(item=>item.link===link);if(!item)return;
    event.preventDefault();
    if(location.hash!==link.hash)history.pushState(null,'',link.hash);
    goToSection(item);
  }));
  window.addEventListener('scroll',()=>{
    if(!navigating&&Math.abs(scrollY-lastScrollY)>1)clickedSection=null;
    lastScrollY=scrollY;
    refreshSection();
    // Older browsers without scrollend still release smooth-scroll ownership.
    if(!('onscrollend' in document)){clearTimeout(scrollTimer);scrollTimer=setTimeout(()=>{navigating=false;},150);}
  },{passive:true});
  document.addEventListener('scrollend',()=>{navigating=false;});
  for(const type of ['wheel','touchmove'])window.addEventListener(type,releaseSection,{passive:true});
  window.addEventListener('keydown',event=>{
    if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(event.key)&&!event.target.closest('input,textarea,select,button,[contenteditable="true"]'))releaseSection();
  });
  window.addEventListener('resize',releaseSection);
  window.addEventListener('hashchange',()=>{const item=sections.find(item=>item.link.hash===location.hash);if(item)goToSection(item);else releaseSection();});
  window.addEventListener('load',()=>{const item=sections.find(item=>item.link.hash===location.hash);if(item)goToSection(item);});
  new ResizeObserver(refreshSection).observe(document.querySelector('.settings-content'));
  selectSection();
  const fields=()=>({window_mode:form.querySelector('[name=windowMode]:checked').value,
    show_on_attention:$('showOnAttention').checked,notifications:$('desktopNotifications').checked,
    open_console_on_schedule:$('openConsoleOnSchedule').checked});
  const same=(a,b)=>a&&b&&Object.keys(b).every(key=>a[key]===b[key]);
  function update(){
    const dirty=saved&&!same(saved,fields());
    $('preferencesState').textContent=!saved?'正在读取设置…':pending?'正在保存…':dirty?'有未保存的更改':'已保存到本机';
    $('savePreferences').disabled=!saved||pending||!dirty;
    form.querySelectorAll('input').forEach(input=>{input.disabled=!loaded||pending;});
    for(const id of ['settingsCloseEmulator','settingsCloseConsole'])$(id).disabled=!loaded||closing;
    $('showEmulator').disabled=pending;
    $('diagnose').disabled=diagnosing||!health||Boolean(health.task_running||health.history_import?.active);
  }
  function message(id,text,error=false){$(id).textContent=text;$(id).dataset.tone=error?'error':'success';}
  async function request(path,body){
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(path.endsWith('/diagnostics')?25000:10000),
      ...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const result=await response.json();
    if(!response.ok||result.status==='failed')throw Error(typeof result.detail==='string'?result.detail:result.message||'暂时无法处理，请稍后重试。');
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
  const closeFields=()=>({close_emulator:$('settingsCloseEmulator').checked,close_console:$('settingsCloseConsole').checked});
  function showCloseOptions(value){
    $('settingsCloseEmulator').checked=value.close_emulator??value.auto_close;
    $('settingsCloseConsole').checked=value.close_console??value.auto_close;
  }
  for(const id of ['settingsCloseEmulator','settingsCloseConsole'])$(id).onchange=async()=>{
    const previous=!$(id).checked;closing=true;update();
    try{showCloseOptions(await request('/api/local/completion-options',closeFields()));message('finishMessage','收尾设置已保存。');}
    catch(error){$(id).checked=previous;message('finishMessage',error.message,true);}
    finally{closing=false;update();}
  };
  window.addEventListener('beforeunload',event=>{if(pending||closing||scheduleEdited||scheduleBusy||saved&&!same(saved,fields())){event.preventDefault();event.returnValue='';}});
  window.addEventListener('maa:schedule-edit',event=>{scheduleEdited=event.detail.dirty;scheduleBusy=event.detail.busy;});
  $('diagnose').onclick=async()=>{
    if(diagnosing||!health||health.task_running||health.history_import?.active)return;
    diagnosing=true;update();message('diagnosticsMessage','正在检查…');
    try{
      const result=await request('/api/local/diagnostics');$('diagnosticsResults').replaceChildren();
      const labels={ok:'正常',warn:'提示',error:'需处理'};
      for(const row of result.checks){
        const item=document.createElement('li'),name=document.createElement('span'),detail=document.createElement('span');
        name.textContent=row.name;detail.textContent=`${labels[row.state]} · ${row.message}`;
        detail.className=row.state;item.append(name,detail);$('diagnosticsResults').append(item);
      }
      message('diagnosticsMessage',result.summary.error?`检查完成，${result.summary.error}项需要处理。`:'检查完成。',Boolean(result.summary.error));
    }catch(error){message('diagnosticsMessage',error.message,true);}
    finally{diagnosing=false;update();}
  };
  let stopped=false,timer=null;
  async function refreshCompletion(){
    if(closing)return;
    try{const result=await request('/api/local/completion-options');if(!closing)showCloseOptions(result);}catch{}
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshCompletion();});
  async function poll(){
    try{
      health=await request('/api/status');$('settingsConnection').textContent=health.task_running?'任务执行中':'后台已连接';
      $('settingsConnection').className='badge ready';$('settingsVersion').textContent=health.local_version;
      window.dispatchEvent(new CustomEvent('maa:status',{detail:health}));
    }catch{health=null;$('settingsConnection').textContent='后台未连接';$('settingsConnection').className='badge attention';window.dispatchEvent(new CustomEvent('maa:status',{detail:null}));}
    update();if(!stopped)timer=setTimeout(poll,document.hidden?15000:2000);
  }
  window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);});
  window.addEventListener('pageshow',event=>{if(event.persisted&&stopped){stopped=false;poll();refreshCompletion();}});
  request('/api/local/preferences').then(value=>{
    saved={window_mode:value.window_mode,show_on_attention:value.show_on_attention,notifications:value.notifications,
      open_console_on_schedule:value.open_console_on_schedule};
    form.querySelector(`[name=windowMode][value="${value.window_mode}"]`).checked=true;
    $('showOnAttention').checked=value.show_on_attention;$('desktopNotifications').checked=value.notifications;
    $('openConsoleOnSchedule').checked=value.open_console_on_schedule;
    showCloseOptions(value.completion);loaded=true;update();
  }).catch(error=>{message('preferencesMessage',error.message,true);$('preferencesState').textContent='读取失败，请刷新重试';});
  request('/api/local/provider').then(value=>{$('settingsProvider').textContent=value.validated?'已配置，最近一次连接验证通过。':value.configured?'已配置密钥，尚待验证连接。':'尚未配置，自动答题前请先添加密钥。';$('settingsProviderSetup').textContent=value.configured?'管理密钥':'配置密钥';}).catch(()=>{$('settingsProvider').textContent='暂时无法读取，请稍后刷新。';});
  poll();
})();
