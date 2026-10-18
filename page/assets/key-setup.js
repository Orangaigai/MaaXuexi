(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const dialog=$('keyHelp'),field=$('key'),result=$('result');
  const steps=[
    {title:'登录 DeepSeek 开放平台',text:'打开官方开放平台并登录。需要使用的是 API 服务入口，而不是普通聊天页面。',
      link:'https://platform.deepseek.com/',label:'打开官方开放平台',tip:'如果官网要求注册或认证，请按照官网提示由你本人完成。'},
    {title:'先小额充值试用',text:'API 按调用量计费，自动答题通常开销较少。先按需小额充值即可，实际费用以官网账单为准。',
      link:'https://platform.deepseek.com/balance',label:'查看余额与充值入口',tip:'充值的是 API 账户余额。充值金额、支付方式及当前价格，以官方页面显示为准；本软件不会替你付款。'},
    {title:'创建并复制 API 密钥',text:'进入 API keys 页面，创建一个密钥并复制。这个密钥用于让本软件连接你的 DeepSeek API 账户。',
      link:'https://platform.deepseek.com/api_keys',label:'打开官方 API keys 页面',tip:'不要把密钥发到聊天、群里或公开截图中；这里只需在本机配置页填写。'},
    {title:'回到这里，保存并验证',text:'把刚复制的密钥粘贴到输入框，点击“保存并验证”。看到验证通过，就可以返回控制台执行任务。',
      link:null,label:'',tip:'验证会产生一次少量调用费用。若提示密钥无效，请重新核对；若提示余额不足，请回到官方平台充值。'}
  ];
  let step=0,busy=false,configured=false;
  function showWelcome(){
    $('welcomeView').hidden=false;$('guideView').hidden=true;
    dialog.setAttribute('aria-labelledby','welcomeTitle');
    if(!dialog.open)dialog.showModal();
    $('needGuide').focus();
  }
  function showStep(index){
    step=index;const data=steps[step];
    $('welcomeView').hidden=true;$('guideView').hidden=false;
    dialog.setAttribute('aria-labelledby','guideTitle');
    $('stepCount').textContent=`第 ${step+1} 步 / 共 ${steps.length} 步`;
    $('guideTitle').textContent=data.title;$('guideText').textContent=data.text;$('guideTip').textContent=data.tip;
    $('guideLink').hidden=!data.link;
    if(data.link){$('guideLink').href=data.link;$('guideLink').textContent=data.label;}
    for(const[index,item]of [...$('stepper').children].entries()){
      if(index===step)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');
      item.classList.toggle('done',index<step);
    }
    $('guideBack').textContent=step===0?'返回选择':'上一步';
    $('guideNext').textContent=step===steps.length-1?'去填写密钥':'下一步';
    if(!dialog.open)dialog.showModal();
  }
  function finish(){dialog.close();field.focus();}
  $('needGuide').onclick=()=>showStep(0);$('haveKey').onclick=finish;
  $('openGuide').onclick=()=>showStep(0);$('skipGuide').onclick=finish;
  $('guideBack').onclick=()=>step?showStep(step-1):showWelcome();
  $('guideNext').onclick=()=>step===steps.length-1?finish():showStep(step+1);
  function controls(){
    $('save').disabled=busy;$('testExisting').disabled=busy;field.disabled=busy;
    $('testExisting').hidden=!configured;
  }
  async function state(){
    const response=await fetch('/api/local/provider',{cache:'no-store'});
    if(!response.ok)throw Error('连接状态暂未读到，请稍后重试。');
    const provider=await response.json();configured=provider.configured===true;
    $('status').textContent=provider.validated?'已连接，可以使用':configured?'密钥已保存，待验证':'尚未配置密钥';
    controls();
  }
  async function request(url,body){
    const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
      body:body===undefined?undefined:JSON.stringify(body)});
    const data=await response.json();
    if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'请求未成功，请稍后再试。');
    return data;
  }
  async function verify(key){
    if(busy)return;
    busy=true;controls();result.className='';result.textContent='正在验证连接…';
    try{
      if(key!==undefined)await request('/api/local/provider',{api_key:key});
      const check=await request('/api/local/provider/test');
      result.textContent=check.message;result.className=check.status==='success'?'success':'failure';
      await state();
    }catch(error){result.textContent=error.message;result.className='failure';}
    finally{busy=false;controls();}
  }
  $('form').addEventListener('submit',event=>{
    event.preventDefault();const key=field.value.trim();field.value='';
    if(!key){result.textContent='请先粘贴 API 密钥。';return;}
    verify(key);
  });
  $('testExisting').onclick=()=>verify();
  state().catch(error=>{result.textContent=error.message;result.className='failure';});
  if(new URLSearchParams(location.search).get('guide')==='1')showStep(0);else showWelcome();
})();
