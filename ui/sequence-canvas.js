/* Layout determinístico do fluxo; a posição visual não altera o áudio. */
(function(root){
 'use strict';
 const W=224,H=92,GAP=70;
 function layout(blocks){
  const nodes=[],edges=[],slots=[],labels=[];
  const measure=list=>Math.max(W,...list.map(b=>b.type==='parallel'?b.branches.reduce((n,branch)=>n+measure(branch),0)+(b.branches.length-1)*50:W));
  const width=measure(blocks)+120,cx=width/2;
  const edge=(x1,y1,x2,y2)=>edges.push({x1,y1,x2,y2});
  function list(items,x,y,parent=null,branch=null){
   let previous=y;
   for(const block of items){
    slots.push({x,y:previous+GAP/2,parent,branch,before:block.id});
    const top=previous+GAP;
    edge(x,previous,x,top);
    nodes.push({...block,x:x-W/2,y:top,width:W,height:H});
    previous=top+H;
    if(block.type==='parallel'){
     const widths=block.branches.map(measure),total=widths.reduce((a,b)=>a+b,0)+(widths.length-1)*50;
     let left=x-total/2;const ends=[];
     block.branches.forEach((items,i)=>{
      const bx=left+widths[i]/2,by=previous+44;
      edge(x,previous,bx,by);labels.push({x:bx,y:by+7,text:'Ramo '+(i+1)});
      ends.push({x:bx,y:list(items,bx,by+20,block.id,i)});left+=widths[i]+50;
     });
     const join=Math.max(...ends.map(e=>e.y))+40;
     ends.forEach(e=>edge(e.x,e.y,x,join));previous=join;
     labels.push({x,y:join+8,text:'Todos terminam → continuar'});
    }
   }
   slots.push({x,y:previous+GAP/2,parent,branch,before:null});
   edge(x,previous,x,previous+GAP);return previous+GAP;
  }
  const end=list(blocks,cx,54);
  return {nodes,edges,slots,labels,width,height:end+70,start:{x:cx,y:30},end:{x:cx,y:end+20}};
 }
 function mount(doc,host,callbacks){
  const viewport=doc.createElement('div'),world=doc.createElement('div');
  viewport.className='sequence-viewport';viewport.setAttribute('aria-label','Canvas do fluxo. Arraste o fundo para navegar.');viewport.tabIndex=0;
  world.className='sequence-world';viewport.append(world);host.append(viewport);
  let x=0,y=20,scale=1,scene=null,first=true,drag=null,ghost=null,over=null;
  const transform=()=>{world.style.transform=`translate(${x}px,${y}px) scale(${scale})`;callbacks.zoom?.(Math.round(scale*100));};
  function fit(){if(!scene)return;scale=Math.max(.3,Math.min(1,(viewport.clientWidth-60)/scene.width,(viewport.clientHeight-40)/scene.height));x=(viewport.clientWidth-scene.width*scale)/2;y=20;transform();}
  function zoom(factor){const next=Math.max(.3,Math.min(1.8,scale*factor)),cx=viewport.clientWidth/2,cy=viewport.clientHeight/2;x=cx-(cx-x)*next/scale;y=cy-(cy-y)*next/scale;scale=next;transform();}
  function clearDrag(){
   const previous=drag;drag=null;
   over?.classList.remove('drop-hover');over=null;ghost?.remove();ghost=null;
   if(previous){doc.removeEventListener('pointermove',previous.move,true);doc.removeEventListener('pointerup',previous.up,true);doc.removeEventListener('pointercancel',clearDrag,true);if(previous.element.hasPointerCapture?.(previous.id))previous.element.releasePointerCapture(previous.id);}
  }
  function bindDrag(element,payload){
   element.draggable=false;element.ondragstart=e=>e.preventDefault();
   element.onpointerdown=e=>{
    if(e.button!==0||e.target.closest?.('[data-no-drag]'))return;
    e.stopPropagation();clearDrag();
    const gesture={id:e.pointerId,x:e.clientX,y:e.clientY,payload,element,moved:false};drag=gesture;
    gesture.move=event=>{
     if(drag!==gesture||event.pointerId!==gesture.id)return;
     if(!gesture.moved&&Math.hypot(event.clientX-gesture.x,event.clientY-gesture.y)<6)return;
     if(!gesture.moved){gesture.moved=true;element.setPointerCapture?.(gesture.id);}
     event.preventDefault();
     if(!ghost){ghost=doc.createElement('div');ghost.className='sequence-drag-ghost';ghost.textContent=payload.title||'Mover bloco';doc.body.append(ghost);}
     ghost.style.left=event.clientX+16+'px';ghost.style.top=event.clientY+12+'px';
     const target=doc.elementFromPoint(event.clientX,event.clientY)?.closest('[data-flow-slot]');
     over?.classList.remove('drop-hover');over=target;over?.classList.add('drop-hover');
    };
    gesture.up=event=>{
     if(drag!==gesture||event.pointerId!==gesture.id)return;
     const destination=over?.flowTarget,moved=gesture.moved;clearDrag();
     if(moved){event.preventDefault();element.dataset.suppressClick='true';setTimeout(()=>delete element.dataset.suppressClick,0);if(destination)callbacks.drop(payload,destination);else callbacks.message('Solte sobre um conector + para inserir o bloco.');}
    };
    doc.addEventListener('pointermove',gesture.move,true);doc.addEventListener('pointerup',gesture.up,true);doc.addEventListener('pointercancel',clearDrag,true);
   };
   element.onlostpointercapture=()=>{if(drag?.element===element)clearDrag();};
  }
  let pan=null;
  viewport.onpointerdown=e=>{if(e.button!==0||e.target.closest?.('.flow-node,[data-flow-slot],button'))return;pan={x:e.clientX,y:e.clientY,left:x,top:y};viewport.setPointerCapture(e.pointerId);};
  viewport.onpointermove=e=>{if(pan){x=pan.left+e.clientX-pan.x;y=pan.top+e.clientY-pan.y;transform();}};
  viewport.onpointerup=viewport.onpointercancel=()=>{pan=null;};
  viewport.addEventListener('wheel',e=>{e.preventDefault();if(e.ctrlKey||e.metaKey)zoom(e.deltaY>0?.9:1.1);else{x-=e.shiftKey?e.deltaY:e.deltaX;y-=e.shiftKey?0:e.deltaY;transform();}},{passive:false});
  function render(blocks,renderer,insertion){
   const previous=scene;scene=layout(blocks);world.replaceChildren();world.style.width=scene.width+'px';world.style.height=scene.height+'px';
   const svg=doc.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('width',scene.width);svg.setAttribute('height',scene.height);svg.classList.add('sequence-edges');
   const defs=doc.createElementNS('http://www.w3.org/2000/svg','defs'),marker=doc.createElementNS('http://www.w3.org/2000/svg','marker'),arrow=doc.createElementNS('http://www.w3.org/2000/svg','path');
   marker.setAttribute('id','flow-arrow');marker.setAttribute('viewBox','0 0 10 10');marker.setAttribute('refX','8');marker.setAttribute('refY','5');marker.setAttribute('markerWidth','5');marker.setAttribute('markerHeight','5');marker.setAttribute('orient','auto');arrow.setAttribute('d','M 0 0 L 10 5 L 0 10 z');arrow.setAttribute('class','flow-arrow');marker.append(arrow);defs.append(marker);svg.append(defs);
   for(const e of scene.edges){const path=doc.createElementNS('http://www.w3.org/2000/svg','path'),middle=(e.y1+e.y2)/2;path.setAttribute('d',`M ${e.x1} ${e.y1} C ${e.x1} ${middle}, ${e.x2} ${middle}, ${e.x2} ${e.y2}`);path.setAttribute('marker-end','url(#flow-arrow)');svg.append(path);}
   world.append(svg);
   for(const item of [{...scene.start,text:'INÍCIO'},{...scene.end,text:'FIM'},...scene.labels]){const label=doc.createElement('span');label.className='flow-label';label.textContent=item.text;label.style.left=item.x+'px';label.style.top=item.y+'px';world.append(label);}
   for(const n of scene.nodes){const card=renderer(n);card.classList.add('flow-node');card.style.left=n.x+'px';card.style.top=n.y+'px';card.style.width=n.width+'px';card.style.height=n.height+'px';bindDrag(card,{blockId:n.id,title:card.getAttribute('aria-label')});world.append(card);}
   for(const slot of scene.slots){const b=doc.createElement('button');b.type='button';b.className='flow-slot';b.dataset.flowSlot='';b.flowTarget={parent:slot.parent,branch:slot.branch,before:slot.before};b.setAttribute('aria-label',slot.before?'Inserir antes deste bloco':'Inserir no fim'+(slot.parent?' deste ramo':''));b.title='Adicionar aqui · arraste um áudio ou clique e escolha na biblioteca';b.textContent='+';b.style.left=slot.x+'px';b.style.top=slot.y+'px';b.classList.toggle('slot-selected',JSON.stringify(b.flowTarget)===JSON.stringify(insertion));b.onclick=()=>callbacks.insert(b.flowTarget);world.append(b);}
   if(first){first=false;x=(viewport.clientWidth-scene.width)/2;}else if(previous)x+=(previous.width-scene.width)*scale/2;
   transform();
  }
  function reveal(id){const n=scene?.nodes.find(n=>n.id===id);if(!n)return;
   const left=x+n.x*scale,top=y+n.y*scale,right=left+n.width*scale,bottom=top+n.height*scale;
   if(left<24)x+=24-left;else if(right>viewport.clientWidth-24)x-=right-viewport.clientWidth+24;
   if(top<24)y+=24-top;else if(bottom>viewport.clientHeight-24)y-=bottom-viewport.clientHeight+24;
   transform();
  }
  return {render,bindDrag,fit,zoom,reveal,viewport,cancelDrag:clearDrag,reset(){if(scene){scale=1;x=(viewport.clientWidth-scene.width)/2;y=20;transform();}},destroy(){clearDrag();viewport.remove();}};
 }
 const api={layout,mount};if(typeof module!=='undefined'&&module.exports)module.exports=api;root.SequenceCanvas=api;
})(typeof globalThis!=='undefined'?globalThis:this);
