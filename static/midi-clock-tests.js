// Record observed segment locations; no assumed Midi-to-36-value clock map.
import {createClockMap} from './midi-clock-map.js';
import {createClockPhotoMap} from './midi-clock-photo-map.js';
export function mountMidiClockTests(profile) {
  if (profile.id !== 'midi') return;
  const el=(tag,text)=>{const node=document.createElement(tag);if(text)node.textContent=text;return node;};
  const card=el('section');card.className='card';card.dataset.widget='midi-clock-tests';card.id='midi-clock-tests';
  const heading=el('h2','Дисплей часов'),badge=el('span');badge.className='badge';
  const head=el('div');head.className='card-head';head.append(heading,badge);
  const intro=el('p','Поставьте музыку на паузу. Выбранный сегмент погаснет на 1 секунду, затем загорится на 2 секунды с яркостью 100% и вернётся как был. Выберите изменившееся место на фото или схеме и запишите результат. Цифры 1–4 — слева направо. Во время пробы не меняйте громкость и не обращайтесь к Алисе.');intro.className='hint';
  const picker=el('select');picker.className='field';picker.setAttribute('aria-label','Сегмент дисплея Миди');
  const status=el('p');status.className='hint';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const progress=el('p');progress.className='hint';
  const outsideHint=el('p','Если нужного элемента нет на фото или схеме, нажмите «Нет на схеме». Его назначение останется неустановленным. При желании опишите форму в «Другой значок или место».');outsideHint.className='hint';
  const tools=el('div');tools.className='chips';
  const photoTools=el('div');photoTools.className='chips';
  const photoHint=el('p','Для фото: все 45 каналов светят на 60% в течение 10 секунд. Во второй фотопробе выбранный канал светит тусклее — на 10%. Подготовьте камеру перед нажатием. После снимка приложите фото в чат; для второго кадра укажите имя канала.');photoHint.className='hint';
  const observations=el('div');
  const selection=el('p','После пробы нажмите на изменившийся сегмент схемы.');selection.className='hint';
  const description=el('input');description.type='text';description.className='field';description.maxLength=160;
  description.placeholder='Например: первая цифра, верхняя полоса';description.setAttribute('aria-label','Где изменился свет');
  const answerButtons=el('div');answerButtons.className='chips';
  const history=el('details'),summary=el('summary'),list=el('ul');history.append(summary,list);
  const mapping=el('details'),mappingTitle=el('summary','Карта сегментов');mapping.open=true;
  const display=el('div'),displayTitle=el('h3','Проверка цифр и двоеточия');
  const displayHint=el('p','Поставьте музыку на паузу. После нажатия смотрите на колонку: 0123 → 4567 → 8901 → 12:34, по 2 секунды на надпись. Яркость 60%. Затем штатные часы вернутся автоматически. Во время проверки не обращайтесь к Алисе.');displayHint.className='hint';
  const displayTools=el('div');displayTools.className='chips';
  const displayStatus=el('p');displayStatus.className='hint';displayStatus.setAttribute('role','status');displayStatus.setAttribute('aria-live','polite');
  const displayAnswers=el('div');displayAnswers.className='chips';
  const checks={
    digits:['Цифры и двоеточие','0123 → 4567 → 8901 → 12:34',8],
    digits_low:['Цифры 0–3','0000 → 1111 → 2222 → 3333',8],
    digits_high:['Цифры 4–7','4444 → 5555 → 6666 → 7777',8],
    degree:['Цифры 8–9 и градус','8888 → 9999 → 12:34°',6],
    brightness:['Яркость','12:34 с яркостью 20% → 60% → 100%',6],
  };
  const checkTools=el('div');checkTools.className='chips';const checkButtons=[];
  const checksDetails=el('details'),checksSummary=el('summary','Проверки дисплея');checksDetails.open=true;
  const customForm=el('div');customForm.className='col';customForm.style.gap='10px';customForm.hidden=true;
  const customText=el('input');customText.type='text';customText.className='field mono';customText.value='12:34';customText.maxLength=6;customText.setAttribute('aria-label','Четыре цифры на дисплее Миди');
  const brightnessRow=el('div');brightnessRow.className='row';
  const brightnessLabel=el('label','Яркость'),customBrightness=el('input'),brightnessValue=el('span','60%');
  customBrightness.type='range';customBrightness.min='20';customBrightness.max='100';customBrightness.value='60';customBrightness.setAttribute('aria-label','Яркость дисплея Миди, проценты');customBrightness.style.flex='1';
  customBrightness.oninput=()=>{brightnessValue.textContent=customBrightness.value+'%';};
  brightnessRow.append(brightnessLabel,customBrightness,brightnessValue);
  const customStatus=el('p');customStatus.className='hint';customStatus.setAttribute('role','status');
  const modeTools=el('div');modeTools.className='seg';modeTools.setAttribute('role','group');modeTools.setAttribute('aria-label','Режим краткого показа Миди');
  const modeButtons=[],modeNames={text:'Текст',date:'Дата',temp:'Температура процессора',timer:'Таймер'};
  const timerAnswers=el('div');timerAnswers.className='chips';timerAnswers.hidden=true;
  let customMode='text',modeLoading=false;
  let selectedCheck='digits',controlsWereReady=false;
  let report={trials:[],clock_segments:[],blocked:false,busy:false},busy=false,current=null,selectedPosition=null,selectedPhotoPoint=null,hasPhoto=false,wasComplete=false,displayMessage='';
  const photoMap=createClockPhotoMap(point=>{selectedPhotoPoint=point;selectedPosition=null;map.select(null);description.value=`Место на фото: ${(point.x*100).toFixed(1)}% слева, ${(point.y*100).toFixed(1)}% сверху`;selection.textContent='Выбрано: '+description.value;render();});
  const map=createClockMap((position,label)=>{selectedPosition=position;selectedPhotoPoint=null;photoMap.clear();description.value=label;map.select(position);selection.textContent='Выбрано: '+label;render();});
  const classic=el('details'),classicTitle=el('summary','Схема полосок и точек');classic.open=true;classic.append(classicTitle,map.element);
  function clearSelection(){selectedPosition=null;selectedPhotoPoint=null;description.value='';map.select(null);photoMap.clear();selection.textContent=hasPhoto?'Нажмите на изменившееся место на фото или на полоску схемы.':'Нажмите на изменившуюся полоску или точку схемы.';}
  const custom=el('details'),customTitle=el('summary','Другой значок или место');custom.append(customTitle,description);
  description.oninput=()=>{selectedPosition=null;selectedPhotoPoint=null;map.select(null);photoMap.clear();selection.textContent=description.value.trim()?'Своё описание: '+description.value:'Нажмите на изменившийся сегмент схемы.';render();};
  const request=async(path,data)=>{const response=await PanelContext.fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await response.json();if(!response.ok||result.error){const error=new Error(result.error||'Ошибка проверки дисплея');error.offline=result.offline;throw error;}return result;};
  const button=(text,action,parent)=>{const node=el('button',text);node.type='button';node.className='btn sm';node.onclick=action;parent.append(node);return node;};
  const latest=()=>new Map(report.trials.filter(t=>t.component==='clock').map(t=>[t.segment,t]));
  const mappingComplete=()=>!!report.clock_segments.length&&report.clock_segments.every(n=>latest().get(n)?.state==='mapped');
  const completionMessage=()=>`Карта расположений собрана: ${report.clock_segments.length}/${report.clock_segments.length}. Повторять пробы не нужно. Следующий этап — контроль цифр и двоеточия (H11).`;
  const displayTrial=()=>report.trials.filter(t=>t.component==='clock_display'&&(t.display_check||t.check||'digits')===selectedCheck).at(-1);
  const recorded=t=>['mapped','unmapped'].includes(t?.state);
  function selectedTrial(){const trial=latest().get(picker.value);return trial?.state==='awaiting_observation'?trial:null;}
  function render(){
    const mapped=[...latest().values()].filter(t=>t.state==='mapped');
    const unmapped=[...latest().values()].filter(t=>t.state==='unmapped');
    badge.textContent=`Карта ${mapped.length}/${report.clock_segments.length}`;
    const complete=mappingComplete(),trial=displayTrial();
    customForm.hidden=!report.display_controls_ready;
    customButton.disabled=busy||modeLoading||!customText.value||report.busy||report.blocked||report.offline||!report.display_controls_ready;
    customText.disabled=customMode!=='text'||busy||modeLoading;
    const acceptedMode=key=>report.trials.filter(t=>(key==='timer'?t.component==='clock_timer':t.component==='clock_text'&&(t.display_mode||'text')===key)&&['passed','failed'].includes(t.state)).at(-1)?.state==='passed';
    for(const {node,key} of modeButtons){node.textContent=modeNames[key]+(acceptedMode(key)?' ✓':'');node.className=customMode===key?'active':'';node.setAttribute('aria-pressed',String(customMode===key));node.disabled=busy||modeLoading;}
    customButton.textContent=customMode==='timer'?'Проверить таймер · 10 с':'Показать · 8 с';
    customHint.textContent=customMode==='timer'?'Проверка отсчёта 00:08 → 00:00. Ноль виден 2 секунды, затем возвращаются часы. Без звука. Поставьте музыку на паузу; во время проверки не обращайтесь к Алисе.':
      'Краткий показ текста, даты или температуры процессора. Через 8 секунд возвращаются обычные часы.'+(['date','temp'].every(acceptedMode)?' Режимы проверены на колонке.':' Дата и температура проходят приёмку.');
    const timerTrial=report.trials.filter(t=>t.component==='clock_timer').at(-1);
    timerAnswers.hidden=customMode!=='timer'||timerTrial?.state!=='awaiting_observation'||!timerTrial.restored||report.blocked;
    for(const node of timerAnswers.children)node.disabled=busy||report.busy||report.blocked;
    if(customMode==='timer'&&!busy&&!modeLoading&&timerTrial){
      customStatus.textContent=timerTrial.state==='passed'?'Отсчёт и возврат часов подтверждены. Длительный таймер и звук ещё не проверены.':
        timerTrial.state==='failed'?'Записано: есть ошибка. Опишите её в чате.':timerTrial.state==='awaiting_observation'?'Часы восстановлены. Отметьте, правильно ли шёл отсчёт.':timerTrial.message||'';
    }
    if(report.display_controls_ready&&!controlsWereReady)checksDetails.open=false;
    controlsWereReady=!!report.display_controls_ready;
    checksSummary.textContent=report.display_controls_ready?'Проверки дисплея · пройдены':'Проверки дисплея';
    mappingTitle.textContent=complete?'Карта сегментов · завершено':`Карта сегментов · ${mapped.length}/${report.clock_segments.length}`;
    if(complete&&!wasComplete)mapping.open=false;
    if(!complete||report.blocked)mapping.open=true;
    wasComplete=complete;
    display.hidden=!complete&&!trial;
    displayRun.disabled=busy||report.busy||report.blocked||report.offline||!report.display_test_ready;
    for(const {node,key} of checkButtons){
      const latest=report.trials.filter(t=>t.component==='clock_display'&&(t.display_check||t.check||'digits')===key).at(-1);
      node.textContent=checks[key][0]+(latest?.state==='passed'?' ✓':'');
      node.setAttribute('aria-pressed',String(key===selectedCheck));node.disabled=busy||report.busy;
    }
    const [label,sequence,seconds]=checks[selectedCheck];
    displayTitle.textContent=label;
    displayHint.textContent=`Поставьте музыку на паузу и смотрите на колонку: ${sequence}. По 2 секунды на шаг. Затем прежние часы вернутся автоматически. Во время проверки не обращайтесь к Алисе.`;
    displayRun.textContent=selectedCheck==='digits'?'Проверить цифры · 8 с':`Проверить · ${seconds} с`;
    displayAnswers.hidden=!(trial?.state==='awaiting_observation'&&trial.restored&&!report.blocked);
    displayAnswers.children[0].textContent=selectedCheck==='digits'?'Все четыре надписи правильные':selectedCheck==='brightness'?'Яркость менялась правильно':selectedCheck==='degree'?'Цифры и градус правильные':'Все цифры правильные';
    for(const button of displayAnswers.children)button.disabled=busy||report.busy||report.blocked;
    const messages={awaiting_observation:'Часы восстановлены. Все четыре надписи выглядели правильно? Отметьте результат ниже.',
      passed:'Записано: все четыре надписи правильные. Проверка яркости и режимов часов ещё впереди.',
      failed:'Записано: есть ошибка. Опишите в чате, какая надпись выглядела неверно.',
      not_observed:'Записано: не успел посмотреть. Можно повторить одной кнопкой.'};
    displayStatus.textContent=displayMessage||(trial?.state==='passed'&&selectedCheck!=='digits'?'Совпадение записано, исходное состояние восстановлено.':messages[trial?.state])||trial?.message||
      (report.display_test_ready?'Карта готова. Нажмите «Проверить», когда будете смотреть на колонку.':report.display_test_reason||'Сначала завершите карту сегментов.');
    progress.textContent=`Сопоставлено: ${mapped.length}. Вне схемы: ${unmapped.length} — назначение не установлено. Осталось без отметки: ${Math.max(0,report.clock_segments.length-mapped.length-unmapped.length)}.`;
    show.disabled=busy||report.busy||report.blocked||report.offline||!picker.value;
    photoAll.disabled=busy||report.busy||report.blocked||report.offline||!report.clock_segments.length;
    photoDim.disabled=photoAll.disabled||!picker.value;
    next.disabled=busy||report.busy||!report.clock_segments.length||mappingComplete();refresh.disabled=busy;
    const ready=!!current&&current.segment===picker.value&&current.state==='awaiting_observation'&&!report.blocked&&!report.busy;
    picker.disabled=busy||report.busy;save.disabled=busy||!ready||!description.value.trim();missed.disabled=busy||!ready;outside.disabled=busy||!ready;
    restore.hidden=!report.blocked;restore.disabled=busy||report.busy;
    map.setEnabled(ready&&!busy);photoMap.setEnabled(ready&&!busy);description.disabled=busy||!ready;
    summary.textContent=`Записанные расположения: ${mapped.length}; вне схемы: ${unmapped.length}`;list.replaceChildren();
    for(const trial of mapped)list.append(el('li',`${trial.segment.replace('cucumber:white:','')}: ${trial.description}`));
    for(const trial of unmapped)list.append(el('li',`${trial.segment.replace('cucumber:white:','')} (вне схемы): ${trial.description}`));
  }
  async function reload(catalog=false){
    const selected=picker.value;
    try{report=await request(catalog?'/api/midi/tests/clock':'/api/midi/tests');}
    catch(error){if(!catalog||!error.offline)throw error;report=await request('/api/midi/tests');report.offline=true;displayMessage='Колонка отключена. Карта сохранена. Когда связь вернётся, обновите страницу, чтобы запустить проверку цифр.';}
    picker.replaceChildren();
    if(report.clock_reference){photoMap.setReference(report.clock_reference);if(!hasPhoto)classic.open=false;hasPhoto=true;}
    for(const name of report.clock_segments){const state=latest().get(name)?.state;const option=el('option',name.replace('cucumber:white:','')+(state==='mapped'?' ✓':state==='unmapped'?' · вне схемы':''));option.value=name;picker.append(option);}
    picker.value=report.clock_segments.includes(selected)?selected:report.clock_segments.find(n=>!recorded(latest().get(n)))||report.clock_segments[0]||'';
    current=selectedTrial();
    render();
  }
  function chooseNext(){const nodes=report.clock_segments,start=nodes.indexOf(picker.value),order=nodes.slice(start+1).concat(nodes.slice(0,start+1));picker.value=order.find(n=>!recorded(latest().get(n)))||nodes[(start+1)%nodes.length]||'';current=selectedTrial();clearSelection();render();}
  const show=button('Показать сегмент на 3 с',async()=>{
    if(busy||report.busy||report.blocked||!picker.value)return;
    clearSelection();busy=true;render();status.textContent=`Смотрите на дисплей: ${picker.value.replace('cucumber:white:','')}…`;
    try{const result=await request('/api/midi/tests/run',{component:'clock',segment:picker.value});await reload();current=result.trial;
      status.textContent=result.trial.state==='awaiting_observation'?'Сегмент восстановлен. Нажмите на фото или схеме то место, которое изменилось.':result.trial.message;
    }catch(error){status.textContent=error.message;try{await reload();}catch{report.blocked=true;}}
    finally{busy=false;render();}
  },tools);
  const next=button('Выбрать следующий',chooseNext,tools);
  const refresh=button('Обновить сегменты',async()=>{try{await reload(true);status.textContent=mappingComplete()?completionMessage():'Список прочитан с колонки. Само чтение свет не включает.';}catch(error){status.textContent=error.message;}},tools);
  const restore=button('Восстановить и проверить дисплей',async()=>{
    if(busy||report.busy)return;busy=true;render();
    try{const result=await request('/api/midi/tests/restore',{});await reload();status.textContent=result.ok?'Восстановление подтверждено.':'Восстановление не подтверждено; пробы остановлены.';}
    catch(error){status.textContent=error.message;}finally{busy=false;render();}
  },tools);
  async function runPhoto(dim){
    if(busy||report.busy||report.blocked)return;
    clearSelection();busy=true;render();
    const name=picker.value.replace('cucumber:white:','');
    status.textContent=dim?`Сделайте фото: ${name} будет тусклее остальных на 10 секунд.`:'Сделайте фото: все сегменты включатся на 10 секунд.';
    try{const body={component:'clock_photo'};if(dim)body.segment=picker.value;
      const result=await request('/api/midi/tests/run',body);await reload();
      status.textContent=result.trial.message+(result.trial.state==='photo_ready'?(dim?` Выделенный канал: ${name}.`:' Это общий кадр всех сегментов.'):'');
    }catch(error){status.textContent=error.message;try{await reload();}catch{report.blocked=true;}}
    finally{busy=false;render();}
  }
  const photoAll=button('Фото: все сегменты · 10 с',()=>runPhoto(false),photoTools);
  const photoDim=button('Фото: выбранный тусклее · 10 с',()=>runPhoto(true),photoTools);
  async function displayAction(path,data){
    if(busy||report.busy||report.blocked)return;
    if(path.endsWith('/run')&&!report.display_test_ready)return;
    busy=true;displayMessage=path.endsWith('/run')?(data.component==='clock_text'?'Показываю '+data.text+'…':data.component==='clock_timer'?'Смотрите на колонку: 00:08 → 00:00…':'Смотрите на колонку: '+checks[selectedCheck][1]+'…'):'Сохраняю результат…';render();
    try{const result=await request(path,data);await reload();displayMessage='';return result;}
    catch(error){displayMessage=error.message;try{await reload();}catch{report.blocked=true;}}
    finally{busy=false;render();}
  }
  for(const key of Object.keys(checks)){
    const node=button(checks[key][0],()=>{selectedCheck=key;displayMessage='';render();},checkTools);
    checkButtons.push({node,key});
  }
  const displayRun=button('Проверить цифры · 8 с',()=>displayAction('/api/midi/tests/run',{component:'clock_display',check:selectedCheck}),displayTools);
  async function loadMode(mode){
    if(busy||modeLoading)return false;
    customMode=mode;modeLoading=true;customStatus.textContent='';render();
    try{
      if(mode==='timer'){
        customText.value='00:08';
      }else if(mode==='date'){
        const now=new Date();customText.value=String(now.getDate()).padStart(2,'0')+'.'+String(now.getMonth()+1).padStart(2,'0');
      }else if(mode==='temp'){
        const state=await request('/api/status'),raw=state.temp;
        const value=typeof raw==='number'||(typeof raw==='string'&&/^\d+(?:\.\d+)?$/.test(raw.trim()))?Number(raw):NaN;
        if(!Number.isFinite(value)||value<0||value>9999000)throw new Error('Не удалось прочитать температуру процессора');
        customText.value=String(Math.round(value/1000)).padStart(4,' ')+'°';
      }
      return true;
    }catch(error){customText.value='';customStatus.textContent=error.message;return false;}
    finally{modeLoading=false;render();}
  }
  for(const key of Object.keys(modeNames))modeButtons.push({key,node:button(modeNames[key],()=>loadMode(key),modeTools)});
  customText.oninput=()=>render();
  const customButton=button('Показать · 8 с',async()=>{
    if(customMode!=='text'&&!await loadMode(customMode))return;
    const timer=customMode==='timer';customStatus.textContent=timer?'Смотрите на колонку: 00:08 → 00:00…':'Показываю надпись…';
    const body=timer?{component:'clock_timer',seconds:8,brightness:Number(customBrightness.value)}:{component:'clock_text',text:customText.value,brightness:Number(customBrightness.value),mode:customMode};
    const result=await displayAction('/api/midi/tests/run',body);
    customStatus.textContent=result?.trial?.state==='completed'?'Надпись показана. Прежние часы восстановлены.':result?.trial?.message||displayMessage||'Не удалось показать надпись';
  },customForm);customButton.className='btn primary';
  for(const [label,observation] of [['Отсчёт верный, часы вернулись','matched'],['Ошибка таймера','wrong'],['Повторить позже','missed']])
    button(label,()=>{const trial=report.trials.filter(t=>t.component==='clock_timer').at(-1);if(trial?.state==='awaiting_observation'&&trial.restored)return displayAction('/api/midi/tests/observe',{id:trial.id,observation});},timerAnswers);
  customForm.replaceChildren(modeTools,customText,brightnessRow,customButton,customStatus,timerAnswers);
  const customHint=el('p','Краткий показ текста, даты или температуры процессора. Через 8 секунд возвращаются обычные часы. Дата и температура проходят приёмку.');customHint.className='hint';customForm.append(customHint);
  for(const [label,observation] of [['Все четыре надписи правильные','matched'],['Есть ошибка','wrong'],['Не успел посмотреть','missed']])
    button(label,()=>{const trial=displayTrial();if(trial?.state==='awaiting_observation'&&trial.restored)return displayAction('/api/midi/tests/observe',{id:trial.id,observation});},displayAnswers);
  async function observe(observation){
    if(!current||busy)return;
    if(observation==='located'&&!description.value.trim()){status.textContent='Нажмите на нужное место на фото или схеме.';return;}
    busy=true;render();
    try{const body={id:current.id,observation,description:observation==='outside_map'&&(selectedPosition||selectedPhotoPoint)?'':description.value};if(observation==='located'&&selectedPosition)body.position=selectedPosition;
      if(observation==='located'&&selectedPhotoPoint)body.photo_point=selectedPhotoPoint;
      await request('/api/midi/tests/observe',body);await reload();
      if(mappingComplete()){current=null;clearSelection();status.textContent=completionMessage();}
      else if(observation!=='missed'){chooseNext();status.textContent=observation==='located'?'Расположение записано. Следующий сегмент выбран, но ещё не включён.':'Отмечено «вне схемы»: назначение не установлено. Следующий канал выбран, но ещё не включён.';}
      else{current=null;status.textContent='Отмечено «не заметил». Можно повторить эту пробу.';}
    }catch(error){status.textContent=error.message;}finally{busy=false;render();}
  }
  const save=button('Записать и выбрать следующий',()=>observe('located'),answerButtons);
  const outside=button('Нет на схеме',()=>observe('outside_map'),answerButtons);
  const missed=button('Не заметил',()=>observe('missed'),answerButtons);
  picker.onchange=()=>{current=selectedTrial();clearSelection();render();};
  observations.append(photoMap.element,classic,selection,outsideHint,custom,answerButtons);
  checksDetails.append(checksSummary,checkTools,displayTitle,displayHint,displayTools,displayStatus,displayAnswers);
  display.append(customForm,checksDetails);
  mapping.append(mappingTitle,intro,progress,picker,tools,photoHint,photoTools,status,observations,history);
  card.append(head);
  const preview=document.getElementById?.('clock-preview');
  if(preview){preview.textContent='Читаю дисплей…';card.append(preview);}
  const liveHint=el('p','Дисплей и 3D-модель показывают состояние колонки. Краткий показ автоматически возвращает часы. Таймер проходит проверку; закрепление пока недоступно.');liveHint.className='hint';
  card.append(liveHint,display,mapping);
  const anchor=document.querySelector('[data-widget="midi-tests"]')||document.getElementById('model-view');anchor?.after(card);
  status.textContent='Читаю реальные сегменты с колонки…';render();
  reload(true).then(()=>{status.textContent=mappingComplete()?completionMessage():'Выберите сегмент и нажмите «Показать». Расположение заранее не предполагается.';})
    .catch(error=>{report.blocked=true;status.textContent=error.message;render();});
}
