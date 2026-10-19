(() => {
  'use strict';
  const $=id=>document.getElementById(id),panel=$('historyImport');
  const states={preparing:'正在准备导入',reading:'正在读取历史积分',checking:'正在核对积分记录',waiting:'需要本人处理',saving:'正在保存积分记录',completed:'历史积分已导入',partial:'部分记录已导入',failed:'历史积分暂未导入',cancelled:'已取消导入',interrupted:'上次导入已中断'};
  let data=null,health=null,pending=false,watched='',notified='',localError='';
  async function request(path,body){
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(10000),
      ...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const result=await response.json();
    if(!response.ok||result.status==='failed')throw Error(result.detail||result.message||'暂时无法处理，请稍后重试。');
    return result;
  }
  function render(){
    if(!data)return;
    const job=data.job,active=Boolean(data.active),terminal=job&&['completed','partial','failed','cancelled','interrupted'].includes(job.phase);
    if(active)watched=job.id;
    panel.hidden=!data.show_onboarding&&!active&&!(terminal&&watched===job.id);
    panel.classList.toggle('is-result',Boolean(terminal));
    $('historyImportTitle').textContent=active||terminal?states[job.phase]||'历史积分导入':'补全你的积分日历';
    $('historyImportDescription').textContent=active?job.message:job?.phase==='completed'||job?.phase==='partial'?
      `${job.date_from} — ${job.date_to} · 新增 ${job.counts.added} 天，更新 ${job.counts.updated} 天，已有 ${job.counts.unchanged} 天。`:
      '导入学习强国可查询的历史每日积分，直观了解近期学习情况。';
    $('historyImportHint').hidden=active||job?.phase==='completed';
    const message=$('historyImportMessage');message.dataset.tone=job?.phase==='completed'?'success':terminal?'info':'info';
    message.textContent=active?(job.read_days?`已读取 ${job.read_days} 天 · 记录只保存在本机。`:'记录只保存在本机，不执行积分任务。'):
      job?.phase==='partial'?`${job.unconfirmed_days} 天尚未核实，可重新导入补齐。`:
      job?.phase==='completed'?'已保存在本机，点击日期查看分数。':terminal?job.message:'';
    const meter=$('historyImportProgress');meter.hidden=!active;meter.removeAttribute('value');
    $('historyImportSkip').hidden=!data.show_onboarding||active;
    $('historyImportSkip').disabled=pending;
    $('historyImportStart').hidden=Boolean(terminal&&job.phase==='completed');
    $('historyImportStart').disabled=pending||active||Boolean(health?.task_running)||!health;
    $('historyImportStart').textContent=active?'正在导入…':terminal?'重新导入':'立即导入';
    $('historyImportStart').classList.toggle('is-busy',active||pending);
    $('historyImportStart').setAttribute('aria-busy',String(active||pending));
    $('historyImportCancel').hidden=!active;
    $('historyImportCancel').disabled=pending||Boolean(job?.cancel_requested)||job?.phase==='saving';
    if(!active&&health?.task_running&&data.show_onboarding)message.textContent='学习任务执行中，结束后即可导入历史积分。';
    if(localError){message.dataset.tone='error';message.textContent=localError;}
    if(terminal&&['completed','partial'].includes(job.phase)&&watched===job.id&&notified!==job.id){
      notified=job.id;window.dispatchEvent(new CustomEvent('maa:points-changed'));
    }
  }
  async function act(path){
    pending=true;localError='';render();
    try{
      data=await request(path,{});if(data.job)watched=data.job.id;
      window.dispatchEvent(new CustomEvent('maa:history-import',{detail:data}));
    }catch(error){localError=error.message;}
    finally{pending=false;render();}
  }
  $('historyImportStart').onclick=()=>act('/api/local/history-import');
  $('historyImportSkip').onclick=()=>act('/api/local/history-import/skip');
  $('historyImportCancel').onclick=()=>act('/api/local/history-import/cancel');
  window.addEventListener('maa:status',event=>{
    health=event.detail;
    if(health?.history_import){data=health.history_import;render();}
    else if(!health&&data?.active){
      $('historyImportMessage').dataset.tone='error';$('historyImportMessage').textContent='暂时无法连接后台，恢复连接后会更新导入结果。';
      $('historyImportCancel').disabled=true;$('historyImportStart').disabled=true;
    }else render();
  });
  request('/api/local/history-import').then(result=>{data=result;render();})
    .catch(error=>{$('historyImportMessage').textContent=error.message;});
})();
