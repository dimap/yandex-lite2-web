/*  Мини-плеер YouTube для панели «Станция».
 *
 *  Карточка #yt-card (data-widget="yt") стоит в правой колонке над «Сейчас играет».
 *  Воспроизведение — официальный IFrame Player API на youtube-nocookie.com: видео играет
 *  в том браузере, где открыта панель, звук идёт из динамика этого устройства.
 *  На колонку команды не отправляются — это отдельный, независимый плеер.
 *
 *  Почему именно так:
 *    • встраивать можно только ролики, разрешившие embed (иначе ошибка 101/150);
 *    • без жеста пользователя браузер не даёт звук (обрабатываем onAutoplayBlocked);
 *    • панель отдаёт страницы с Referrer-Policy: no-referrer, а без Referer YouTube
 *      отвечает ошибкой 153 — поэтому на iframe выставлен referrerpolicy
 *      strict-origin-when-cross-origin (атрибут элемента приоритетнее заголовка документа);
 *    • если youtube.com/iframe_api не открывается, карточка показывает «нет доступа»
 *      и ссылку «Открыть на YouTube», а не висит молча.
 *
 *  Модуль самостоятельный: работает с уже отрисованной карточкой, помнит последнее
 *  видео, громкость и историю (localStorage), не трогает остальную панель.
 */
(() => {
  'use strict';

  const card = document.getElementById('yt-card');
  if (!card) return; // карточки на странице нет — модуль не нужен

  // ---------- утилиты (не зависим от внутренних хелперов панели) ----------
  const el = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const clamp = (v, lo, hi, dflt) => { v = parseInt(v, 10); return Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : dflt; };
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { } },
    json(k, dflt) { try { const v = JSON.parse(store.get(k)); return v == null ? dflt : v; } catch { return dflt; } },
    setJson(k, v) { store.set(k, JSON.stringify(v)); },
  };
  const key = n => (window.PanelContext && PanelContext.storageKey ? PanelContext.storageKey(n) : n);
  const K = {
    video: key('yt.video'),
    recent: key('yt.recent'),
    vol: key('yt.vol'),
    muted: key('yt.muted'),
    pos: id => key('yt.pos.' + id),
  };
  // toast() объявлен в общем скрипте панели: typeof безопасен, если его там нет
  const say = msg => { try { if (typeof toast === 'function') toast(msg); } catch { } };

  // ---------- элементы карточки ----------
  const box = el('yt-box');
  const frameSlot = el('yt-frame');
  const veilEl = el('yt-ov'), veilTitle = el('yt-ov-title'), veilText = el('yt-ov-text');
  const badge = el('yt-badge'), openLink = el('yt-open');
  const titleEl = el('yt-title'), subEl = el('yt-sub');
  const seek = el('yt-seek'), posEl = el('yt-pos'), durEl = el('yt-dur');
  const playBtn = el('yt-play'), muteBtn = el('yt-mute'), volInput = el('yt-vol'), fsBtn = el('yt-fs');
  const form = el('yt-form'), urlInput = el('yt-url'), recentEl = el('yt-recent');

  // без разметки модуль работать не может — молча выходим
  if (!frameSlot || !veilEl || !form) return;

  // ---------- состояние ----------
  const state = {
    id: '',            // id текущего видео
    ready: false,      // плеер готов (onReady)
    playing: false,
    muted: store.get(K.muted) === '1',
    vol: clamp(store.get(K.vol), 0, 100, 80),
    scrubbing: false,
    timer: 0,
    savedAt: 0,
    api: null,         // промис загрузки IFrame API
  };
  let player = null;

  const ERR = {
    2: ['Ссылка не распознана', 'YouTube не понял идентификатор видео. Скопируйте адрес из адресной строки целиком.'],
    5: ['Браузер не смог воспроизвести', 'Плеер не сумел воспроизвести видео в этом браузере. Попробуйте другой браузер.'],
    100: ['Видео недоступно', 'Ролик удалён или скрыт владельцем.'],
    101: ['Встраивание запрещено', 'Автор запретил показ во встроенном плеере. Видео откроется на YouTube по ссылке сверху.'],
    150: ['Встраивание запрещено', 'Автор запретил показ во встроенном плеере. Видео откроется на YouTube по ссылке сверху.'],
    153: ['Плееру не хватает Referer', 'YouTube отклонил встраивание (ошибка 153): в запросе нет Referer. Откройте видео на YouTube по ссылке сверху.'],
  };

  // ---------- мелочи отрисовки ----------
  const fmt = t => {
    t = Math.max(0, Math.floor(Number(t) || 0));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : String(m)) + ':' + String(s).padStart(2, '0');
  };

  function setBadge(text, kind) {
    if (!badge) return;
    badge.hidden = !text;
    badge.textContent = text || '';
    badge.className = 'badge' + (kind ? ' ' + kind : '');
  }

  function veil(show, title, text, isError) {
    veilEl.hidden = !show;
    veilEl.classList.toggle('bad', !!isError);
    if (title != null) veilTitle.textContent = title;
    if (text != null) veilText.textContent = text;
  }

  function setPlayIcon(playing) {
    playBtn.innerHTML = '<svg width="22" height="22" aria-hidden="true"><use href="#' + (playing ? 'i-pause' : 'i-play') + '"/></svg>';
    playBtn.setAttribute('aria-label', playing ? 'Пауза' : 'Играть');
  }

  function setOpen(id) {
    if (!openLink) return;
    openLink.href = id ? 'https://www.youtube.com/watch?v=' + encodeURIComponent(id) : 'https://www.youtube.com/';
    openLink.hidden = false;
  }

  // ---------- разбор ссылки ----------
  function parseId(input) {
    const s = String(input || '').trim();
    if (!s) return '';
    if (/^[A-Za-z0-9_-]{11}$/.test(s)) return s;
    const m = s.match(/(?:youtu\.be\/|[?&]v=|\/embed\/|\/shorts\/|\/live\/|\/v\/)([A-Za-z0-9_-]{11})/);
    return m ? m[1] : '';
  }

  // ---------- история ----------
  function recent() {
    const a = store.json(K.recent, []);
    return Array.isArray(a) ? a.filter(x => x && x.id) : [];
  }

  function pushRecent(id, title) {
    if (!id) return;
    const list = recent().filter(x => x.id !== id);
    list.unshift({ id, title: title || ('youtube.com/watch?v=' + id) });
    store.setJson(K.recent, list.slice(0, 8));
    renderRecent();
  }

  function renderRecent() {
    if (!recentEl) return;
    const list = recent();
    recentEl.hidden = !list.length;
    recentEl.innerHTML = list.map(x =>
      '<button type="button" class="chip" data-id="' + esc(x.id) + '" title="' + esc(x.title) + '">' +
      (x.id === state.id ? '▶ ' : '') + esc(x.title.slice(0, 42)) + '</button>').join('');
    [...recentEl.querySelectorAll('button')].forEach(b => {
      b.onclick = () => { urlInput.value = 'https://youtu.be/' + b.dataset.id; play(b.dataset.id); };
    });
  }

  // ---------- IFrame API ----------
  function loadApi() {
    if (window.YT && window.YT.Player) return Promise.resolve();
    if (state.api) return state.api;
    state.api = new Promise((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => { if (!settled) { settled = true; reject(new Error('timeout')); } }, 15000);
      window.onYouTubeIframeAPIReady = () => {
        if (settled) return;
        settled = true; clearTimeout(timer); resolve();
      };
      const s = document.createElement('script');
      s.src = 'https://www.youtube.com/iframe_api';
      s.async = true;
      s.onerror = () => { if (!settled) { settled = true; clearTimeout(timer); reject(new Error('net')); } };
      document.head.append(s);
    }).catch(e => { state.api = null; throw e; });
    return state.api;
  }

  function embedUrl(id, start) {
    const p = new URLSearchParams({
      enablejsapi: '1',
      autoplay: '1',
      controls: '0',
      rel: '0',
      playsinline: '1',
      modestbranding: '1',
      iv_load_policy: '3',
      disablekb: '0',
      origin: location.origin,
    });
    if (start > 0) p.set('start', String(Math.floor(start)));
    return 'https://www.youtube-nocookie.com/embed/' + encodeURIComponent(id) + '?' + p.toString();
  }

  function createPlayer(id, start) {
    const frame = document.createElement('iframe');
    frame.id = 'yt-iframe';
    frame.title = 'Плеер YouTube';
    frame.setAttribute('allow', 'autoplay; encrypted-media; picture-in-picture; fullscreen');
    frame.setAttribute('allowfullscreen', '');
    // Referrer обязателен: панель отдаёт документ с Referrer-Policy: no-referrer,
    // а без Referer YouTube отклоняет встраивание ошибкой 153.
    frame.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
    frame.src = embedUrl(id, start);
    frameSlot.textContent = '';
    frameSlot.append(frame);
    player = new window.YT.Player(frame, {
      events: {
        onReady: onReady,
        onStateChange: onStateChange,
        onError: onError,
        onAutoplayBlocked: onAutoplayBlocked,
      },
    });
  }

  // ---------- события плеера ----------
  function onReady() {
    state.ready = true;
    try {
      player.setVolume(state.vol);
      if (state.muted) player.mute();
    } catch { }
    seek.disabled = false;
    setPlayIcon(true);
    startTicker();
    setTimeout(updateMeta, 400);
  }

  function onStateChange(e) {
    const s = e.data;
    if (s === 1) {
      state.playing = true;
      setPlayIcon(true);
      setBadge('играет', 'ok');
      veil(false);
      startTicker();
      updateMeta();
    } else if (s === 2) {
      state.playing = false;
      setPlayIcon(false);
      setBadge('пауза');
      stopTicker();
      updateMeta();
      rememberPos();
    } else if (s === 0) {
      state.playing = false;
      setPlayIcon(false);
      setBadge('закончилось');
      stopTicker();
      seek.value = seek.max;
    } else if (s === 3) {
      setBadge('буферизация');
    } else if (s === 5 || s === -1) {
      setBadge('готов');
    }
  }

  function onError(e) {
    const info = ERR[e.data] || ['Ошибка плеера ' + e.data, 'YouTube не смог показать это видео. Попробуйте открыть его на YouTube.'];
    state.playing = false;
    setPlayIcon(false);
    stopTicker();
    setBadge('ошибка', 'bad');
    veil(true, info[0], info[1], true);
    say(info[0]);
  }

  function onAutoplayBlocked() {
    setBadge('нужен клик');
    setPlayIcon(false);
    veil(true, 'Браузер не дал включить звук',
      'Нажмите ▶ под плеером — после нажатия браузер разрешит воспроизведение.', false);
  }

  // ---------- позиция ----------
  function startTicker() {
    if (state.timer) return;
    state.timer = setInterval(tick, 300);
  }

  function stopTicker() {
    if (state.timer) { clearInterval(state.timer); state.timer = 0; }
  }

  function tick() {
    if (!state.ready || state.scrubbing) return;
    let t = 0, d = 0;
    try { t = player.getCurrentTime() || 0; } catch { return; }
    try { d = player.getDuration() || 0; } catch { }
    if (d > 0) {
      if (seek.max !== String(Math.floor(d))) seek.max = String(Math.floor(d));
      durEl.textContent = fmt(d);
    }
    seek.value = String(Math.floor(t));
    posEl.textContent = fmt(t);
    if (state.playing) rememberPos(t);
  }

  function rememberPos(t) {
    if (!state.id || !state.ready) return;
    const now = Date.now();
    if (now - state.savedAt < 5000) return;
    state.savedAt = now;
    if (t == null) { try { t = player.getCurrentTime(); } catch { return; } }
    store.set(K.pos(state.id), String(Math.floor(t || 0)));
  }

  function updateMeta() {
    if (!state.ready) return;
    let d = null;
    try { d = player.getVideoData(); } catch { }
    if (!d) return;
    if (d.title) {
      titleEl.textContent = d.title;
      subEl.textContent = d.author ? d.author + ' · YouTube' : 'YouTube';
      if (d.video_id) { state.id = d.video_id; setOpen(d.video_id); pushRecent(d.video_id, d.title); }
    }
  }

  // ---------- запуск видео ----------
  function needUrl() {
    veil(true, 'Нужна ссылка', 'Вставьте адрес видео YouTube в поле ниже или выберите из недавних.', false);
    setBadge('');
    try { urlInput.focus(); } catch { }
  }

  async function play(rawId) {
    const id = parseId(rawId);
    if (!id) { needUrl(); return; }

    state.id = id;
    store.set(K.video, id);
    setOpen(id);
    veil(false);
    setBadge('загрузка');

    const start = clamp(store.get(K.pos(id)), 0, 86400, 0);

    if (!state.ready) {
      if (!(window.YT && window.YT.Player)) {
        try {
          await loadApi();
        } catch (e) {
          setBadge('нет доступа', 'bad');
          veil(true, 'YouTube не открывается',
            'Браузер не смог загрузить IFrame API YouTube. Проверьте доступ к youtube.com (VPN, DNS, DPI) и нажмите ▶ ещё раз.',
            true);
          return;
        }
      }
      createPlayer(id, start);
      return;
    }

    try {
      player.loadVideoById({ videoId: id, startSeconds: start });
      setPlayIcon(true);
      startTicker();
    } catch (e) {
      // плеер потерял iframe — создаём заново
      state.ready = false;
      createPlayer(id, 0);
    }
  }

  // ---------- элементы управления ----------
  playBtn.onclick = () => {
    if (!state.ready) {
      const id = state.id || parseId(urlInput.value) || (recent()[0] && recent()[0].id) || '';
      if (id) play(id); else needUrl();
      return;
    }
    let playing = false;
    try { playing = player.getPlayerState() === 1; } catch { }
    if (playing) player.pauseVideo(); else player.playVideo();
  };

  // Клик по затемнению плеера: пусто — ставим курсор в поле ссылки, «нужен клик» — играем.
  veilEl.addEventListener('click', () => {
    if (state.ready) { try { player.playVideo(); } catch { } return; }
    const id = state.id || parseId(urlInput.value) || (recent()[0] && recent()[0].id) || '';
    if (id) play(id);
    else { try { urlInput.focus(); } catch { } }
  });

  seek.addEventListener('pointerdown', () => { state.scrubbing = true; });
  seek.addEventListener('input', () => { posEl.textContent = fmt(+seek.value); });
  const commitSeek = () => {
    if (!state.scrubbing) return;
    state.scrubbing = false;
    if (state.ready) { try { player.seekTo(+seek.value, true); } catch { } }
    rememberPos(+seek.value);
  };
  seek.addEventListener('change', commitSeek);
  seek.addEventListener('pointerup', commitSeek);

  function applyVolume() {
    if (state.ready) { try { player.setVolume(state.vol); } catch { } }
  }

  function setMuted(m) {
    state.muted = !!m;
    store.set(K.muted, state.muted ? '1' : '0');
    if (state.ready) { try { if (state.muted) player.mute(); else player.unMute(); } catch { } }
    muteBtn.setAttribute('aria-pressed', state.muted ? 'true' : 'false');
    muteBtn.classList.toggle('on', state.muted);
    muteBtn.title = state.muted ? 'Включить звук' : 'Выключить звук';
  }

  volInput.value = String(state.vol);
  volInput.oninput = () => {
    state.vol = clamp(volInput.value, 0, 100, 80);
    if (state.muted && state.vol > 0) setMuted(false);
    store.set(K.vol, String(state.vol));
    applyVolume();
  };
  muteBtn.onclick = () => setMuted(!state.muted);
  setMuted(state.muted);

  fsBtn.onclick = () => {
    if (document.fullscreenElement) { if (document.exitFullscreen) document.exitFullscreen(); return; }
    if (card.requestFullscreen) card.requestFullscreen().catch(() => say('Браузер не дал развернуть'));
    else say('Браузер не умеет полный экран');
  };
  document.addEventListener('fullscreenchange', () => {
    fsBtn.setAttribute('aria-pressed', document.fullscreenElement ? 'true' : 'false');
  });

  form.onsubmit = e => {
    e.preventDefault();
    const id = parseId(urlInput.value);
    if (!id) { needUrl(); return; }
    urlInput.value = 'https://youtu.be/' + id;
    play(id);
  };

  // ---------- начальное состояние ----------
  setPlayIcon(false);
  setBadge('');
  const last = store.get(K.video) || '';
  if (last) {
    urlInput.value = 'https://youtu.be/' + last;
    setOpen(last);
  }
  renderRecent();

  // Убрали виджет (✕ в режиме настройки) — останавливаем звук, чтобы он не играл из скрытого блока.
  const hiddenZone = document.getElementById('wz-hidden');
  if (hiddenZone && window.MutationObserver) {
    new MutationObserver(() => {
      if (hiddenZone.contains(card) && player) {
        try { player.pauseVideo(); } catch { }
      }
    }).observe(hiddenZone, { childList: true });
  }
})();
