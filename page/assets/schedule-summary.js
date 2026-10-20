(() => {
  'use strict';
  const $=id=>document.getElementById(id),view=new window.MaaScheduleView($);
  let reading=false,health=null;
  async function refresh(){
    if(reading)return;reading=true;
    try{
      const response=await fetch('/api/local/schedule',{cache:'no-store',signal:AbortSignal.timeout(10000)});
      const value=await response.json();
      if(!response.ok)throw Error('计划暂时无法读取，请到设置中检查。');
      view.set(value);view.render(health);$('executionMessage').textContent='';
    }catch(error){view.fail();$('executionMessage').textContent=error.message;}
    finally{reading=false;}
  }
  window.addEventListener('maa:status',event=>{health=event.detail;view.render(health);});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  window.addEventListener('pageshow',event=>{if(event.persisted)refresh();});
  refresh();setInterval(()=>view.render(health),1000);setInterval(refresh,15000);
})();
