(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const names=['登录','选读文章','视听学习','本地频道','趣味答题','订阅','发表观点','每日答题'];
  const states={completed:'已完成',running:'执行中',partial:'待补做',needs_human:'待本人处理',failed:'待处理',stopped:'已中止',observed:'已记录'};
  const results={pending:'待执行',running:'执行中',skipped_full:'已满分',skipped_mode:'已跳过',verified:'已核实',
    credited:'已领取',unavailable:'暂不可用',unverified:'待核实',incomplete:'待补做',failed:'未完成',
    stopped:'已中止',needs_human:'待本人处理',deferred:'待继续'};
  const done=new Set(['skipped_full','verified','credited']);
  let month='',selected='',data=null,reading=false,revision=0,lastRun='',lastAction=null,saving=false,optionsLoaded=false;
  async function request(path,body){
    const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(10000),
      ...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const value=await response.json();
    if(!response.ok||value.status==='failed')throw Error(value.detail||value.message||'暂时无法读取，请稍后重试。');
    return value;
  }
  const formatPoints=value=>value===null||value===undefined?'—':value+'分';
  function detail(){
    const row=data?.days.find(item=>item.day===selected);
    const parts=selected.split('-');
    $('pointsDayTitle').textContent=parts.length===3?`${Number(parts[1])}月${Number(parts[2])}日`:'选择日期';
    $('pointsDayState').textContent=row?states[row.status]||'已记录':'暂无记录';
    $('pointsDayState').className='badge'+(row?.status==='completed'?' ready':row&&!['observed','running'].includes(row.status)?' attention':'');
    $('pointsDayHint').textContent=!row?'这天还没有积分记录。':row.points===null?'尚未读到积分，已保留运行结果。':
      row.points_source==='xuexi_points_detail'?`已核实 ${row.points} 分 · 平台历史记录`+(row.known_tasks?'':'，未导入逐项明细。'):
      `已核实 ${row.points} 分`+(row.complete_snapshot?'':` · ${row.known_tasks}项任务`);
    $('pointsTaskDetails').replaceChildren();
    for(const name of names){
      const score=row?.tasks[name],result=row?.results?.[name];if(!score&&!result)continue;
      const group=document.createElement('div'),term=document.createElement('dt'),value=document.createElement('dd');
      term.textContent=name;
      if(result?.mode){const mode=document.createElement('small');mode.textContent=result.mode;term.append(mode);}
      value.className='points-task-score';value.textContent=score?`${score.value}/${score.maximum}`:'—';
      const state=document.createElement('dd');state.className='points-task-state';state.textContent=results[result?.state]||(score?'已读取':'待核实');
      state.classList.toggle('is-done',done.has(result?.state));
      state.classList.toggle('is-attention',Boolean(result&&!done.has(result.state)&&!['pending','running','skipped_mode'].includes(result.state)));
      group.append(term,value,state);
      if(result?.reason){
        const reason=document.createElement('dd');reason.className='points-task-reason';
        reason.textContent=row.status==='needs_human'?result.reason.replace(/；正常页面恢复后(?:会自动收尾|自动继续本轮)/g,''):result.reason;
        group.append(reason);
      }
      $('pointsTaskDetails').append(group);
    }
  }
  function render(){
    const [year,number]=data.month.split('-').map(Number);
    $('pointsMonth').textContent=`${year}年${number}月`;
    $('todayPoints').textContent=formatPoints(data.today_points);$('monthPoints').textContent=formatPoints(data.month_points);
    $('pointsNext').disabled=data.month>=data.today.slice(0,7);$('pointsPrevious').disabled=year<=1970&&number===1;
    const byDay=new Map(data.days.map(row=>[row.day,row]));
    const first=(new Date(Date.UTC(year,number-1,1)).getUTCDay()+6)%7;
    const count=new Date(Date.UTC(year,number,0)).getUTCDate();
    $('pointsDays').replaceChildren();
    for(let i=0;i<first;i++){const gap=document.createElement('span');gap.className='calendar-gap';gap.setAttribute('aria-hidden','true');$('pointsDays').append(gap);}
    for(let day=1;day<=count;day++){
      const key=`${data.month}-${String(day).padStart(2,'0')}`,row=byDay.get(key);
      const button=document.createElement('button');button.type='button';button.className='calendar-day';
      button.classList.toggle('has-record',Boolean(row));button.classList.toggle('is-today',key===data.today);
      button.classList.toggle('is-selected',key===selected);
      button.classList.toggle('needs-attention',Boolean(row&&!['completed','observed','running'].includes(row.status)));
      button.disabled=key>data.today;button.setAttribute('aria-pressed',String(key===selected));
      button.setAttribute('aria-label',`${year}年${number}月${day}日，${row?formatPoints(row.points)+'，'+(states[row.status]||'已记录'):'暂无记录'}`);
      const label=document.createElement('span'),score=document.createElement('strong');label.textContent=day;
      score.textContent=row?formatPoints(row.points):'';button.append(label,score);
      button.onclick=()=>{selected=key;render();$('pointsDays').querySelector('[aria-pressed="true"]').focus({preventScroll:true});};$('pointsDays').append(button);
    }
    detail();
  }
  async function load(){
    const ticket=++revision;reading=true;
    try{
      const result=await request('/api/local/points-history'+(month?'?month='+encodeURIComponent(month):''));
      if(ticket!==revision)return;
      data=result;month=result.month;
      if(!selected.startsWith(month))selected=result.today.startsWith(month)?result.today:result.days.at(-1)?.day||month+'-01';
      $('pointsMessage').textContent='';render();
    }catch(error){if(ticket===revision)$('pointsMessage').textContent=error.message;}
    finally{if(ticket===revision)reading=false;}
  }
  function move(amount){
    if(!month)return;const [year,number]=month.split('-').map(Number),date=new Date(Date.UTC(year,number-1+amount,1));
    month=date.toISOString().slice(0,7);selected='';load();
  }
  $('pointsPrevious').onclick=()=>move(-1);$('pointsNext').onclick=()=>move(1);
  $('pointsCurrent').onclick=()=>{month='';selected='';load();};
  $('autoClose').onchange=async()=>{
    const chosen=$('autoClose').checked;if(saving)return;saving=true;$('autoClose').disabled=true;
    $('completionSettingMessage').dataset.tone='info';$('completionSettingMessage').textContent='保存中…';
    try{const result=await request('/api/local/completion-options',{auto_close:chosen});$('autoClose').checked=result.auto_close;$('completionSettingMessage').dataset.tone='success';$('completionSettingMessage').textContent='已保存';}
    catch(error){$('autoClose').checked=!chosen;$('completionSettingMessage').dataset.tone='error';$('completionSettingMessage').textContent=error.message;}
    finally{saving=false;$('autoClose').disabled=lastAction?.state==='closing';}
  };
  $('keepOpen').onclick=async()=>{
    $('keepOpen').disabled=true;
    try{await request('/api/local/completion/cancel',{});lastAction={state:'cancelled'};completion();}
    catch(error){$('completionMessage').textContent=error.message;}
    finally{$('keepOpen').disabled=false;}
  };
  function completion(){
    const state=lastAction?.state;$('completionBanner').hidden=!['pending','closing','failed','cancelled'].includes(state);
    $('keepOpen').hidden=state!=='pending';
    if(state==='pending'){
      const seconds=Math.max(0,Math.ceil((Date.parse(lastAction.closes_at)-Date.now())/1000));
      $('completionMessage').textContent=`积分已记录，${seconds}秒后自动关闭。`;
    }else if(state==='closing')$('completionMessage').textContent='积分已记录，正在关闭控制台和模拟器…';
    else if(state==='failed')$('completionMessage').textContent=lastAction.message;
    else if(state==='cancelled')$('completionMessage').textContent='积分已记录，本次保持打开。';
  }
  window.addEventListener('maa:status',event=>{
    const health=event.detail;
    if(!health){
      if(lastAction?.state==='closing'){$('status').textContent='任务已完成，积分记录已保存。查看记录时重新打开 MaaXuexi 即可。';$('connection').textContent='后台已关闭';$('connection').dataset.state='closed';$('runtimeCard').classList.remove('is-offline');}
      return;
    }
    lastAction=health.completion_action||null;completion();
    $('autoClose').disabled=saving||!optionsLoaded||lastAction?.state==='closing';
    const run=health.run_summary,key=JSON.stringify([run?.run_id,run?.status,
      Object.entries(run?.tasks||{}).map(([name,row])=>[name,row.state,row.after?.value,row.reason])]);
    if(key!==lastRun&&!reading){lastRun=key;load();}
  });
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!reading)load();});
  window.addEventListener('maa:points-changed',()=>load());
  request('/api/local/completion-options').then(result=>{optionsLoaded=true;$('autoClose').checked=result.auto_close;$('autoClose').disabled=lastAction?.state==='closing';})
    .catch(error=>{$('completionSettingMessage').textContent=error.message;});
  load();setInterval(()=>{if(!document.hidden)completion();},1000);
})();
