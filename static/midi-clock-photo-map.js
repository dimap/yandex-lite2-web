// An observed location on the owner's real photo, not an inferred channel map.
export function createClockPhotoMap(onSelect) {
  const ns='http://www.w3.org/2000/svg';
  const root=document.createElement('div');root.hidden=true;
  const title=document.createElement('h3');title.textContent='Индикаторы по вашему фото';
  const hint=document.createElement('p');hint.className='hint';hint.textContent='После пробы нажмите прямо на элемент, который мигнул. Голубая метка покажет выбранное место. Затем запишите результат.';
  const zoom=document.createElement('button');zoom.type='button';zoom.className='btn sm';zoom.textContent='Увеличить ×2';zoom.setAttribute('aria-pressed','false');
  const viewport=document.createElement('div');Object.assign(viewport.style,{overflow:'auto',maxWidth:'100%',marginTop:'8px'});
  const button=document.createElement('button');button.type='button';button.disabled=true;
  button.setAttribute('aria-label','Фото дисплея: выбрать место. Стрелки перемещают метку, Enter выбирает.');
  Object.assign(button.style,{display:'block',width:'100%',padding:'0',border:'1px solid #aab8cc',borderRadius:'12px',overflow:'hidden',background:'#151a23',cursor:'crosshair'});
  const svg=document.createElementNS(ns,'svg');svg.style.display='block';svg.style.width='100%';svg.style.height='auto';svg.setAttribute('aria-hidden','true');
  const photo=document.createElementNS(ns,'image');
  const marker=document.createElementNS(ns,'g');marker.setAttribute('visibility','hidden');marker.setAttribute('pointer-events','none');
  for(const [colour,stroke]of [['#07121f',14],['#59e5ff',7]]){const ring=document.createElementNS(ns,'circle');ring.setAttribute('fill','none');ring.setAttribute('stroke',colour);ring.setAttribute('stroke-width',String(stroke));marker.append(ring);}
  svg.append(photo,marker);button.append(svg);viewport.append(button);root.append(title,hint,zoom,viewport);
  let reference=null,enabled=false,cursor={x:.5,y:.5},chosen=false,box=null,zoomed=false;
  zoom.onclick=()=>{zoomed=!zoomed;button.style.width=zoomed?'200%':'100%';zoom.textContent=zoomed?'Обычный масштаб':'Увеличить ×2';zoom.setAttribute('aria-pressed',String(zoomed));};
  function draw(){marker.setAttribute('visibility',chosen?'visible':'hidden');if(!box)return;
    for(const ring of marker.children){ring.setAttribute('cx',String(box.x+cursor.x*box.width));ring.setAttribute('cy',String(box.y+cursor.y*box.height));ring.setAttribute('r',String(box.width*.018));}}
  function choose(x,y){cursor={x:Math.max(0,Math.min(1,x)),y:Math.max(0,Math.min(1,y))};chosen=true;draw();onSelect({...cursor,reference:reference.id});}
  button.onclick=event=>{if(!enabled||!reference)return;
    if(event.detail===0){choose(cursor.x,cursor.y);return;}
    const r=svg.getBoundingClientRect();if(r.width>0&&r.height>0)choose((event.clientX-r.left)/r.width,(event.clientY-r.top)/r.height);};
  button.onkeydown=event=>{if(!enabled||!reference)return;const deltas={ArrowLeft:[-.01,0],ArrowRight:[.01,0],ArrowUp:[0,-.01],ArrowDown:[0,.01]};const d=deltas[event.key];if(d){event.preventDefault();choose(cursor.x+d[0],cursor.y+d[1]);}};
  return {element:root,setReference(value){if(!value||!value.image?.startsWith('data:image/jpeg;base64,'))return;
      reference=value;box={x:value.crop.x*value.width,y:value.crop.y*value.height,width:value.crop.width*value.width,height:value.crop.height*value.height};
      svg.setAttribute('viewBox',`${box.x} ${box.y} ${box.width} ${box.height}`);photo.setAttribute('width',String(value.width));photo.setAttribute('height',String(value.height));photo.setAttribute('href',value.image);root.hidden=false;draw();},
    setEnabled(value){enabled=!!value;button.disabled=!enabled;button.style.opacity=enabled?'1':'.7';},
    clear(){chosen=false;cursor={x:.5,y:.5};draw();}};
}
