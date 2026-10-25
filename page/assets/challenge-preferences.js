/* Single-player challenge tier picker in settings, plus the console preference summary. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const slider=$('challengeProfile'),limitInput=$('challengeLimit'),saveButton=$('challengeSave');
  const summary=$('challengeSummary'),summaryName=$('challengeSummaryName');
  if(!slider&&!summary)return;
  let saved=null,busy=false,loaded=false,profiles=[];
  const names=['beginner','practiced','knowledgeable','adept','master'];
  async function request(path,body){
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(10000),...(body===undefined?{}:{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const result=await response.json();
    if(!response.ok||result.status==='failed'){
      const detail=Array.isArray(result.detail)?result.detail.map(item=>item.msg).join('；'):result.detail;
      throw Error(typeof detail==='string'?detail:result.message||'暂时无法处理，请稍后重试。');
    }
    return result;
  }
  const label=name=>profiles.find(row=>row.name===name)?.label||name;
  function showMessage(text,error=false){
    const output=$('challengeMessage');
    if(!output)return;
    output.textContent=text;output.dataset.tone=error?'error':'success';
  }
  /* Console: one neutral line about the saved preference, never a claim about today's mode. */
  if(summary){
    request('/api/local/challenge-preferences').then(value=>{
      profiles=value.profiles||[];
      summaryName.textContent=label(value.profile);
      summary.hidden=false;
    }).catch(()=>{});
  }
  if(!slider)return;
  const ticks=[...document.querySelectorAll('#challengeTicks [data-profile]')];
  const details=$('challengeDetails'),state=$('challengeEditState');
  function limitValue(){
    const text=limitInput.value.trim();
    return /^\d+$/.test(text)?Number(text):NaN;
  }
  const draft=()=>({profile:names[slider.valueAsNumber]||'beginner',limit:limitValue()});
  const edited=()=>Boolean(saved&&(saved.profile!==draft().profile||saved.limit!==draft().limit));
  function renderDetails(rows){
    if(!details)return;
    const cells=rows.map(row=>{
      const item=document.createElement('div'),name=document.createElement('dt'),value=document.createElement('dd');
      name.textContent=`${row.label} · 中心${row.center}`;
      value.textContent=`计划约 ${row.mean.toFixed(1)} 题`;
      item.append(name,value);
      return item;
    });
    details.replaceChildren(...cells);
  }
  function controls(){
    const dirty=loaded&&edited();
    slider.disabled=limitInput.disabled=!loaded||busy;
    const current=profiles.find(row=>row.name===names[slider.valueAsNumber]);
    slider.setAttribute('aria-valuetext',current?`${current.label}，分布中心${current.center}题，计划均值${current.mean.toFixed(1)}题`:'正在读取');
    slider.style.setProperty('--challenge-fill',`${slider.valueAsNumber*25}%`);
    for(const tick of ticks){
      tick.disabled=!loaded||busy;
      tick.setAttribute('aria-pressed',String(names[slider.valueAsNumber]===tick.dataset.profile));
    }
    saveButton.disabled=!loaded||busy||!dirty;
    if(state){
      state.textContent=!loaded?'读取中':busy?'正在保存…':dirty?'有修改，尚未保存':'已保存';
      state.classList.toggle('unsaved',dirty&&!busy);
    }
  }
  function render(value){
    profiles=value.profiles||[];
    slider.value=names.indexOf(value.profile);
    limitInput.value=value.limit;
    renderDetails(profiles);
    saved={profile:value.profile,limit:value.limit};
    loaded=true;
    controls();
  }
  for(const tick of ticks)tick.addEventListener('click',()=>{
    if(busy||!loaded)return;
    slider.value=names.indexOf(tick.dataset.profile);
    showMessage('');
    controls();
  });
  slider.addEventListener('input',()=>{showMessage('');controls();});
  limitInput.addEventListener('input',()=>{showMessage('');controls();});
  saveButton.addEventListener('click',async()=>{
    if(busy||!loaded)return;
    const value=draft();
    if(!Number.isInteger(value.limit)||value.limit<20||value.limit>100){
      showMessage('题数上限需为20～100之间的整数，默认50。',true);
      limitInput.reportValidity();
      return;
    }
    busy=true;controls();showMessage('');
    try{
      const result=await request('/api/local/challenge-preferences',value);
      render(result);showMessage(result.message);
    }catch(error){showMessage(error.message,true);}
    finally{busy=false;controls();}
  });
  window.addEventListener('beforeunload',event=>{
    if(busy||(saved&&edited())){event.preventDefault();event.returnValue='';}
  });
  request('/api/local/challenge-preferences').then(render).catch(()=>showMessage('单人挑战设置读取失败，请刷新重试。',true));
  controls();
})();
