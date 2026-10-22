/* Native task reordering, with buttons and keyboard alternatives to dragging. */
(() => {
  'use strict';
  window.MaaTaskOrder=class {
    constructor(container,names,onChange){
      this.container=container;this.names=[...names];this.order=[...names];this.rows=new Map();
      this.onChange=onChange;this.locked=true;this.dragging=null;
    }
    value(){return [...this.order];}
    append(name,label){
      const row=document.createElement('div'),handle=document.createElement('span'),position=document.createElement('span');
      row.className='task-order-item';row.dataset.task=name;row.setAttribute('role','listitem');
      handle.className='task-drag-handle';handle.textContent='⠿';handle.title='拖动调整'+name+'的顺序';handle.setAttribute('aria-hidden','true');
      position.className='task-order-number';position.setAttribute('aria-hidden','true');
      const controls=document.createElement('span');controls.className='task-order-controls';
      for(const [offset,symbol,title] of [[-1,'↑','提前执行'],[1,'↓','延后执行']]){
        const button=document.createElement('button');button.type='button';button.textContent=symbol;
        button.title=name+'：'+title;button.setAttribute('aria-label',button.title);
        button.setAttribute('aria-keyshortcuts',offset<0?'Alt+ArrowUp':'Alt+ArrowDown');
        button.dataset.offset=offset;button.onclick=()=>this.move(name,this.order.indexOf(name)+offset);
        controls.append(button);
      }
      row.append(handle,position,label,controls);this.rows.set(name,row);this.container.append(row);
      row.addEventListener('keydown',event=>{
        if(!event.altKey||event.ctrlKey||event.metaKey||!['ArrowUp','ArrowDown'].includes(event.key))return;
        event.preventDefault();this.move(name,this.order.indexOf(name)+(event.key==='ArrowUp'?-1:1));
      });
      handle.addEventListener('dragstart',event=>{
        if(this.locked){event.preventDefault();return;}
        this.dragging=name;row.classList.add('is-dragging');event.dataTransfer.effectAllowed='move';
        event.dataTransfer.setData('text/plain',name);
      });
      handle.addEventListener('dragend',()=>this.clearDrag());
      row.addEventListener('dragover',event=>{
        if(this.locked||!this.dragging||this.dragging===name)return;
        event.preventDefault();event.dataTransfer.dropEffect='move';row.classList.add('is-drop-target');
      });
      row.addEventListener('dragleave',event=>{if(!row.contains(event.relatedTarget))row.classList.remove('is-drop-target');});
      row.addEventListener('drop',event=>{
        if(this.locked||!this.dragging)return;
        event.preventDefault();this.move(this.dragging,this.order.indexOf(name));this.clearDrag();
      });
      this.render();
    }
    clearDrag(){
      this.dragging=null;
      for(const row of this.rows.values())row.classList.remove('is-dragging','is-drop-target');
    }
    set(order){
      const valid=Array.isArray(order)?order.filter(name=>this.names.includes(name)):[];
      this.order=[...new Set([...valid,...this.names])];
      for(const name of this.order)if(this.rows.has(name))this.container.append(this.rows.get(name));
      this.render();
    }
    disable(value){this.locked=value;if(value)this.clearDrag();this.render();}
    render(){
      for(const[name,row]of this.rows){
        const index=this.order.indexOf(name);
        row.querySelector('.task-order-number').textContent=index+1;
        row.querySelector('.task-drag-handle').draggable=!this.locked;
        row.classList.toggle('is-locked',this.locked);
        for(const button of row.querySelectorAll('button'))button.disabled=this.locked||index+Number(button.dataset.offset)<0||index+Number(button.dataset.offset)>=this.order.length;
      }
    }
    move(name,index){
      if(this.locked||!this.names.includes(name)||index<0||index>=this.order.length||index===this.order.indexOf(name))return;
      const focus=document.activeElement,next=this.order.filter(item=>item!==name);
      next.splice(index,0,name);this.set(next);
      if(focus&&this.container.contains(focus)){
        (focus.disabled?this.rows.get(name).querySelector('input'):focus)?.focus({preventScroll:true});
      }
      this.onChange(`${name}已调整为第${index+1}项，尚未保存。`);
    }
  };
})();
