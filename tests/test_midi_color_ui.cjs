// No physical device. Exercise actual UI event handlers against a fake API.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const elements=[];let inserted,requests=[],failed=false,blocked=false,rgbControls={};
const pollers=[];
const stored=[{id:'old-red',component:'ring',color:'red',state:'passed',restored:true}];
function element(tag){const e={tag,children:[],dataset:{},attributes:{},hidden:false,disabled:false,
 append(...nodes){this.children.push(...nodes);},setAttribute(k,v){this.attributes[k]=v;}};elements.push(e);return e;}
const context={document:{createElement:element,querySelector:()=>({after:n=>{inserted=n;}})},
 PanelContext:{fetch:async(url,options)=>{
  const data=options.body?JSON.parse(options.body):undefined;requests.push({url,data});
  if(failed)return {ok:false,json:async()=>({error:'fixture failure'})};
  if(url.endsWith('/run')){const trial={id:'new-'+stored.length,...data,state:blocked?'error':'awaiting_observation',restored:!blocked};stored.push(trial);return {ok:true,json:async()=>({trial})};}
  if(url.endsWith('/observe')){const trial=stored.find(t=>t.id===data.id);trial.state=data.observation==='matched'?'passed':'not_observed';return {ok:true,json:async()=>({trial})};}
  return {ok:true,json:async()=>({trials:stored,blocked,busy:false,rgb_controls:rgbControls})};
 }},Blob,URL,console,setInterval:fn=>pollers.push(fn)};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/midi-tests.js'),'utf8').replace('export function','function'),context);
const flush=()=>new Promise(r=>setImmediate(r));
const find=label=>elements.find(e=>e.attributes['aria-label']===label||e.textContent===label);
(async()=>{
 context.mountMidiTests({id:'mini2'});assert.equal(elements.length,0);
 context.mountMidiTests({id:'midi'});await flush();
 assert.equal(inserted.dataset.widget,'midi-tests');
 assert(requests.every(r=>r.data===undefined),'mount sends only GET, never a physical trial');
 assert.equal(elements.filter(e=>e.attributes['aria-label']).length,8,'four colours per component');
 assert(find('Кольцо: красный').textContent.includes('✓'),'previous observations survive reload');
 stored.push({id:'unrelated-clock',component:'clock_display',state:'awaiting_observation',restored:true});
 await find('Обновить результаты').onclick();
 assert(elements.find(e=>e.children.includes(find('Цвет совпал'))).hidden,'clock observations never appear in the colour card');
 const green=find('Логотип: зелёный');const pending=green.onclick();
 assert(green.disabled,'double click disabled during request');await pending;
 assert.deepEqual(requests.find(r=>r.url.endsWith('/run')).data,{component:'logo',color:'green'});
 assert.equal(find('Цвет совпал').disabled,false);
 await find('Цвет совпал').onclick();
 assert.equal(stored.at(-1).state,'passed');assert(green.textContent.includes('✓'));
 await green.onclick();await find('Не заметил').onclick();assert.equal(stored.at(-1).state,'not_observed');
 blocked=true;await find('Обновить результаты').onclick();
 assert(green.disabled,'failed restoration blocks further colours');
 assert.equal(find('Восстановить и проверить').hidden,false);
 failed=true;await find('Обновить результаты').onclick();
 assert(elements.some(e=>e.textContent==='fixture failure'));
 failed=false;blocked=false;
 rgbControls={ring:{colors_ready:true,ready:false,can_reset:false},logo:{colors_ready:true,ready:false,can_reset:false}};
 await find('Обновить результаты').onclick();
 await find('Проверить управление кольцом · 5 с').onclick();
 assert.deepEqual(requests.at(-2).data,{component:'ring_controls'});
 await find('Всё сработало').onclick();assert.equal(stored.at(-1).state,'passed');
 rgbControls.ring={colors_ready:true,ready:true,can_reset:true};
 stored.push({id:'repeated-red',component:'ring',color:'red',state:'awaiting_observation',restored:true});
 const runsBeforePoll=requests.filter(r=>r.url.endsWith('/run')).length;
 await pollers[0]();
 assert(find('Кольцо: красный').textContent.includes('✓'),'an ungraded recheck does not hide prior acceptance');
 assert.equal(requests.filter(r=>r.url.endsWith('/run')).length,runsBeforePoll,'background refresh is read-only');
 assert(!elements.find(e=>e.textContent==='Включить').hidden,'acceptance from chat opens the controls without reloading the page');
 const beforeSelection=requests.length;await find('Кольцо: синий').onclick();
 assert.equal(requests.length,beforeSelection,'ordinary palette selection never writes LEDs');
 const ons=elements.filter(e=>e.textContent==='Включить');await ons[0].onclick();
 assert.deepEqual(requests.at(-1),{url:'/api/ring',data:{red:0,green:0,blue:8}});
 await elements.find(e=>e.textContent==='Выключить').onclick();
 assert.deepEqual(requests.at(-1),{url:'/api/ring',data:{red:0,green:0,blue:0}});
 await elements.find(e=>e.textContent==='Сбросить').onclick();
 assert.deepEqual(requests.at(-1),{url:'/api/ring/reset',data:{}});
 console.log('Midi manual colour session: no automatic trial, eight presets, durable observations, busy/error gates passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
