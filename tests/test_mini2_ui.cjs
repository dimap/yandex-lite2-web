const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');

// Exercise the existing ring player: a firmware intro must not repeat with the loop.
{
  const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
  const begin=html.indexOf('function animateRing(');
  const end=html.indexOf('// ---------- заставка при загрузке ----------',begin);
  function play(animation,count){
    const shown=[],jobs=[];
    const sandbox={ringTimer:null,ringAnimUntil:0,Date:{now:()=>0},clearTimeout:()=>{},
      setTimeout:fn=>{jobs.push(fn);return jobs.length;},toRing:colors=>shown.push(colors[0][0])};
    vm.createContext(sandbox);vm.runInContext(html.slice(begin,end),sandbox);
    sandbox.animateRing(animation,10);
    while(jobs.length&&shown.length<count)jobs.shift()();
    return shown;
  }
  const frames=[0,1,2].map(n=>({c:[[n,0,0]],ms:20}));
  assert.deepEqual(play({frames,loop:true,loop_at:1},6),[0,1,2,1,2,1]);
  assert.deepEqual(play({frames,loop:true},6),[0,1,2,0,1,2]);
  assert.deepEqual(play({frames,loop:false},6),[0,1,2]);
  console.log('Ring intro plays once; default loops and finite animations unchanged');
}

// Render the actual shared SVG with and without the profile's existing segment list.
{
  const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
  const begin=html.indexOf("const SEGS = 'ABCDEFGH';");
  const end=html.indexOf('const toModel =',begin);
  const sandbox={window:{}};
  vm.createContext(sandbox);vm.runInContext(html.slice(begin,end),sandbox);
  const values=Array(36).fill(160),before=[...values];
  const original=sandbox.clockSVG(values);
  const dots=[7,15,23,31];
  for(const i of dots)assert(original.includes(`data-i="${i}"`));
  sandbox.window.clockGeom.layout={segments:Array.from({length:4},(_,p)=>
    [...'ABCDEFG'].map(s=>s+(p+1))).flat().concat(['PLUS1','PLUS2','MINUS','DEGREE','PLUS3','PLUS4'])};
  const mini2=sandbox.clockSVG(values);
  for(const i of dots)assert(!mini2.includes(`data-i="${i}"`));
  assert.equal((mini2.match(/<path /g)||[]).length,28);
  for(const i of [32,33,34,35])assert(mini2.includes(`data-i="${i}"`));
  sandbox.window.clockGeom.layout=undefined;
  assert.equal(sandbox.clockSVG(values),original);
  assert.deepEqual(values,before);
  console.log('Shared clock SVG uses profile segments; default dots and existing markers preserved');
}

// Both Overview actions send the existing frame contract without a browser-side converter.
(async()=>{
  const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
  const calls=[],nodes={'#disp-pin':{},'#clock-br':{value:'160'},'#clock-text':{value:'12:34'}};
  const values=Array(36).fill(0);values[0]=80;values[33]=80;
  const sandbox={window:{Mini2Clock:{payload(){throw new Error('Overview must use the server frame adapter');}}},
    $:selector=>nodes[selector],dispMode:'text',clockFrame:()=>values,clockPreview:()=>{},pcTime:()=>'',
    act:async(btn,fn)=>fn(),api:async(url,body)=>{calls.push({url,body:JSON.parse(JSON.stringify(body))});return {mode:'frame'};},
    renderDispState:()=>{},toModel:()=>{},Date};
  vm.createContext(sandbox);
  let begin=html.indexOf("$('#disp-pin').onclick =");
  let end=html.indexOf("$('#disp-stop').onclick =",begin);
  vm.runInContext(html.slice(begin,end),sandbox);
  begin=html.indexOf('function sendClock(');end=html.indexOf("$('#clock-set').onclick =",begin);
  vm.runInContext(html.slice(begin,end),sandbox);
  await nodes['#disp-pin'].onclick({currentTarget:{}});
  await sandbox.sendClock({});
  assert.deepEqual(calls,[{url:'/api/display',body:{mode:'frame',brightness:160,v:values}},
    {url:'/api/clock',body:{v:values}}]);
  console.log('Overview pin and one-shot use v[36] through the existing server adapter');
})().catch(error=>{console.error(error);process.exitCode=1;});

// The existing Launch editor clears and sends ordinary frames without model-specific hooks.
(async()=>{
  const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
  const nodes={'#boot-clear':{},'#boot-device':{}},calls=[];
  const frame={v:Array(36).fill(80),segments:{A1:233},ms:200,flag:1};
  const sandbox={$:selector=>nodes[selector],bootCur:()=>frame,bootFull:()=>[frame],bootDuration:()=>1,
    blankFrame:()=>Array(36).fill(0),renderBoot:()=>{},act:async(btn,fn)=>fn(),
    api:async(url,body)=>calls.push({url,body:JSON.parse(JSON.stringify(body))})};
  vm.createContext(sandbox);
  let begin=html.indexOf("$('#boot-clear').onclick ="),end=html.indexOf("$('#boot-del').onclick =",begin);
  vm.runInContext(html.slice(begin,end),sandbox);
  begin=html.indexOf("$('#boot-device').onclick =");end=html.indexOf("$('#boot-load-default').onclick =",begin);
  vm.runInContext(html.slice(begin,end),sandbox);
  nodes['#boot-clear'].onclick();assert(frame.v.every(v=>v===0));
  await nodes['#boot-device'].onclick({currentTarget:{}});
  assert.deepEqual(calls,[{url:'/api/clock/play',body:{frames:[frame],seconds:1}}]);
  console.log('Launch clear and playback retain the existing frame contract');
})().catch(error=>{console.error(error);process.exitCode=1;});

{
  const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
  const nodes={},sandbox={boot:{status:{supported:false,editor_only:true}},$:id=>nodes[id]||=( {})};
  vm.createContext(sandbox);
  const begin=html.indexOf('function renderBootStatus()'),end=html.indexOf('async function loadBoot()',begin);
  vm.runInContext(html.slice(begin,end),sandbox);sandbox.renderBootStatus();
  assert.equal(nodes['#boot-status'].innerHTML,'<span class="badge">только редактор и проигрывание</span>');
  delete sandbox.boot.status.editor_only;sandbox.renderBootStatus();
  assert.equal(nodes['#boot-status'].innerHTML,'<span class="badge bad">прошивка не поддерживается</span>');
  console.log('Launch editor-only status comes from the API; default unsupported status unchanged');
}

{
  const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
  const nodes={'#boot-pause':{value:'0'},'#boot-pause-hint':{style:{}}};
  const sandbox={boot:{status:{editor_only:true}},$:id=>nodes[id]};
  vm.createContext(sandbox);
  const begin=html.indexOf('function checkPause()'),end=html.indexOf("$('#boot-pause').oninput",begin);
  vm.runInContext(html.slice(begin,end),sandbox);sandbox.checkPause();
  assert.equal(nodes['#boot-pause-hint'].style.color,'');
  sandbox.boot.status={};sandbox.checkPause();
  assert.equal(nodes['#boot-pause-hint'].style.color,'var(--danger)');
  nodes['#boot-pause'].value='3300';sandbox.checkPause();
  assert.equal(nodes['#boot-pause-hint'].style.color,'');
  console.log('Boot delay warning applies to startup installation, not editor-only playback');
}

// Exercise the renderer's actual LED callback: Mini 2 mixes every zone, other models keep sampling.
{
  const source=fs.readFileSync(path.join(__dirname,'../static/model-studio.js'),'utf8');
  const begin=source.indexOf('window.modelRing='),end=source.indexOf('function paint(',begin);
  class Color{
    constructor(value){this.rgb=value==='#ffffff'?[1,1,1]:[0,0,0];}
    setRGB(r,g,b){this.rgb=[r,g,b];return this;}
    set(value){this.rgb=value==='#ffffff'?[1,1,1]:[0,0,0];return this;}
    copy(other){this.rgb=[...other.rgb];return this;}
    lerp(other,a){this.rgb=this.rgb.map((v,i)=>v*(1-a)+other.rgb[i]*a);return this;}
  }
  function show(mini2,leds,count=1,logo=true){
    const materials=Array.from({length:count},()=>({color:new Color(),emissive:new Color(),userData:{logo}}));
    const sandbox={window:{},mini2,ledMaterials:materials,T:{Color,SRGBColorSpace:'srgb'},dirty:false};
    vm.createContext(sandbox);vm.runInContext(source.slice(begin,end),sandbox);sandbox.window.modelRing(leds);
    return materials;
  }
  const onlyLast=Array.from({length:6},(_,i)=>i===5?[255,0,0]:[0,0,0]);
  const actual=show(true,onlyLast)[0].color.rgb,expected=[.15+.85/6,.15,.15];
  actual.forEach((v,i)=>assert(Math.abs(v-expected[i])<1e-12));
  assert.deepEqual(show(true,Array.from({length:6},()=>[0,0,0]))[0].color.rgb,[1,1,1]);
  const native=[[255,0,0],[0,255,0],[0,0,255],[120,60,30],[0,0,0],[255,255,255]];
  const original=show(false,native,2,false);
  assert.deepEqual(original[0].color.rgb,[1,0,0]);
  assert.deepEqual(original[1].color.rgb,[120/255,60/255,30/255]);
  assert.equal(original[1].emissiveIntensity,.55);
  console.log('Mini 2 central light includes zone 6; other models retain their LED mapping');
}

(async()=>{
  const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
  const begin=html.indexOf('async function loadYam()'),end=html.indexOf('const yamCall =',begin);
  for(const status of [{installed:true,running:false,autostart:true,autostart_external:true,read_only:true,read_only_reason:'Needs separate verification'},
                       {installed:false,read_only:true,read_only_reason:'Needs separate verification'},
                       {installed:true,running:false,autostart:false}]){
    const nodes={},sandbox={yam:{},$:id=>nodes[id]||=( {}),api:async()=>status,toast:()=>{},
      loadYamVersion:()=>{},renderYam:()=>{}};
    vm.createContext(sandbox);vm.runInContext(html.slice(begin,end),sandbox);await sandbox.loadYam();
    for(const id of ['#yam-install','#yam-start','#yam-autostart','#yam-update','#yam-update-confirm-btn','#yam-binaries']){
      assert.equal(nodes[id]?.disabled===true,!!status.read_only);
    }
    if(status.read_only)assert.equal(nodes['#yam-missing .hint'].textContent,status.read_only_reason);
    if(status.installed)assert.equal(nodes['#yam-autostart'].checked,status.autostart);
  }
  console.log('YAM status preserves other profiles and disables unverified Mini 2 mutations');
})().catch(error=>{console.error(error);process.exitCode=1;});

// Run the actual restore handler: UI-only copies keep the device-reset warning out of the flow.
(async()=>{
  const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
  const begin=html.indexOf("$('#bk-file').onchange ="),end=html.indexOf('// Установка: Chrome/Edge',begin);
  for(const uiOnly of [true,false]){
    const nodes={'#bk-file':{},'#bk-restore':{}},saved={},messages=[],calls=[];
    const sandbox={$:id=>nodes[id],footprint:uiOnly?{backup_ui_only:true}:{},confirm:text=>{messages.push(text);return true},
      toast:()=>{},act:async(_,fn)=>fn(),api:async(url,body,options)=>{calls.push({url,body,options});return {ui:{'ui.theme':'auto',token:'excluded'},restored:['UI']};},
      UI_KEYS:/^(ui|ov|ch|snd)\./,localStorage:{setItem:(key,value)=>saved[key]=value},
      PanelContext:{storageKey:key=>key},applyUI:()=>{},store:{get:()=>null},loadFootprint:async()=>{},
      bootLoaded:true,alarmsData:{},Object,JSON,Date};
    vm.createContext(sandbox);vm.runInContext(html.slice(begin,end),sandbox);
    const file={name:'preferences.tar.gz'};const input={files:[file],value:'selected'};
    await nodes['#bk-file'].onchange({target:input});
    assert.equal(input.value,'');assert.equal(calls[0].url,'/api/backup/restore');assert.equal(calls[0].body,file);
    assert.equal(calls[0].options.headers['Content-Type'],'application/octet-stream');
    assert.deepEqual(saved,{'ui.theme':'auto'});
    assert.equal(messages[0].includes('системными файлами панели не принимаются'),uiOnly);
    assert.equal(messages[0].includes('будет заменено содержимым копии'),!uiOnly);
  }
  console.log('Backup UI restore uses capability-specific confirmation and filters browser keys');
})().catch(error=>{console.error(error);process.exitCode=1;});
