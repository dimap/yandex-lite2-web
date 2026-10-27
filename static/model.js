// Choose the visual model from the device; hardware capabilities remain explicit.
import {rendererProfile} from './renderer-profile.js';
async function loadProfile(){
  for(;;){
    const auth=await PanelContext.fetch('/api/panel-auth').then(r=>r.json()).catch(()=>null);
    if(auth&&(!auth.enabled||auth.authed)){
      const response=await PanelContext.fetch('/api/profile');if(response.ok)return response.json();
    }
    await new Promise(resolve=>setTimeout(resolve,2000));
  }
}
const profile=await loadProfile();
window.clockGeom.layout=profile.clock_layout;
window.clockPreview();
window.renderBoot();
const view=document.getElementById('model-view');
const loading=document.createElement('p');loading.className='studio-caption';loading.textContent='Ожидаю данные колонки…';view.append(loading);
let status;
for(;;){
  try{
    const response=await PanelContext.fetch('/api/status');
    if(response.ok){const value=await response.json();if(value.hostname||value.hardware_model){status=value;break;}}
  }catch{}
  await new Promise(resolve=>setTimeout(resolve,2000));
}
const visual=rendererProfile(profile,status);
loading.remove();
const gallery = document.getElementById("model-gallery");
if (gallery) gallery.href = PanelContext.url("static/model-preview.html") + "?model=" + encodeURIComponent(visual.id);
window.__deviceColor=status.color||window.__deviceColor;
document.title=visual.name+' — панель';
document.getElementById('model-name').textContent=visual.name;
view.dataset.hardwareProfile=profile.id;
if(visual.id!==profile.id){
  const note=document.createElement('p');note.className='hint';note.id='model-profile-note';
  note.textContent=visual.id==='lite'
    ? 'Станция Лайт первого поколения. Для управления пока используется профиль «'+profile.name+'»; совместимость аппаратных функций не подтверждена.'
    : '3D: '+visual.name+'. Профиль управления: '+profile.name+'. Для аппаратных функций выберите --model '+visual.id+' при запуске сервера.';
  view.after(note);
}
const hide=element=>element?.setAttribute('data-profile-hidden','');
if(!profile.clock)hide(document.querySelector('[data-widget="clock"]'));
if(!profile.light)hide(document.querySelector('[data-widget="light"]'));
if(!profile.boot)hide(document.getElementById('privacy-row'));
if(!profile.boot&&profile.id!=='mini2'){hide(document.querySelector('a[href="#boot"]'));const tab=document.querySelector('#tab-boot');for(const child of tab.children)hide(child);tab.insertAdjacentHTML('afterbegin','<p class="card">Редактор запуска не поддерживается выбранным профилем.</p>');}
if(!profile.alarms){if(profile.id!=='mini2')hide(document.querySelector('a[href="#alarm"]'));const tab=document.querySelector('#tab-alarm');for(const child of tab.children)hide(child);tab.insertAdjacentHTML('afterbegin','<p class="card">Автозапуск будильника этой модели ещё не поддерживается. Существующие будильники не изменяются.</p>');}
if(profile.id!=='lite2'){
  hide(document.getElementById('pat-play'));
  document.querySelectorAll('[data-k="clock"]').forEach(e=>{if(!profile.clock)hide(e);});
  document.querySelector('[data-widget="light"] .hint').textContent=profile.light_note;
}
if(profile.id==='mini2'){
  document.querySelector('#tab-alarm > p').textContent='Служба будильника панели на Мини 2 не установлена. Для неё нужны адаптация звука и подсветки и проверенный автозапуск; штатные будильники Алисы работают отдельно.';
  document.querySelector('#bk-save').closest('.set-item').querySelector('small').textContent='Для Мини 2 копия сохраняет оформление и раскладку браузера. Восстановление системных файлов панели из копий других моделей не поддерживается. Пароль, вход в Яндекс и история графиков в копию не входят.';
  document.querySelector('#fp-reset').closest('.set-item').querySelector('small').textContent='Постоянная установка функций панели на Мини 2 не поддерживается; системный сброс недоступен.';
  hide(document.querySelector('#tab-boot > .cols'));
  hide(document.getElementById('boot-install').parentElement);
  const original=document.getElementById('boot-load-default');original.disabled=true;original.title='В этой прошивке Мини 2 нет стандартной заставки часов';
  document.querySelector('#tab-boot .sub').textContent='Редактор кадров и проигрывание на колонке. Установка при загрузке для Мини 2 пока не поддерживается.';
  document.getElementById('boot-pause-hint').textContent='Пауза перед проигрыванием анимации. Редактор не меняет заставку при загрузке колонки.';
  hide(document.querySelector('#vol-bt').closest('.vol-bt'));
  document.getElementById('disp-hint').textContent='Закреплённый режим работает, пока запущена панель. После остановки панели или потери связи управление возвращается колонке в течение 30 секунд. «Один раз» — до штатной перерисовки. По окончании таймера играет штатный звонок.';
}
// У Лайт первого поколения нет дисплея: только светящийся логотип Алисы — прячем всё про часы.
if(visual.id==='lite'){
  hide(document.querySelector('[data-widget="clock"]'));hide(document.getElementById('pat-play'));
  document.querySelectorAll('[data-k="clock"]').forEach(hide);
}
if(visual.id==='lite2')await import('./model-lite2.js');
else{const {createModel}=await import('./model-studio.js');createModel(visual);}
