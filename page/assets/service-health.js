(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const bar=$('serviceHealth'),button=$('healthRefresh'),stamp=$('healthStamp'),setup=$('healthSetup');
  if(!bar||!button)return;
  const items={
    network:{node:$('healthNetwork'),fallback:{ok:'可达',error:'站点异常',unknown:'未知'}},
    deepseek:{node:$('healthDeepseek'),fallback:{ok:'正常',low:'余额偏低',empty:'余额已用尽',unavailable:'暂不可用',auth:'密钥异常',error:'检查失败',unknown:'未知',unconfigured:'未配置'}},
    device:{node:$('healthDevice'),fallback:{ok:'已连接',unknown:'未知'}}
  };
  for(const item of Object.values(items))item.value=item.node?item.node.querySelector('.health-value'):null;
  let latest=null,timer=0,fast=0,pending=false;
  const clock=new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false});
  async function request(path,post){
    const response=await fetch(path,{cache:'no-store',...(post?{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}:{}),signal:AbortSignal.timeout(10000)});
    const result=await response.json();
    if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:result.message||'请求未成功，请稍后重试。');
    return result;
  }
  function render(data,failed){
    const checking=!failed&&Boolean(data?.checking);
    bar.dataset.checking=String(checking);
    button.disabled=checking;button.classList.toggle('is-busy',checking);
    button.setAttribute('aria-busy',String(checking));
    button.textContent=checking?'检查中…':'刷新';
    const view=failed?latest:(data||latest);
    for(const[key,item]of Object.entries(items)){
      if(!item.node||!item.value)continue;
      const raw=view?.[key]?.state;
      const state=typeof raw==='string'&&raw in item.fallback?raw:'unknown';
      item.node.dataset.state=state;
      const message=view?.[key]?.message;
      item.value.textContent=typeof message==='string'&&message.trim()?message:item.fallback[state];
      item.value.title=typeof message==='string'?message:'';
    }
    if(setup)setup.hidden=!(view?.deepseek&&['unconfigured','auth','low','empty'].includes(view.deepseek.state));
    const when=Date.parse(view?.checked_at||'');
    stamp.textContent=!failed&&Number.isFinite(when)?'检查于 '+clock.format(when)
      :failed&&Number.isFinite(when)?clock.format(when)+' 检查 · 暂未更新'
      :failed?'暂不可用，可重试':'';
  }
  async function load(post){
    if(pending)return;
    clearTimeout(timer);
    if(document.hidden&&!post){schedule();return;}
    pending=true;
    if(post)render({...latest,checking:true},false);
    try{latest=await request('/api/local/service-health'+(post?'/refresh':''),post);render(latest,false);}
    catch{render(null,true);}
    pending=false;
    schedule();
  }
  function schedule(){
    clearTimeout(timer);
    let wait=30000;
    if(latest?.checking&&fast<16){wait=2500;fast++;}else fast=0;
    timer=setTimeout(()=>load(false),wait);
  }
  button.addEventListener('click',()=>load(true));
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)load(false);});
  load(false);
})();
