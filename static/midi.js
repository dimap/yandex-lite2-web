// Separate logo control. Hardware flags are enabled only after physical tests.
export function mountMidiControls(profile) {
  if (!profile.logo) return;
  const card = document.createElement('section');
  card.className = 'card';
  card.dataset.widget = 'midi-logo';
  card.dataset.title = 'Логотип Миди';
  const heading = document.createElement('h2');
  heading.textContent = 'Логотип Миди';
  const row = document.createElement('div');
  row.className = 'row';
  const color = document.createElement('input');
  color.type = 'color';
  color.value = '#100010';
  color.setAttribute('aria-label', 'Цвет логотипа Миди');
  const message = document.createElement('p');
  message.className = 'hint';
  message.textContent = '6 RGB-зон. «Сбросить» возвращает исходную яркость и управление Алисе.';
  const buttons = [];
  const action = (label, path, body) => {
    const button = document.createElement('button');
    button.className = 'btn sm';
    button.textContent = label;
    buttons.push(button);
    button.onclick = async () => {
      buttons.forEach(b => { b.disabled = true; });
      try {
        const response = await PanelContext.fetch(path, {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body()),
        });
        const result = await response.json();
        if (!response.ok || result.error) throw new Error(result.error || 'Ошибка управления логотипом');
        message.textContent = label === 'Сбросить' ? 'Исходное состояние логотипа восстановлено.' : 'Цвет логотипа установлен.';
      } catch (error) {
        message.textContent = error.message;
      } finally {
        buttons.forEach(b => { b.disabled = false; });
      }
    };
    row.append(button);
  };
  row.append(color);
  action('Включить', '/api/logo', () => Object.fromEntries(['red', 'green', 'blue'].map((name, i) => [name, parseInt(color.value.slice(1 + i * 2, 3 + i * 2), 16)])));
  action('Выключить', '/api/logo', () => ({red: 0, green: 0, blue: 0}));
  action('Сбросить', '/api/logo/reset', () => ({}));
  card.append(heading, row, message);
  document.querySelector('[data-widget="light"]')?.after(card);
}
