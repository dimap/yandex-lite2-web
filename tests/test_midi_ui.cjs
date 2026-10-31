const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
const polled = [], clocks = [];
const c = {
  document: {hidden: false}, $: () => ({hidden: false, _until: 0}), online: true,
  api: async () => c.state, toModel: v => clocks.push(v), toRing: r => polled.push(r),
  window: {}, Date, performance: {now: () => 1000}, setTimeout() {}, muState: () => ({playing: true}),
  state: {v: null, ring: [[8, 0, 0], [8, 0, 0], [8, 0, 0]]},
  X_DEG: 32, X_DH: 33, X_DL: 34, X_DM: 35,
};
vm.createContext(c);
vm.runInContext(html.slice(html.indexOf('let ringAnimUntil ='), html.indexOf('\npollLeds();')), c);
vm.runInContext(html.slice(html.indexOf('const isSpectrum ='), html.indexOf('\nsetTimeout(liveLeds, 1000)')), c);
(async () => {
  await c.pollLeds();
  await c.liveLeds();
  assert.equal(clocks.length, 0, 'unmapped clock is never rendered as invented zeros');
  assert.equal(polled.length, 2, 'RGB state is independent of the clock mapping');
  c.state = {v: Array(36).fill(0), ring: [[0, 0, 0]]};
  await c.pollLeds();
  await c.liveLeds();
  assert.equal(clocks.length, 2, 'existing mapped clocks still update');
  assert.equal(polled.length, 4);

  let inserted;
  const elements = [];
  const context = {
    document: {
      createElement: tag => { const e = {tag, dataset: {}, children: [], setAttribute() {}, append(...items) {this.children.push(...items);}}; elements.push(e); return e; },
      querySelector: () => ({after: card => { inserted = card; }}),
    },
    PanelContext: {fetch: async (url, options) => { context.request = {url, options}; return {ok: true, json: async () => ({ok: true})}; }},
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/midi.js'), 'utf8').replace('export function', 'function'), context);
  context.mountMidiControls({logo: false});
  assert.equal(elements.length, 0, 'unverified logo controls stay hidden');
  context.mountMidiControls({logo: true});
  assert.equal(inserted.dataset.widget, 'midi-logo');
  const buttons = elements.filter(e => e.tag === 'button');
  await buttons[0].onclick();
  assert.equal(context.request.url, '/api/logo');
  assert.deepEqual(JSON.parse(context.request.options.body), {red: 16, green: 0, blue: 16});
  await buttons[2].onclick();
  assert.equal(context.request.url, '/api/logo/reset');
  context.PanelContext.fetch = async () => ({ok: false, json: async () => ({error: 'fixture failure'})});
  await buttons[0].onclick();
  assert(buttons.every(b => !b.disabled), 'failure leaves controls usable for reset');
  assert(elements.some(e => e.textContent === 'fixture failure'));
  console.log('Midi: unmapped clocks, independent RGB updates, logo flags, actions and error recovery passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
