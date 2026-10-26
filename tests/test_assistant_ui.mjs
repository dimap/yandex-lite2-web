import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const nodes = new Map();
const $ = id => {
  if (!nodes.has(id)) nodes.set(id, {value:'', checked:false, files:[], hidden:true, disabled:false,
    textContent:'', style:{}, setAttribute(){}});
  return nodes.get(id);
};
let state = {running:true, installed:true, version:'test', model:'test', voice:'coral', prefix:'спроси gpt',
  instructions:'test', vpn_installed:true, vpn_configured:false, vpn_enabled:false, vpn_dns:'1.1.1.1'};
let failSave = true;
const writes = [];
const window = {};
const api = async (path, body) => {
  if (body === undefined) return {...state};
  writes.push({path, body});
  if (failSave) throw new Error('Ошибка профиля, строка 47');
  state = {...state, vpn_configured:true, vpn_needs_auth:true, vpn_has_auth:!!body.user && !!body.password,
    vpn_enabled:body.enabled, vpn_state:body.enabled ? 'connecting' : 'off'};
  return {...state};
};
vm.runInNewContext(fs.readFileSync(new URL('../static/assistant.js', import.meta.url), 'utf8'),
  {$, window, api, online:false, setInterval(){}, toast(){}, confirm(){return false;}});
const settle = () => new Promise(resolve => setImmediate(resolve));
const submit = async () => { $('#ai-vpn-form').onsubmit({preventDefault(){}, submitter:{}}); await settle(); };

await window.loadAssistant();
$('#ai-vpn-config').value = 'client\n<ca>\nfixture\n</ca>';
await submit();
assert.match($('#ai-vpn-result').textContent, /Не сохранено: Ошибка профиля/);
assert.equal($('#ai-vpn-result').hidden, false);
assert.match($('#ai-vpn-config').value, /fixture/, 'Failed save must preserve the pasted profile');
await window.loadAssistant();
assert.match($('#ai-vpn-result').textContent, /строка 47/, 'Polling must not erase the save error');

failSave = false;
await submit();
assert.equal($('#ai-vpn-config').value, '');
assert.match($('#ai-vpn-result').textContent, /Профиль сохранён на колонке/);
assert.match($('#ai-vpn-next').textContent, /логин и пароль/);
assert.equal($('#ai-vpn-connect').hidden, false);
assert.equal($('#ai-vpn-enabled').checked, false, 'Save must not implicitly enable VPN');

$('#ai-vpn-user').value = 'fixture-user';
$('#ai-vpn-password').value = 'fixture-password';
failSave = true;
await $('#ai-vpn-connect').onclick({currentTarget:{}});
assert.match($('#ai-vpn-result').textContent, /VPN не подключён:/);
assert.match($('#ai-vpn-next').textContent, /Профиль сохранён/);
assert.equal($('#ai-vpn-password').value, 'fixture-password');
failSave = false;
await $('#ai-vpn-connect').onclick({currentTarget:{}});
assert.equal(writes.at(-1).body.enabled, true);
assert.equal(writes.at(-1).body.password, 'fixture-password');
assert.equal($('#ai-vpn-password').value, '');
assert.equal($('#ai-vpn-connect').hidden, true);
assert.equal($('#ai-vpn-disconnect').hidden, false);
assert.match($('#ai-vpn-result').textContent, /Подключаем VPN/);
state = {...state, vpn_connected:true, vpn_state:'connected'};
await window.loadAssistant();
assert.equal($('#ai-vpn-result').textContent, 'Профиль сохранён. VPN подключён.');

console.log('VPN form: persistent errors, preserved input, saved state and explicit connection passed');
