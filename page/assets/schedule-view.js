/* Shared read-only schedule presentation for the console and the settings editor. */
(() => {
  'use strict';
  window.MaaScheduleView=class {
    constructor(lookup){this.$=lookup;this.plan=null;this.observedAt=0;this.unavailable=false;}
    set(plan){
      if(!plan.available)throw Error(plan.message||'定时状态未能读取。');
      this.plan=plan;this.observedAt=performance.now();this.unavailable=false;
    }
    fail(){
      this.unavailable=true;this.$('runMode').textContent='执行方式待确认';
      this.$('scheduleCountdown').textContent='暂时无法更新';this.$('scheduleNextTime').textContent='';
    }
    render(health){
      const plan=this.plan;if(!plan||this.unavailable)return;
      this.$('runMode').textContent=plan.enabled?'定时自动执行':'手动执行';
      const timing=plan.mode==='random_window'?`每天 ${plan.window_start}～${plan.window_end} 内随机启动一次`:`每天 ${plan.time} 启动`;
      this.$('schedule').textContent=plan.enabled?timing+(plan.today_started?'；今日已启动，不再定时重复执行。':''):'定时已关闭，可随时手动执行。';
      const next=plan.enabled&&plan.next_run_at?new Date(plan.next_run_at):null;
      this.$('scheduleNextTime').textContent=next&&Number.isFinite(next.getTime())
        ?'预计 '+new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(next):'';
      if(!plan.enabled||plan.seconds_until_next_run==null){
        this.$('scheduleCountdown').textContent=plan.enabled===false?'定时已关闭':'暂未读到计划';return;
      }
      const remaining=Math.max(0,Math.ceil(plan.seconds_until_next_run-(performance.now()-this.observedAt)/1000));
      if(!remaining){this.$('scheduleCountdown').textContent=health?.task_running?'任务执行中，正在更新下一次时间':'计划时间已到，等待系统启动';return;}
      const days=Math.floor(remaining/86400),hours=Math.floor(remaining%86400/3600),minutes=Math.floor(remaining%3600/60),seconds=remaining%60;
      this.$('scheduleCountdown').textContent=`${days?days+'天 ':''}${hours}小时 ${minutes}分 ${seconds}秒后`;
    }
  };
})();
