// MIT: original visual reconstructions; see models/SOURCES.md.
import * as T from 'three';
import { OrbitControls } from './vendor/three/OrbitControls.js';
import {modelSettings} from './model-settings.js';
import {CONCEPTS} from './model-concepts.js';
export function createModel(profile){
const view=document.getElementById('model-view'),concept=CONCEPTS[profile.id],lite=profile.id==='lite',mini2=profile.id==='mini2',mini=lite||mini2||profile.id==='mini3pro'||profile.id==='mini3';
view.setAttribute('role','group');view.setAttribute('aria-label','3D-модель: вращайте мышью или пальцем');view.classList.add('studio-view');
// Preserve upstream visualizer canvas and the references held by its animation loop.
// Лайт первого поколения: цвета как в её ledconfigs (purple, yellow, green, pink, red, beige).
const palette=concept?concept.colors:lite?{purple:['Фиолетовый','#6a3fe6'],yellow:['Жёлтый','#f0c43a'],green:['Зелёный','#59b96d'],pink:['Розовый','#ec2c7c'],red:['Красный','#d9473f'],beige:['Бежевый','#d8c6ab']}:profile.id==='mini3'?{black:['Чёрный','#34343a'],grey:['Серый','#a0a2a8'],violet:['Лиловый','#9b8eaa'],teal:['Бирюзовый','#6aa6a7']}:profile.id==='mini2'?{black:['Чёрный','#34343a'],blue:['Синий','#355d82'],red:['Красный','#a8423f']}:mini?{black:['Графит','#34343a'],grey:['Серый','#888b91'],blue:['Синий','#355d82']}:{black:['Антрацит','#3b3c40'],blue:['Кобальт','#485c85'],copper:['Медный','#a66d5d'],sand:['Песочный','#c5baaa'],red:['Рубин','#8c3447']};
let renderer;
try{renderer=new T.WebGLRenderer({antialias:true,alpha:true,powerPreference:'low-power'});}catch(e){view.insertAdjacentHTML('beforeend','<p class="studio-error">Для объёмной модели включите WebGL в браузере.</p>');throw e;}
renderer.domElement.className='studio-canvas';renderer.domElement.setAttribute('aria-label',profile.name+' — вращаемая 3D-модель');
renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=1.15;
view.appendChild(renderer.domElement);
const scene=new T.Scene(),camera=new T.PerspectiveCamera(30,1,1,2500),controls=new OrbitControls(camera,renderer.domElement);
controls.enableDamping=true;controls.enablePan=false;controls.minDistance=mini?135:240;controls.maxDistance=mini?520:820;controls.maxPolarAngle=Math.PI*.91;controls.autoRotateSpeed=.7;
// На телефоне модель не крутится: палец по ней должен прокручивать страницу
if(matchMedia('(hover: none) and (pointer: coarse)').matches){controls.enabled=false;renderer.domElement.style.touchAction='pan-y';}
const group=new T.Group();scene.add(group);
scene.add(new T.HemisphereLight(0xf1f4ff,0x45404b,2));
const keyLight=new T.DirectionalLight(0xfff5e9,3.2);keyLight.position.set(-140,220,180);scene.add(keyLight);
const fill=new T.DirectionalLight(0xd7e4ff,1.5);fill.position.set(160,80,-100);scene.add(fill);
// прорези по краю крышки Лайт: 72 наклонных овала (R — радиус крышки в пикселях холста)
function liteSlots(c,R){const k=R/512;c.save();c.translate(R,R);for(let i=0;i<72;i++){c.save();c.rotate(i/72*Math.PI*2);c.translate(0,-R*.905);c.rotate(.62);
  c.beginPath();c.roundRect(-11*k,-28*k,22*k,56*k,11*k);c.fill();c.restore();}c.restore();}
function canvasTexture(size,draw){const canvas=document.createElement('canvas');canvas.width=canvas.height=size;draw(canvas.getContext('2d'),size);const texture=new T.CanvasTexture(canvas);texture.colorSpace=T.SRGBColorSpace;return texture;}
// Match the lightweight material budget of the original Lite 2 renderer.
const fallback=Object.keys(palette)[0];
const fabric=new T.MeshStandardMaterial({color:palette[fallback][1],roughness:.92,metalness:.02});
const plastic=new T.MeshStandardMaterial({color:'#27282d',roughness:.55,metalness:.04});
const rubber=new T.MeshStandardMaterial({color:'#101114',roughness:.85});
const glass=new T.MeshStandardMaterial({color:'#303139',roughness:.35,metalness:.1});
function mesh(geo,mat,x=0,y=0,z=0,parent=group){const m=new T.Mesh(geo,mat);m.position.set(x,y,z);parent.add(m);return m;}
function roundedShape(w,d,r){const s=new T.Shape(),x=-w/2,z=-d/2;s.moveTo(x+r,z);s.lineTo(x+w-r,z);s.quadraticCurveTo(x+w,z,x+w,z+r);s.lineTo(x+w,z+d-r);s.quadraticCurveTo(x+w,z+d,x+w-r,z+d);s.lineTo(x+r,z+d);s.quadraticCurveTo(x,z+d,x,z+d-r);s.lineTo(x,z+r);s.quadraticCurveTo(x,z,x+r,z);return s;}
function roundedBox(w,h,d,r,b=1){const geo=new T.ExtrudeGeometry(roundedShape(w-2*b,d-2*b,Math.max(1,r-b)),{depth:h-2*b,bevelEnabled:true,bevelThickness:b,bevelSize:b,bevelSegments:5,steps:1,curveSegments:16});geo.rotateX(-Math.PI/2);geo.translate(0,-h/2+b,0);geo.computeVertexNormals();return geo;}
const ledMaterials=[];
function ledMaterial(){const m=new T.MeshStandardMaterial({color:'#181820',emissive:'#000000',emissiveIntensity:0,roughness:.28,metalness:.05});ledMaterials.push(m);return m;}
function port(y,z){mesh(roundedBox(10,4,1.5,1.9,.3),rubber,0,y,z);mesh(roundedBox(6,1.5,1.8,.7,.2),new T.MeshStandardMaterial({color:'#727078',metalness:.7,roughness:.5}),0,y,z-.15);}
let topY,baseY,clockSegments=[];
if(concept)({topY,baseY}=concept.build({T,group,mesh,roundedBox,canvasTexture,fabric,plastic,rubber,glass,ledMaterial}));
else if(lite){
  // По фото: прямой цилиндр 88 мм с острым верхним краем (корпус ~44 мм) на чёрном основании ~12 мм,
  // которое сужается книзу и изрезано наклонными канавками.
  mesh(new T.LatheGeometry([[0,-16],[43.7,-16],[44,-15.7],[44,27.6],[43.6,28]].map(p=>new T.Vector2(...p)),160),fabric);
  const cap=mesh(new T.CircleGeometry(43.6,160),fabric,0,28);cap.rotation.x=-Math.PI/2;   // плоская крышка: без сглаженных нормалей края
  // шаг канавок — ровно 1/22 ширины текстуры, иначе на стыке по кругу виден шов
  const ribs=canvasTexture(512,(c,n)=>{c.fillStyle='#3b3c42';c.fillRect(0,0,n,n);c.strokeStyle='#060607';c.lineWidth=11;
    for(let i=-22;i<44;i++){const x=i*n/22;c.beginPath();c.moveTo(x,n*.06);c.lineTo(x+n*.2,n*.94);c.stroke();}});
  ribs.wrapS=T.RepeatWrapping;ribs.repeat.set(3,1);
  mesh(new T.LatheGeometry([[0,-28],[38.6,-28],[40.2,-27.3],[42,-25],[43.3,-20.5],[43.7,-16]].map(p=>new T.Vector2(...p)),160),
    new T.MeshStandardMaterial({color:'#ffffff',map:ribs,roughness:.8}));
  // Светится только логотип Алисы в центре крышки (прорези декоративные, часов нет): в покое белый,
  // при подсветке — её цвет.
  const logoTex=canvasTexture(256,(c,n)=>{c.translate(n/2,n/2+8);const t=104,pts=[[0,-t],[t*.9,t*.62],[-t*.9,t*.62]];
    c.beginPath();c.moveTo(t*.45,-t*.19);for(let i=0;i<3;i++){const p=pts[(i+1)%3],q=pts[(i+2)%3];c.arcTo(p[0],p[1],q[0],q[1],t*.42);}c.closePath();
    const g=c.createRadialGradient(0,10,6,0,10,t);g.addColorStop(0,'#ffffff');g.addColorStop(1,'#dcd8e6');c.fillStyle=g;c.fill();});
  const logo=new T.MeshBasicMaterial({map:logoTex,transparent:true,depthWrite:false,toneMapped:false,polygonOffset:true,polygonOffsetFactor:-2,polygonOffsetUnits:-2});
  logo.userData.logo=true;ledMaterials.push(logo);
  const logoMesh=mesh(new T.PlaneGeometry(15,15),logo,0,28);logoMesh.rotation.x=-Math.PI/2;logoMesh.renderOrder=2;
  // плоская чёрная пластина с вдавленной «Я» на стыке корпуса и основания
  const side=.62,tagGroup=new T.Group();tagGroup.position.set(44.45*Math.sin(side),-9.5,44.45*Math.cos(side));tagGroup.rotation.y=side;group.add(tagGroup);
  const tagTex=canvasTexture(256,(c,n)=>{c.fillStyle='#1d1e22';c.fillRect(0,0,n,n);c.font='300 120px "Helvetica Neue", Arial, sans-serif';c.textAlign='center';c.textBaseline='middle';
    c.translate(n/2,n/2+6);c.scale(1.05,1.75);c.lineJoin='round';c.strokeStyle='#5a5b62';c.lineWidth=1.6;c.strokeText('Я',1,.7);c.fillStyle='#08080a';c.fillText('Я',0,0);});
  mesh(new T.BoxGeometry(10.5,15,.9),[0,0,0,0,1,0].map(i=>new T.MeshStandardMaterial(i?{color:'#ffffff',map:tagTex,roughness:.75}:{color:'#1d1e22',roughness:.75})),0,0,0,tagGroup);
  // USB-C сзади — рисунком на корпусе: скруглённое окно, внутри тёмный проём и светлый язычок
  const usb=canvasTexture(256,(c,n)=>{c.translate(n/2,n/2);c.fillStyle='#9a9aa2';c.beginPath();c.roundRect(-118,-44,236,88,44);c.fill();
    c.fillStyle='#141418';c.beginPath();c.roundRect(-106,-33,212,66,33);c.fill();c.fillStyle='#5d5d66';c.beginPath();c.roundRect(-64,-9,128,18,5);c.fill();});
  const usbDecal=mesh(new T.PlaneGeometry(9.6,9.6),new T.MeshStandardMaterial({map:usb,transparent:true,roughness:.5,metalness:.3,polygonOffset:true,polygonOffsetFactor:-1}),0,-9,-44.03);
  usbDecal.rotation.y=Math.PI;
  topY=28;baseY=-28;
}else if(mini2){
  // Мини 2 по фото: прямой тканевый цилиндр 97 × 54 мм, плоская чёрная крышка с перфорацией по краю,
  // «−» и «+», четыре микрофона. Светится только кольцо логотипа Алисы в центре (6 RGB), сбоку — бирка «Я».
  mesh(new T.LatheGeometry([[0,-27],[46.6,-27],[48,-26.5],[48.5,-25.2],[48.5,24.8],[48.1,25.4],[0,25.4]].map(p=>new T.Vector2(...p)),192),fabric);
  const capTex=canvasTexture(1024,(c,n)=>{const R=n/2;c.fillStyle='#1d1e22';c.fillRect(0,0,n,n);c.translate(R,R);c.fillStyle='#050506';
    for(let ring=0;ring<4;ring++){const r=R*(.86+ring*.035),count=Math.round(150+ring*8);
      for(let i=0;i<count;i++){const a=(i+(ring%2)/2)/count*Math.PI*2;c.beginPath();c.arc(r*Math.cos(a),r*Math.sin(a),3.2,0,7);c.fill();}}
    c.strokeStyle='#b9bac0';c.lineWidth=6;c.lineCap='round';const k=R*.5;
    c.beginPath();c.moveTo(-k-22,0);c.lineTo(-k+22,0);c.moveTo(k-22,0);c.lineTo(k+22,0);c.moveTo(k,-22);c.lineTo(k,22);c.stroke();   // − и +
    c.fillStyle='#08080a';for(const[x,y]of[[-.27,-.3],[.27,-.3],[-.27,.3],[.27,.3]]){c.beginPath();c.arc(x*R,y*R,7,0,7);c.fill();}});
  capTex.center.set(.5,.5);capTex.rotation=Math.PI/2;   // UV крышки цилиндра повёрнуты: «−» слева, «+» справа
  mesh(new T.CylinderGeometry(48.1,48.1,1.2,192),[new T.MeshStandardMaterial({color:'#1d1e22',roughness:.6}),new T.MeshStandardMaterial({map:capTex,roughness:.6}),new T.MeshStandardMaterial({color:'#1d1e22'})],0,25.4);
  for(let i=0;i<6;i++){const arc=mesh(new T.RingGeometry(3.4,5.4,16,1,Math.PI/2-(i+1)*Math.PI/3,Math.PI/3+.01),ledMaterial(),0,26.05);arc.rotation.x=-Math.PI/2;}
  const tagTex=canvasTexture(128,(c,n)=>{c.fillStyle='#2a2b30';c.fillRect(0,0,n,n);c.font='300 64px "Helvetica Neue", Arial, sans-serif';c.textAlign='center';c.textBaseline='middle';c.fillStyle='#55565c';c.fillText('Я',n/2,n/2+4);});
  const side=.62,tag=new T.Group();tag.position.set(48.9*Math.sin(side),20.5,48.9*Math.cos(side));tag.rotation.y=side;group.add(tag);
  const tagMat=new T.MeshStandardMaterial({color:'#2a2b30',roughness:.7});
  mesh(new T.BoxGeometry(7.5,11,.9),[tagMat,tagMat,tagMat,tagMat,new T.MeshStandardMaterial({map:tagTex,roughness:.7}),tagMat],0,0,0,tag);
  mesh(new T.BoxGeometry(7.5,.9,3.2),tagMat,0,5.3,-1.6,tag);   // загнута на крышку
  topY=26;baseY=-27;port(-20,-48.6);
}else if(mini){
  const points=[[0,-35],[43,-35],[47,-34],[49.4,-31],[50.4,-26],[51,-18],[51,20],[50.8,28],[49.6,31],[0,31]].map(p=>new T.Vector2(...p));
  const shell=new T.LatheGeometry(points,192),shellUV=shell.attributes.uv,shellPosition=shell.attributes.position;
  for(let i=0;i<shellUV.count;i++)shellUV.setY(i,(shellPosition.getY(i)+35)/66);
  mesh(shell,fabric);
  mesh(new T.CylinderGeometry(49.5,47.6,3,160),plastic,0,-33.6);mesh(new T.CylinderGeometry(43,43,1.2,128),rubber,0,-35.6);
  if(profile.id!=='mini3')for(let i=0;i<16;i++)mesh(new T.CylinderGeometry(50.7,50.7,2.3,16,1,true,i*Math.PI/8,Math.PI/8+.005),ledMaterial(),0,30.1);
  mesh(new T.CylinderGeometry(50.6,50.8,2,192),plastic,0,32.1);mesh(new T.CylinderGeometry(50.2,50.2,.6,192),glass,0,33.4);topY=33.8;baseY=-36.2;port(-25,-50.1);
  if(profile.id==='mini3'){group.scale.y=60.5/71;const indicator=mesh(new T.RingGeometry(3.6,5.2,64),ledMaterial(),0,33.85);indicator.rotation.x=-Math.PI/2;}
}else{
  const body=roundedBox(123,190,99,21,3),positions=body.attributes.position,uv=body.attributes.uv;
  // ExtrudeGeometry uses millimetres as UVs; normalize to avoid subpixel aliasing.
  for(let i=0;i<uv.count;i++)uv.setXY(i,Math.atan2(positions.getX(i),positions.getZ(i))/(2*Math.PI)+.5,(positions.getY(i)+95)/190);
  mesh(body,fabric,0,-1.5);mesh(roundedBox(118,6,94,19,1),plastic,0,-96.5);mesh(roundedBox(107,1.4,84,16,.5),rubber,0,-99.3);
  mesh(roundedBox(122,2.6,98,21,.7),ledMaterial(),0,94.5);mesh(roundedBox(121,3,97,21,.9),glass,0,97.1);
  const lightMap=canvasTexture(256,(c,n)=>{const g=c.createLinearGradient(0,0,n,n);g.addColorStop(0,'#68d3ff');g.addColorStop(.45,'#9e7bff');g.addColorStop(1,'#e89ad7');c.fillStyle=g;c.fillRect(0,0,n,n);});
  const topMaterial=new T.MeshStandardMaterial({color:'#a5a2b7',map:lightMap,emissive:'#706187',emissiveMap:lightMap,emissiveIntensity:.18,roughness:.6,metalness:.02,transparent:true,opacity:.8});ledMaterials.push(topMaterial);
  const top=mesh(new T.ShapeGeometry(roundedShape(115,91,18),32),topMaterial,0,98.65);top.rotation.x=-Math.PI/2;topY=99;baseY=-100;port(-85,-49.6);
}
const controlsTex=canvasTexture(512,(c,n)=>{c.strokeStyle='#d6d6dc';c.fillStyle='#d6d6dc';c.lineWidth=2;c.lineCap='round';for(const x of[100,412]){c.beginPath();c.moveTo(x-10,256);c.lineTo(x+10,256);if(x>256){c.moveTo(x,246);c.lineTo(x,266);}c.stroke();}c.beginPath();c.ellipse(256,256,25,19,0,0,Math.PI*2);c.stroke();c.beginPath();c.moveTo(242,262);c.quadraticCurveTo(256,225,270,262);c.closePath();c.fill();c.fillStyle='#090a0c';for(const[x,y]of[[162,128],[350,128],[162,384],[350,384]]){c.beginPath();c.arc(x,y,2,0,Math.PI*2);c.fill();}});
// Крышка Лайт по фото сверху: по краю кольцо наклонных прорезей, по диагоналям микрофоны,
// слева «−», справа «+», сверху зачёркнутый микрофон, снизу пауза (логотип Алисы — отдельной деталью).
const liteTex=lite&&canvasTexture(1024,(c,n)=>{const R=n/2;c.translate(R,R);
  const ink='rgba(20,10,40,.34)';c.strokeStyle=ink;c.fillStyle=ink;c.lineCap='round';c.lineJoin='round';
  c.translate(-R,-R);liteSlots(c,R);c.translate(R,R);
  for(const[x,y]of[[-1,-1],[1,-1],[-1,1],[1,1]]){c.beginPath();c.arc(x*R*.49,y*R*.49,6,0,Math.PI*2);c.fillStyle='rgba(10,5,20,.8)';c.fill();}
  c.fillStyle=ink;c.lineWidth=7;const k=R*.48;
  c.beginPath();c.moveTo(-k-38,0);c.lineTo(-k+38,0);c.moveTo(k-38,0);c.lineTo(k+38,0);c.moveTo(k,-38);c.lineTo(k,38);c.stroke();   // − и +
  c.beginPath();c.moveTo(-14,k-34);c.lineTo(-14,k+34);c.moveTo(14,k-34);c.lineTo(14,k+34);c.stroke();                                // пауза
  c.save();c.translate(0,-k);c.scale(1.5,1.5);c.lineWidth=4.6;c.beginPath();c.roundRect(-11,-34,22,38,11);c.moveTo(-19,-8);c.quadraticCurveTo(-19,16,0,16);c.quadraticCurveTo(19,16,19,-8);
  c.moveTo(0,16);c.lineTo(0,28);c.moveTo(-13,28);c.lineTo(13,28);c.moveTo(-22,12);c.lineTo(22,-30);c.stroke();c.restore();            // микрофон выключен
});
if(!mini2&&!concept){
const topMarks=mesh(lite?new T.CircleGeometry(43.5,128):new T.PlaneGeometry(mini?98:117,mini?98:93),lite?new T.MeshBasicMaterial({map:liteTex,transparent:true,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1}):new T.MeshBasicMaterial({map:controlsTex,transparent:true,depthWrite:false,opacity:.72}),0,topY+.05);topMarks.rotation.x=-Math.PI/2;topMarks.renderOrder=1;   // прозрачные слои крышки — всегда в одном порядке, иначе мерцают при вращении
topMarks.castShadow=false;
}
if(mini&&!lite){
  const white=new T.MeshStandardMaterial({color:'#fff9e8',emissive:'#fff2d6',emissiveIntensity:2,roughness:.8,toneMapped:false});
  function horizontal(){const s=new T.Shape();s.moveTo(-3.5,0);s.lineTo(-2.5,1);s.lineTo(2.5,1);s.lineTo(3.5,0);s.lineTo(2.5,-1);s.lineTo(-2.5,-1);s.closePath();return new T.ShapeGeometry(s);}
  const positions=[[0,10,0],[4.2,5,1],[4.2,-5,1],[0,-10,0],[-4.2,-5,1],[-4.2,5,1],[0,0,0]];
  // у Мини 2 цифры крупнее и корпус уже: радиус, шаг и масштаб цифр свои
  const R=mini2?48.8:51.3,DX=mini2?[-21,-8,8,21]:[-19,-7,7,19],K=mini2?1.1:1,Y=mini2?-2:-3;
  for(let digit=0;digit<4;digit++)for(let j=0;j<8;j++){const[dx,dy,vertical]=positions[j]||[0,3,0],x=DX[digit]+dx*K,theta=x/R;const seg=mesh(j===7?new T.CircleGeometry(.7,12):horizontal(),white,(R+.3)*Math.sin(theta),dy*K+Y,(R+.3)*Math.cos(theta));seg.rotation.y=theta;seg.geometry.scale(K,K,1);if(vertical){seg.geometry.scale(1.25,.8,1);seg.rotateZ(Math.PI/2);}seg.castShadow=false;seg.visible=false;clockSegments.push(seg);}
  for(const[x,y]of[[mini2?32:29,10*K+Y+3],[0,1*K+Y+3],[0,-7*K+Y+3],[0,-3*K+Y+3]]){const theta=x/R,geometry=clockSegments.length===32?new T.RingGeometry(.9,1.4,20):clockSegments.length===35?new T.PlaneGeometry(3,1):new T.CircleGeometry(.9,16),s=mesh(geometry,white,(R+.3)*Math.sin(theta),y,(R+.3)*Math.cos(theta));s.rotation.y=theta;s.visible=false;s.castShadow=false;clockSegments.push(s);}
}
let dirty=true;
window.modelShow=values=>{clockSegments.forEach((s,i)=>{s.visible=Number(values?.[i])>0;});dirty=true;};
window.modelRing=leds=>{if(!leds?.length)return;ledMaterials.forEach((m,i)=>{const rgb=leds[Math.floor(i*leds.length/ledMaterials.length)]||[0,0,0],color=new T.Color().setRGB(...rgb.map(v=>Math.max(0,Math.min(255,v))/255),T.SRGBColorSpace);m.color.copy(color);
  if(m.userData.logo){if(Math.max(...rgb)<12)m.color.set('#ffffff');else m.color.copy(color).lerp(new T.Color('#ffffff'),.15);return;}   // логотип Лайт: без подсветки белый
  m.emissive.copy(color);m.emissiveIntensity=.55;});dirty=true;};
function paint(key){key=palette[key]?key:fallback;fabric.color.set(palette[key][1]);plastic.color.copy(fabric.color).multiplyScalar(.65);dirty=true;}
let preference=null;
window.modelColor=key=>{if(!preference)paint(key);};
paint(window.__deviceColor||fallback);
// Gallery-only bodies differ widely in size: frame each one by its bounding sphere.
if(concept?.lying)group.rotation.z=Math.PI/2;
const bounds=concept&&new T.Box3().setFromObject(group).getBoundingSphere(new T.Sphere());
if(bounds){controls.minDistance=bounds.radius*1.6;controls.maxDistance=bounds.radius*6;controls.maxPolarAngle=Math.PI;}
function home(){if(bounds){camera.position.copy(bounds.center).add(new T.Vector3(.38,.42,1).setLength(bounds.radius*4.3));controls.target.copy(bounds.center);}
  else{if(lite)camera.position.set(60,115,215);else camera.position.set(mini?55:240,mini?83:155,mini?258:420);controls.target.set(0,mini?-4:0,0);}controls.update();dirty=true;}home();
modelSettings(profile.id,palette,{color:key=>{preference=key;paint(key||window.__deviceColor||fallback);},home,spin:value=>{controls.autoRotate=value;dirty=true;}});
controls.addEventListener('change',()=>{dirty=true;});controls.addEventListener('start',()=>{view.classList.add('studio-used');});
let visible=true;new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;dirty=true;}).observe(view);
new ResizeObserver(()=>{const{width,height}=view.getBoundingClientRect();if(width&&height){renderer.setSize(width,height);camera.aspect=width/height;camera.updateProjectionMatrix();dirty=true;}}).observe(view);
// без ограничения частоты, как у Лайт 2: с ограничением в 30 к/с вращение дёргалось; в покое кадры не рисуются
function frame(){requestAnimationFrame(frame);if(document.hidden||!visible)return;controls.update();if(dirty||controls.autoRotate){renderer.render(scene,camera);dirty=false;view.dataset.renderer='webgl';view.dataset.model=profile.id;}}requestAnimationFrame(frame);

}
