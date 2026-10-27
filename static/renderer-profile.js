// Visual identity is independent of the explicitly selected hardware command profile.
export const MODEL_NAMES={lite:'Станция Лайт',lite2:'Станция Лайт 2',mini2:'Станция Мини 2',mini3:'Станция Мини 3',mini3pro:'Станция Мини 3 Про',station2:'Станция 2'};

export function rendererProfile(profile,status={}){
  const hardware=String(status.hardware_model||'').toLowerCase();
  const hostname=String(status.hostname||'').trim().toLowerCase();
  let id;
  if(/\bstation[ -]mini[ -]?2\b/.test(hardware))id='mini2';
  // Device-tree identity wins over a hostname that the owner may have renamed.
  for(const [platform,model]of Object.entries({bergamot:'mini3',pickle:'mini3pro',plum:'lite2',yandexmidi:'station2'})){
    if(new RegExp('(^|[^a-z0-9])'+platform+'([^a-z0-9]|$)').test(hardware)){id=model;break;}
  }
  if(!id&&/\bstation[ -]micro\b/.test(hardware))id='lite';
  // Test Pro first: it shares the Mini 3 prefix, but has a different body and ring.
  if(!id){
    const patterns=[
      ['mini3pro',/^yandex-mini(?:-gen|-)?3-?pro(?:-|$)/],
      ['mini3',/^yandex-mini(?:-gen|-)?3(?:-|$)/],
      ['mini2',/^yandex-?mini(?:-gen|-)?2(?:-|$)/],
      ['lite2',/^yandex-lite(?:-gen|-)?2(?:-|$)/],
      ['lite',/^yandex-station-lite$/],
      ['station2',/^yandex-station(?:-gen|-)?2(?:-|$)/],
    ];
    id=patterns.find(([,pattern])=>pattern.test(hostname))?.[0];
  }
  id=id||profile.id;
  return {...profile,id,name:MODEL_NAMES[id]||profile.name};
}
