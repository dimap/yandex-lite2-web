import {startAsciiBackground} from './ascii-background.js';
import * as geometry from './clock-geometry.js';
const catalog={lite:'Станция Лайт',lite2:'Станция Лайт 2',mini2:'Станция Мини 2',mini3:'Станция Мини 3',mini3pro:'Станция Мини 3 Про',station2:'Станция 2'};
const requested=new URLSearchParams(location.search).get('model');
const id=Object.prototype.hasOwnProperty.call(catalog,requested)?requested:'lite2';
const picker=document.getElementById('preview-model');picker.value=id;
picker.addEventListener('change',()=>{const url=new URL(location.href);url.searchParams.set('model',picker.value);location.assign(url);});
window.clockGeom=geometry;window.__deviceColor=id==='lite2'?'copper':id==='lite'?'purple':id==='mini2'?'white':'black';
document.getElementById('model-description').textContent=id==='lite2'?'Авторская STL vanyabeat, CC BY-SA 4.0. Геометрия, материалы и свет исходного рендера сохранены.':id==='mini2'?'Приблизительная реконструкция по фото и замерам владельца (97 × 54 мм) с дисплеем часов. Лёгкие материалы без теней. Это не официальный CAD-файл.':'Приблизительная визуальная реконструкция по официальным ракурсам. Лёгкие материалы без теней. Это не официальный CAD-файл.';
const demo=()=>{window.modelShow?.(geometry.textToFrame('12:34',190));window.modelRing?.(Array.from({length:id==='mini3pro'?16:id==='lite'?12:id==='mini2'?6:3},(_,i)=>[90+i*3,45,170+i*4]));};
document.getElementById('model-view').addEventListener('modelready',demo,{once:true});
if(id==='lite2')await import('./model-lite2.js');
else{const{createModel}=await import('./model-studio.js');createModel({id,name:catalog[id]});}
demo();

startAsciiBackground(document.getElementById('model-viz'));
