// An observation picker, not a physical Cucumber layout or a live clock display.
export function createClockMap(onSelect) {
  const make=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;return n;};
  const root=make('div');root.className='midi-clock-map';root.setAttribute('role','group');root.setAttribute('aria-label','Схема для выбора расположения сегмента');
  const style=make('style');style.textContent=`
    .midi-clock-map{padding:10px;border-radius:14px;background:#151a23;color:#e7edf5;max-width:100%;box-sizing:border-box}
    .midi-clock-map .midi-clock-digits{display:grid;grid-template-columns:repeat(2,minmax(0,1fr)) 24px repeat(2,minmax(0,1fr));gap:4px;align-items:start}
    .midi-clock-map .midi-clock-digit-label{text-align:center;font-size:12px;line-height:22px;color:#aab8cc}
    .midi-clock-map .midi-clock-digit{display:grid;grid-template-columns:24px minmax(0,1fr) 24px;grid-template-rows:24px 34px 24px 34px 24px}
    .midi-clock-map .midi-clock-slot{display:flex;align-items:center;justify-content:center;box-sizing:border-box;min-width:0;min-height:24px;width:100%;padding:2px;border:0;border-radius:5px;background:transparent;color:#d6e1ee;cursor:pointer}
    .midi-clock-map .midi-clock-slot svg{display:block;width:100%;height:100%;min-height:20px;pointer-events:none}
    .midi-clock-map .midi-clock-slot:disabled{opacity:.45;cursor:default}
    .midi-clock-map .midi-clock-slot:hover:not(:disabled){background:#ffffff15;color:#fff}
    .midi-clock-map .midi-clock-slot[aria-pressed="true"]{background:#ffb45422;color:#ffb454;outline:2px solid #ffb454}
    .midi-clock-map .midi-clock-slot:focus-visible{outline:2px solid #90bfff;outline-offset:1px}
    .midi-clock-map .midi-clock-punctuation{position:relative;height:162px}
    .midi-clock-map .midi-clock-punctuation button{position:absolute;height:24px}
    .midi-clock-map .midi-clock-extras{display:flex;gap:4px;flex-wrap:wrap;margin-top:10px}
    .midi-clock-map .midi-clock-extras button{width:auto;padding:4px 8px;font-size:12px;border:1px solid #4a5668}
  `;root.append(style);
  let enabled=false;const buttons=[];
  const shapes={horizontal:'M10 5H90L98 12 90 19H10L2 12Z',vertical:'M5 10L12 2 19 10V90L12 98 5 90Z',dot:'M12 5a7 7 0 1 0 0 14a7 7 0 1 0 0-14'};
  function target(position,label,shape,parent){
    const b=make('button');b.type='button';b.className='midi-clock-slot';b.dataset.position=position;b.setAttribute('aria-label',label);b.setAttribute('title',label);b.setAttribute('aria-pressed','false');b.disabled=true;
    if(shape){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox',shape==='horizontal'?'0 0 100 24':shape==='vertical'?'0 0 24 100':'0 0 24 24');svg.setAttribute('preserveAspectRatio','none');svg.setAttribute('aria-hidden','true');
      const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',shapes[shape]);path.setAttribute('fill','currentColor');svg.append(path);b.append(svg);
    }else b.textContent=label;
    b.onclick=()=>{if(enabled)onSelect(position,label);};buttons.push(b);parent.append(b);return b;
  }
  const digits=make('div');digits.className='midi-clock-digits';root.append(digits);
  const bars=[['A','верхняя полоса',1,'1 / 4','horizontal'],['B','верхняя правая полоса',2,'3','vertical'],['C','нижняя правая полоса',4,'3','vertical'],['D','нижняя полоса',5,'1 / 4','horizontal'],['E','нижняя левая полоса',4,'1','vertical'],['F','верхняя левая полоса',2,'1','vertical'],['G','средняя полоса',3,'1 / 4','horizontal']];
  for(let digit=1;digit<=4;digit++){
    const wrap=make('div'),label=make('div',String(digit));label.className='midi-clock-digit-label';wrap.append(label);
    const grid=make('div');grid.className='midi-clock-digit';wrap.append(grid);digits.append(wrap);
    for(const [letter,name,row,column,shape]of bars){const b=target(`d${digit}-${letter}`,`Цифра ${digit} слева: ${name}`,shape,grid);b.style.gridRow=String(row);b.style.gridColumn=column;}
    if(digit===2){const punctuation=make('div');punctuation.className='midi-clock-punctuation';digits.append(punctuation);
      target('colon-top','Двоеточие: верхняя точка','dot',punctuation).style.top='54px';
      target('minus','Средний дефис','horizontal',punctuation).style.top='80px';
      target('colon-bottom','Двоеточие: нижняя точка','dot',punctuation).style.top='106px';
    }
  }
  const extras=make('div');extras.className='midi-clock-extras';root.append(extras);
  for(let digit=1;digit<=4;digit++){const b=target(`d${digit}-H`,`Цифра ${digit} слева: точка справа внизу`,null,extras);b.textContent=`Точка ${digit}`;}
  const degree=target('degree','Знак градуса',null,extras);degree.textContent='Градус °';
  return {element:root,setEnabled(value){enabled=!!value;for(const b of buttons)b.disabled=!enabled;},
    select(position){for(const b of buttons)b.setAttribute('aria-pressed',String(b.dataset.position===position));}};
}
