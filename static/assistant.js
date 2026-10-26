// Secrets stay in inputs only until an explicit save; no storage, logs or URL params.
(() => {
  let loaded = false, loading = false, state = {}, working = false;
  let vpnResultKind = '';
  function render(s, fill = false) {
    state = s;
    const ready = !!s.running;
    $('#ai-state').textContent = !s.installed ? 'Сервис ещё не установлен' : !ready ? 'Сервис остановлен' :
      s.busy ? ({thinking:'OpenAI готовит ответ…', speech:'Создаётся голос…', playing:'Колонка отвечает…'}[s.stage] || 'Обрабатываем вопрос…') :
      s.enabled ? 'Готов к голосовым вопросам' : 'Сервис запущен · голосовой запуск выключен';
    $('#ai-detail').textContent = ready ? `Версия ${s.version} · ${s.log_available ? 'распознавание доступно' : 'журнал распознавания пока недоступен'} · запросов: ${s.requests}` : 'ARM64 · автозапуск после включения колонки';
    $('#ai-install').textContent = s.installed ? 'Переустановить / запустить' : 'Установить сервис';
    $('#ai-uninstall').hidden = !s.installed;
    $('#ai-fields').disabled = !ready || working;
    $('#ai-vpn-fields').disabled = !ready || working;
    $('#ai-vpn-install').disabled = !ready || working || s.vpn_installed;
    $('#ai-vpn-install').textContent = s.vpn_installed ? 'OpenVPN установлен' : 'Установить OpenVPN';
    $('#vpn-prepare-row').hidden = ready;
    $('#ai-error').hidden = !s.error;
    $('#ai-error').textContent = s.error || '';
    $('#ai-key-state').textContent = s.has_key ? 'Ключ сохранён на колонке' : 'Ключ не задан';
    $('#ai-vpn-file-state').textContent = s.vpn_configured ? `Профиль сохранён${s.vpn_has_auth ? ', логин и пароль заданы' : ''}` : 'Не загружена';
    $('#ai-vpn-state').textContent = s.vpn_enabled ? (s.vpn_message || 'Подключаемся…') : 'VPN выключен';
    if (vpnResultKind === 'connection' && s.vpn_enabled) {
      vpnResult(s.vpn_connected ? 'Профиль сохранён. VPN подключён.' : s.vpn_state === 'error' ?
        'Профиль сохранён. VPN не подключён: ' + s.vpn_message : 'Профиль сохранён. Подключаем VPN…', s.vpn_state === 'error', 'connection');
    }
    $('#ai-vpn-connect').hidden = !s.vpn_configured || s.vpn_enabled;
    $('#ai-vpn-disconnect').hidden = !s.vpn_enabled;
    $('#ai-vpn-next').textContent = !ready ? 'Подготовьте колонку, чтобы сохранить профиль.' :
      !s.vpn_configured ? 'Добавьте файл или текст .ovpn и нажмите «Сохранить профиль».' :
      s.vpn_connected ? 'VPN подключён. Теперь можно перейти во вкладку OpenAI и выполнить тест.' :
      s.vpn_enabled ? (s.vpn_state === 'error' ? s.vpn_message : 'Профиль сохранён. Дождитесь подключения — статус обновится автоматически.') :
      s.vpn_needs_auth && !s.vpn_has_auth ? 'Профиль сохранён на колонке. Заполните логин и пароль подключения из кабинета Red Shield, затем нажмите «Подключить VPN».' :
      'Профиль сохранён на колонке. Нажмите «Подключить VPN», чтобы включить соединение.';
    $('#ai-example').textContent = `«Алиса, ${s.prefix || 'спроси GPT'}, почему небо голубое?»`;
    if ((fill === true || fill === 'openai') && ready) {
      $('#ai-enabled').checked = !!s.enabled;
      $('#ai-model').value = s.model;
      $('#ai-prefix').value = s.prefix;
      $('#ai-instructions').value = s.instructions;
      $('#ai-voice').value = s.voice;
    }
    if ((fill === true || fill === 'vpn') && ready) {
      $('#ai-vpn-enabled').checked = !!s.vpn_enabled;
      $('#ai-vpn-dns').value = s.vpn_dns || '1.1.1.1';
    }
    if (fill && ready) loaded = true;
  }
  window.loadAssistant = async () => {
    if (loading || working) return;
    loading = true;
    try { render(await api('/api/assistant'), !loaded); }
    catch (e) { $('#ai-state').textContent = e.message; vpnResult('Не удалось обновить состояние: ' + e.message, true); }
    finally { loading = false; }
  };
  function vpnResult(message, error = false, kind = '') {
    vpnResultKind = kind;
    const node = $('#ai-vpn-result');
    node.hidden = !message;
    node.textContent = message;
    node.style.color = error ? 'var(--danger, #e76c6c)' : '';
    node.setAttribute('role', error ? 'alert' : 'status');
  }
  async function command(button, path, data, message, clear) {
    if (working) return;
    working = true;
    render(state);
    const vpnAction = path === 'vpn' || path === 'vpn-install';
    if (button) button.disabled = true;
    if (vpnAction) vpnResult(path === 'vpn' ? 'Сохраняем настройки на колонку…' : 'Устанавливаем OpenVPN…');
    try {
      const s = await api('/api/assistant/' + path, data || {});
      clear?.();
      render(s, path === 'config' ? 'openai' : path === 'vpn' ? 'vpn' : path === 'install');
      if (message) toast(message);
      if (vpnAction) vpnResult(data?.clear ? 'Профиль удалён с колонки.' : path === 'vpn-install' ? 'OpenVPN установлен.' :
        s.vpn_enabled ? 'Профиль сохранён. Подключаем VPN…' : 'Профиль сохранён на колонке. VPN пока выключен.', false,
        path === 'vpn' && s.vpn_enabled ? 'connection' : '');
    } catch (e) {
      if (vpnAction) {
        const what = path === 'vpn-install' ? 'Не удалось установить OpenVPN' : data?.clear ? 'Не удалось удалить профиль' :
          data?.enabled && !data?.config && state.vpn_configured ? 'VPN не подключён' : 'Не сохранено';
        vpnResult(what + ': ' + e.message, true);
      }
      if (!e.offline) toast(e.message, true);
    } finally {
      working = false;
      if (button) button.disabled = false;
      render(state);
    }
  }
  $('#ai-refresh').onclick = () => window.loadAssistant();
  $('#ai-install').onclick = e => command(e.currentTarget, 'install', {}, 'Сервис установлен на колонку');
  $('#vpn-prepare').onclick = e => command(e.currentTarget, 'install', {}, 'Колонка готова к настройке');
  $('#ai-vpn-install').onclick = e => command(e.currentTarget, 'vpn-install', {}, 'OpenVPN установлен');
  $('#ai-form').onsubmit = e => {
    e.preventDefault();
    const data = {enabled:$('#ai-enabled').checked, model:$('#ai-model').value.trim(), voice:$('#ai-voice').value,
      prefix:$('#ai-prefix').value.trim(), instructions:$('#ai-instructions').value.trim()};
    if ($('#ai-key').value.trim()) data.api_key = $('#ai-key').value.trim();
    command(e.submitter, 'config', data, 'Настройки OpenAI сохранены', () => { $('#ai-key').value = ''; });
  };
  async function saveVPN(button, enabled) {
    if (working) return;
    const data = {enabled:enabled ?? $('#ai-vpn-enabled').checked, dns:$('#ai-vpn-dns').value.trim()};
    const file = $('#ai-vpn-file').files[0];
    const pasted = $('#ai-vpn-config').value.trim();
    if (file && pasted) return vpnResult('Не сохранено: выберите файл или вставьте текст профиля — нужен один вариант.', true);
    if (pasted) data.config = pasted;
    if (file) {
      if (file.size > 256*1024) return vpnResult('Не сохранено: профиль должен быть не больше 256 КБ.', true);
      try { data.config = await file.text(); } catch { return vpnResult('Не сохранено: не удалось прочитать файл.', true); }
    }
    if ($('#ai-vpn-user').value) data.user = $('#ai-vpn-user').value;
    if ($('#ai-vpn-password').value) data.password = $('#ai-vpn-password').value;
    return command(button, 'vpn', data, 'Настройки VPN сохранены', () => {
      $('#ai-vpn-file').value = ''; $('#ai-vpn-config').value = ''; $('#ai-vpn-user').value = ''; $('#ai-vpn-password').value = '';
    });
  }
  $('#ai-vpn-form').onsubmit = e => { e.preventDefault(); saveVPN(e.submitter); };
  $('#ai-vpn-connect').onclick = e => saveVPN(e.currentTarget, true);
  $('#ai-vpn-disconnect').onclick = e => command(e.currentTarget, 'vpn', {enabled:false}, 'VPN отключён');
  $('#ai-test').onclick = e => command(e.currentTarget, 'test', {}, 'Тест запущен на колонке');
  $('#ai-stop').onclick = e => command(e.currentTarget, 'stop', {}, 'Ответ остановлен');
  $('#ai-clear').onclick = e => command(e.currentTarget, 'config', {clear_key:true}, 'Ключ удалён', () => { $('#ai-key').value = ''; });
  $('#ai-vpn-clear').onclick = e => command(e.currentTarget, 'vpn', {clear:true}, 'Профиль и пароль VPN удалены', () => {
    $('#ai-vpn-file').value = ''; $('#ai-vpn-config').value = ''; $('#ai-vpn-user').value = ''; $('#ai-vpn-password').value = '';
  });
  $('#ai-uninstall').onclick = e => {
    if (confirm('Удалить помощника, ключ OpenAI и настройки VPN с колонки?'))
      command(e.currentTarget, 'uninstall', {}, 'Сервис и ключи удалены', () => { loaded = false; $('#ai-key').value = ''; });
  };
  const visible = () => online && (!$('#tab-assistant').hidden || !$('#tab-vpn').hidden);
  setInterval(() => { if (visible()) window.loadAssistant(); }, 3000);
  if (visible()) window.loadAssistant();
})();
