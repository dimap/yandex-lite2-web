// Owner-operated, bounded colour trials. Regular hardware flags remain separate.
export function mountMidiTests(profile) {
  if (profile.id !== 'midi') return;
  const card = document.createElement('section');
  card.className = 'card'; card.dataset.widget = 'midi-tests'; card.id = 'midi-tests';
  const heading = document.createElement('h2'); heading.textContent = 'Подсветка';
  const badge=document.createElement('span');badge.className='badge';
  const head=document.createElement('div');head.className='card-head';head.append(heading,badge);
  const intro = document.createElement('p'); intro.className = 'hint';
  intro.textContent = 'Выберите цвет кольца или логотипа: он включится на 2 секунды с малой яркостью, затем вернётся прежний свет. Постоянный цвет и анимации ещё проходят приёмку.';
  const status = document.createElement('p'); status.className = 'hint'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  const observation = document.createElement('div'); observation.className = 'chips';
  const rows = [], actions = [], controls = [], selected={ring:'red',logo:'red'},
    labels = {ring:'Кольцо', logo:'Логотип',ring_controls:'Управление кольцом',logo_controls:'Управление логотипом'};
  const colors = {red:'Красный', green:'Зелёный', blue:'Синий', yellow:'Жёлтый'};
  let report = {trials:[], blocked:false, busy:false}, busy = false, current = null;
  const button = (label, action, parent) => {
    const node = document.createElement('button'); node.type = 'button'; node.className = 'btn sm'; node.textContent = label;
    node.onclick = action; parent.append(node); return node;
  };
  const request = async (path, data) => {
    const response = await PanelContext.fetch(path, data === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(result.error || 'Не удалось выполнить проверку');
    return result;
  };
  function render() {
    let passed = 0;
    for (const row of rows) {
      const history=[...report.trials].reverse().filter(t => t.component === row.component && t.color === row.color);
      const trial = history.find(t=>t.state==='passed'||t.state==='failed')||history[0];
      const ok = trial?.state === 'passed'; if (ok) passed++;
      row.button.textContent = ok ? '✓' : '';
      row.button.setAttribute('title',colors[row.color]+(ok?' — проверен':trial?.state==='failed'?' — не совпал':''));
      const selectedColor=!!report.rgb_controls?.[row.component]?.ready&&selected[row.component]===row.color;
      row.button.className=selectedColor?'sw sel':'sw';
      row.button.setAttribute('aria-pressed',String(selectedColor));
      row.button.disabled = busy || report.busy || report.blocked;
    }
    badge.textContent = `Проверка · ${passed}/8`;
    for(const c of controls){
      const capability=report.rgb_controls?.[c.component]||{},locked=busy||report.busy||report.blocked;
      c.verify.hidden=!!capability.ready;c.verify.disabled=locked||!capability.colors_ready;
      c.on.hidden=c.off.hidden=!capability.ready;c.reset.hidden=!capability.can_reset;
      c.on.disabled=c.off.disabled=c.reset.disabled=locked;
    }
    actions[0].textContent=current?.component?.endsWith('_controls')?'Всё сработало':'Цвет совпал';
    intro.textContent=controls.some(c=>report.rgb_controls?.[c.component]?.ready)
      ? 'Выберите цвет и нажмите «Включить». Доступны проверенные цвета на малой яркости. «Сбросить» возвращает прежний свет и управление Алисе.'
      : 'Цвет включается на 2 секунды с малой яркостью и возвращается обратно. После проверки управления откроются «Включить», «Выключить» и «Сбросить».';
    restore.hidden = !report.blocked; restore.disabled = busy || report.busy;
    refresh.disabled = busy; exportButton.disabled = busy || report.busy;
    observation.hidden = !current || current.state !== 'awaiting_observation' || report.blocked || report.busy;
    actions.forEach(node => {node.disabled = busy;});
  }
  async function reload() {
    report = await request('/api/midi/tests');
    const previous=current;
    current = [...report.trials].reverse().find(t => Object.hasOwn(labels,t.component) && t.state === 'awaiting_observation'
      && !report.rgb_controls?.[t.target_component||t.component.replace('_controls','')]?.ready) || null;
    if(previous&&!current)status.textContent='Управление проверено. Выберите цвет и нажмите «Включить».';
    render();
  }
  async function run(component, color) {
    if (busy || report.busy || report.blocked) return;
    busy = true; render(); observation.hidden = true;
    const description=component.endsWith('_controls')?'красный → погаснет → зелёный → прежний свет':colors[color].toLowerCase();
    status.textContent = `${labels[component]}: ${description} — смотрите на колонку. Затем проверяю восстановление…`;
    try {
      const result = await request('/api/midi/tests/run', {component, color});
      await reload(); current = result.trial;
      status.textContent = result.trial.state==='not_run' ? result.trial.message : result.trial.restored && result.trial.state === 'awaiting_observation'
        ? `${labels[component]}: ${description}. Исходное состояние восстановлено${result.trial.initial_mismatch ? ' после повторной сверки' : ''}. Что вы увидели?`
        : result.trial.restored ? 'Проба завершилась с ошибкой, исходное состояние восстановлено. Можно повторить.'
        : 'Восстановление не подтверждено. Новые пробы остановлены; нажмите «Восстановить и проверить».';
    } catch (error) {
      status.textContent = error.message;
      try { await reload(); } catch { report.blocked = true; }
    } finally { busy = false; render(); }
  }
  async function observe(value) {
    if (!current || busy) return;
    busy = true; render();
    try {
      await request('/api/midi/tests/observe', {id:current.id, observation:value});
      status.textContent = value === 'matched' ? 'Совпадение записано. Можно выбрать следующий цвет.'
        : value === 'wrong' ? 'Несовпадение записано. Можно проверить другие цвета.' : 'Отмечено «не заметил». Нажмите нужный цвет ещё раз.';
      current = null; await reload();
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; render(); }
  }
  async function control(component, action) {
    if(busy||report.busy)return;
    busy=true;render();
    const values={red:[8,0,0],green:[0,8,0],blue:[0,0,8],yellow:[8,8,0]};
    const rgb=action==='off'?[0,0,0]:values[selected[component]];
    try{
      await request('/api/'+component+(action==='reset'?'/reset':''),action==='reset'?{}:Object.fromEntries(['red','green','blue'].map((c,i)=>[c,rgb[i]])));
      status.textContent=action==='reset'?'Прежний свет восстановлен.':action==='off'?'Свет погашен. «Сбросить» вернёт прежнее состояние.':'Цвет включён на малой яркости. «Сбросить» вернёт прежнее состояние.';
    }catch(error){status.textContent=error.message;}
    finally{busy=false;render();}
  }
  card.append(head, intro);
  for (const component of ['ring', 'logo']) {
    const title = document.createElement('h3'); title.textContent = labels[component];
    const row = document.createElement('div'); row.className = 'swatches';
    for (const color of Object.keys(colors)) {
      const node = button(colors[color], () => {
        if(report.rgb_controls?.[component]?.ready){selected[component]=color;render();return;}
        return run(component,color);
      }, row);
      node.className='sw';node.setAttribute('style','--c:'+({red:'#FF0000',green:'#00FF00',blue:'#0000FF',yellow:'#FFFF00'})[color]+';color:#101014;font-weight:800');
      node.setAttribute('aria-label', `${labels[component]}: ${colors[color].toLowerCase()}`);
      rows.push({component,color,button:node});
    }
    card.append(title, row);
    const buttons=document.createElement('div');buttons.className='row';
    const on=button('Включить',()=>control(component,'on'),buttons);on.className='btn sm primary';
    const off=button('Выключить',()=>control(component,'off'),buttons);
    const reset=button('Сбросить',()=>control(component,'reset'),buttons);
    const verify=button('Проверить управление '+(component==='ring'?'кольцом':'логотипом')+' · 5 с',()=>run(component+'_controls'),buttons);
    controls.push({component,on,off,reset,verify});card.append(buttons);
  }
  for (const [label, value] of [['Цвет совпал','matched'], ['Другой цвет','wrong'], ['Не заметил','missed']])
    actions.push(button(label, () => observe(value), observation));
  const tools = document.createElement('div'); tools.className = 'chips';
  const restore = button('Восстановить и проверить', async () => {
    if (busy || report.busy) return;
    busy = true; render(); status.textContent = 'Восстанавливаю и сверяю состояние…';
    try { const result = await request('/api/midi/tests/restore', {}); await reload(); status.textContent = result.ok ? 'Восстановление подтверждено.' : 'Восстановление не подтверждено. Дальнейшие пробы остановлены.'; }
    catch (error) {status.textContent = error.message;}
    finally {busy = false; render();}
  }, tools);
  const refresh = button('Обновить результаты', async () => {
    try {await reload(); status.textContent = report.busy ? 'Проба ещё выполняется.' : report.blocked ? 'Пробы остановлены до восстановления.' : 'Результаты загружены.';}
    catch(error) {status.textContent = error.message;}
  }, tools);
  const exportButton = button('Скачать отчёт', () => {
    const blob = new Blob([JSON.stringify(report,null,2)], {type:'application/json'});
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = 'midi-hardware-tests.json'; link.click(); URL.revokeObjectURL(url);
  }, tools);
  card.append(status, observation, tools);
  const anchor = document.querySelector('[data-widget="light"]');
  if (anchor) anchor.after(card); else document.getElementById('model-view')?.after(card);
  status.textContent = 'Загружаю результаты…'; render();
  reload().then(() => {status.textContent = report.blocked ? 'Пробы остановлены до восстановления.' : 'Выберите компонент и цвет. Проверенные цвета отмечены галочкой.';})
    .catch(error => {report.blocked = true; status.textContent = error.message; render();});
  globalThis.setInterval?.(async()=>{
    if(busy||document.hidden||document.getElementById?.('tab-overview')?.hidden)return;
    try{await reload();}catch{} // Preserve the current message during a brief disconnect.
  },4000);
}
