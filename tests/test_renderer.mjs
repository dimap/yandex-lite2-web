import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../static/renderer-profile.js',import.meta.url),'utf8');
const {rendererProfile}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const original={id:'lite2',name:'Lite 2',clock:true,boot:true};
const cases=[
  [{hostname:'Yandex-Mini3-6CAK'},'mini3'],
  [{hostname:'Yandex-Mini-gen3-6CAK'},'mini3'],
  [{hostname:'Yandex-Mini3-Pro-H6QK'},'mini3pro'],
  [{hostname:'Yandex-Mini-gen3-Pro-H6QK'},'mini3pro'],
  [{hostname:'Yandex-Lite-gen2-NJ0C'},'lite2'],
  [{hostname:'Yandex-Station-gen2'},'station2'],
  [{hardware_model:'Yandex Bergamot EVT1',hostname:'renamed'},'mini3'],
  [{hardware_model:'Yandex Pickle EVT1',hostname:'Yandex-Mini3-6CAK'},'mini3pro'],
  [{hardware_model:'Yandex Station Micro',hostname:'yandex-station-lite'},'lite'],
  [{hardware_model:'Yandex Station Micro v1.2',hostname:'renamed'},'lite'],
  [{hardware_model:'yandex,station-micro',hostname:'renamed'},'lite'],
  [{hostname:'yandex-station-lite'},'lite'],
  [{hardware_model:'Yandex Plum EVT2',hostname:'yandex-station-lite'},'lite2'],
  [{hostname:'Yandex-Mini2-1234'},'mini2'],
  [{hostname:'Yandex-Mini-gen2-ABCD'},'mini2'],
  [{hostname:'Yandex-Mini-gen2'},'mini2'],
  [{hostname:'YandexMini2-ABCD'},'mini2'],
  [{hostname:'Yandex-Mini-gen25-ABCD'},'lite2'],
  [{hardware_model:'Yandex Station Mini 2',hostname:'renamed'},'mini2'],
  [{hardware_model:'yandex,station-mini2',hostname:'renamed'},'mini2'],
  [{hostname:'unrecognized-device'},'lite2'], // Unknown identities still keep the selected profile.
];
for(const [identity,expected]of cases){
  const result=rendererProfile(original,identity);
  assert.equal(result.id,expected,JSON.stringify(identity));
  assert.equal(result.clock,true);assert.equal(result.boot,true); // Detection never enables hardware writes.
}
assert.equal(rendererProfile({id:'station2',name:'Station 2'},{hostname:'renamed'}).id,'station2');
assert.deepEqual(original,{id:'lite2',name:'Lite 2',clock:true,boot:true});
console.log(`${cases.length+1} renderer identity cases passed; hardware profile unchanged`);
