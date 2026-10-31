// MIT. Original visual reconstruction from public Yandex photographs, not manufacturing CAD.
// Millimetres; the 36-value panel display API is a visual adapter, not Midi's native 45-segment layout.
import * as G from './clock-geometry.js';

export function buildMidi(kit) {
  const {T,mesh,roundedBox,canvasTexture,fabric,rubber,renderer}=kit;
  const samples=256,tile=18;
  function outline(w,r) {
    const a=w/2-r,s=new T.Shape();
    s.moveTo(-a,-w/2);s.lineTo(a,-w/2);s.absarc(a,-a,r,-Math.PI/2,0);
    s.lineTo(w/2,a);s.absarc(a,a,r,0,Math.PI/2);
    s.lineTo(-a,w/2);s.absarc(-a,a,r,Math.PI/2,Math.PI);
    s.lineTo(-w/2,-a);s.absarc(-a,-a,r,Math.PI,Math.PI*1.5);
    return s;
  }
  // One indexed surface with a continuous UV strip: rounded corners have smooth normals,
  // and the weave keeps its physical scale instead of stretching across each extruded face.
  function sleeve(rings) {
    const positions=[],uv=[],indices=[];
    for(const [y,w,r] of rings) {
      const path=outline(w,r),points=path.getSpacedPoints(samples),perimeter=path.getLength();
      for(let i=0;i<=samples;i++){positions.push(points[i].x,y,points[i].y);uv.push(i/samples*perimeter/tile,(y+55)/tile);}
    }
    const row=samples+1;
    for(let j=0;j<rings.length-1;j++)for(let i=0;i<samples;i++){
      const a=j*row+i,b=a+row;indices.push(a,b,a+1,a+1,b,b+1);
    }
    const geo=new T.BufferGeometry();geo.setAttribute('position',new T.Float32BufferAttribute(positions,3));
    geo.setAttribute('uv',new T.Float32BufferAttribute(uv,2));geo.setIndex(indices);geo.computeVertexNormals();
    const normals=geo.attributes.normal,v=new T.Vector3();
    for(let j=0;j<rings.length;j++){
      const a=j*row,b=a+samples;v.set(normals.getX(a)+normals.getX(b),normals.getY(a)+normals.getY(b),normals.getZ(a)+normals.getZ(b)).normalize();
      normals.setXYZ(a,v.x,v.y,v.z);normals.setXYZ(b,v.x,v.y,v.z);
    }
    return geo;
  }
  // The official macro photo shows open, raised knitted loops, not a fine diagonal print.
  // An 18 mm repeat contains 12 × 14 stitches. Albedo, height and transmission share the same loops.
  function knitTexture(height=false) {
    return canvasTexture(1024,(c,n)=>{
      c.fillStyle=height?'#101010':'#505050';c.fillRect(0,0,n,n);
      const dx=n/12,dy=n/14;
      function loop(x,y,shift=0) {
        c.beginPath();c.moveTo(x-dx*.17+shift,y-dy*.62);
        c.bezierCurveTo(x-dx*.5+shift,y-dy*.3,x-dx*.32+shift,y+dy*.12,x+shift,y+dy*.46);
        c.bezierCurveTo(x+dx*.32+shift,y+dy*.12,x+dx*.5+shift,y-dy*.3,x+dx*.17+shift,y-dy*.62);
      }
      // Draw across the tile boundaries too, so every map repeats without an edge seam.
      for(let row=-2;row<=16;row++)for(let col=-2;col<=14;col++){
        const rr=(row+28)%14,cc=(col+24)%12,jitter=Math.sin(rr*57+cc*113)*.025;
        const x=(col+(rr%2)*.065+jitter)*dx,y=(row+Math.cos(cc*61+rr*17)*.018)*dy;
        c.lineCap='round';c.lineJoin='round';
        // The recessed connection between adjacent loops.
        c.strokeStyle=height?'#484848':'#8a8a8a';c.lineWidth=dx*.15;
        c.beginPath();c.moveTo(x-dx*.3,y-dy*.45);c.bezierCurveTo(x-dx*.16,y-dy*.77,x+dx*.16,y-dy*.77,x+dx*.3,y-dy*.45);c.stroke();
        if(height){
          for(let layer=0;layer<10;layer++){
            const h=Math.round(48+164*Math.sin((layer+1)/11*Math.PI/2));
            c.strokeStyle=`rgb(${h},${h},${h})`;c.lineWidth=dx*(.43-layer*.036);loop(x,y);c.stroke();
          }
        }else{
          const tint=Math.round(Math.sin(rr*97+cc*31)*10);
          for(const [width,level,shift] of [[.47,62,1.8],[.38,174,0],[.25,214,-1.5],[.11,240,-3]]){
            const a=level+tint;c.strokeStyle=`rgb(${a},${a},${a})`;c.lineWidth=dx*width;loop(x,y,shift);c.stroke();
          }
          // Fine individual fibres break up perfectly smooth synthetic-looking yarn.
          c.strokeStyle='rgba(240,240,240,.35)';c.lineWidth=.85;
          for(const shift of [-dx*.072,-dx*.025,dx*.028,dx*.073]){loop(x,y,shift);c.stroke();}
        }
      }
    });
  }
  const weave=knitTexture(),relief=knitTexture(true),n=1024;
  const heights=relief.image.getContext('2d').getImageData(0,0,n,n).data;
  const transmission=canvasTexture(n,c=>{
    const pixels=c.createImageData(n,n);
    for(let i=0;i<heights.length;i+=4){const a=Math.round(255-heights[i]*.9);pixels.data[i]=pixels.data[i+1]=pixels.data[i+2]=a;pixels.data[i+3]=255;}
    c.putImageData(pixels,0,0);
  });
  const anisotropy=Math.min(8,renderer.capabilities.getMaxAnisotropy());
  for(const texture of [weave,transmission]){texture.wrapS=texture.wrapT=T.RepeatWrapping;texture.anisotropy=anisotropy;}
  transmission.colorSpace=T.NoColorSpace;
  // Keep the lightweight material budget required by CONTRIBUTION.md.
  fabric.map=weave;fabric.normalMap=null;fabric.bumpMap=null;
  fabric.roughness=.94;fabric.metalness=0;fabric.envMapIntensity=.15;fabric.needsUpdate=true;
  const body=mesh(sleeve([[-53.4,93.6,17.2],[-52.5,95.1,17.7],[-50.8,96,18],[47.7,96,18],[49.2,95.7,18],[50.1,94.8,17.8]]),fabric);
  body.name='midi-woven-shell';
  mesh(roundedBox(92.5,2.8,92.5,17,.6),rubber,0,-53.6).name='midi-base';
  mesh(roundedBox(85,1,85,15,.3),rubber,0,-54.5);

  // The top is moulded matte plastic. No clearcoat, metal or studio reflection map.
  const edge=new T.MeshStandardMaterial({color:'#20232b',roughness:.9,metalness:0});
  const panel=new T.MeshStandardMaterial({color:'#272b34',roughness:.88,metalness:0});
  mesh(roundedBox(96,2.7,96,18.5,.6),edge,0,53.25).name='midi-top-rim';
  const topGeometry=roundedBox(94.9,.8,94.9,18.1,.22),topUV=topGeometry.attributes.uv,topPositions=topGeometry.attributes.position;
  for(let i=0;i<topUV.count;i++)topUV.setXY(i,(topPositions.getX(i)+48)/96,(topPositions.getZ(i)+48)/96);
  mesh(topGeometry,panel,0,54.6).name='midi-matte-plastic-panel';

  // A single band with vertex colours gives a continuous gradient without dozens of draw calls.
  const band=sleeve([[50.05,95.1,18],[52.35,95.1,18]]);
  const bandColors=new Float32Array(band.attributes.position.count*3);band.setAttribute('color',new T.BufferAttribute(bandColors,3));
  mesh(band,new T.MeshBasicMaterial({vertexColors:true,toneMapped:false})).name='midi-contour-light';
  const halo=band.clone();halo.setAttribute('color',band.attributes.color);
  const haloMesh=mesh(halo,new T.MeshBasicMaterial({vertexColors:true,transparent:true,opacity:.13,blending:T.AdditiveBlending,depthWrite:false,toneMapped:false}));
  haloMesh.scale.set(1.009,1,1.009);haloMesh.name='midi-light-diffusion';
  const off=new T.Color('#20212a');let indicator=null;
  function ring(leds) {
    const colors=leds?.length?leds.map(rgb=>new T.Color().setRGB(...[0,1,2].map(i=>Math.max(0,Math.min(255,Number(rgb?.[i])||0))/255),T.SRGBColorSpace)):[];
    const color=new T.Color();
    for(let i=0;i<=samples;i++){
      if(colors.length){const u=i/samples*colors.length,index=Math.floor(u);color.copy(colors[index%colors.length]).lerp(colors[(index+1)%colors.length],u-index);color.lerp(off,.04);}
      else color.copy(off);
      for(let row=0;row<2;row++)band.attributes.color.setXYZ(row*(samples+1)+i,color.r,color.g,color.b);
    }
    band.attributes.color.needsUpdate=true;
    if(indicator){
      const hue=new T.Color(0,0,0);colors.forEach(c=>hue.add(c));if(colors.length)hue.multiplyScalar(1/colors.length);
      indicator.color.copy(hue.r+hue.g+hue.b>.001?hue: new T.Color('#787d8a'));
    }
  }
  ring([]);

  const markings=canvasTexture(1024,(c,n)=>{
    c.translate(n/2,n/2);c.lineCap='round';c.lineJoin='round';c.strokeStyle='#a3a5af';c.fillStyle='#a3a5af';c.lineWidth=5;
    for(const x of [-245,245]){c.beginPath();c.moveTo(x-24,0);c.lineTo(x+24,0);if(x>0){c.moveTo(x,-24);c.lineTo(x,24);}c.stroke();}
  });
  const marks=mesh(new T.PlaneGeometry(92,92),new T.MeshStandardMaterial({map:markings,transparent:true,opacity:.7,depthWrite:false,roughness:.42,polygonOffset:true,polygonOffsetFactor:-1}),0,55.015);
  marks.rotation.x=-Math.PI/2;marks.renderOrder=2;marks.name='midi-touch-controls';
  const logo=canvasTexture(256,(c,size)=>{
    c.translate(size/2,size/2);c.scale(2,2);c.fillStyle='#ffffff';c.beginPath();c.arc(0,0,58,0,Math.PI*2);c.fill();c.globalCompositeOperation='destination-out';
    c.beginPath();c.moveTo(0,-35);c.bezierCurveTo(10,-35,38,19,34,27);c.bezierCurveTo(30,35,-30,35,-34,27);c.bezierCurveTo(-38,19,-10,-35,0,-35);c.fill();
  });
  indicator=new T.MeshBasicMaterial({map:logo,color:'#787d8a',transparent:true,depthWrite:false,toneMapped:false});
  const logoMesh=mesh(new T.PlaneGeometry(11.5,11.5),indicator,0,55.025);logoMesh.rotation.x=-Math.PI/2;logoMesh.renderOrder=3;logoMesh.name='midi-alice-indicator';
  for(const [x,z] of [[-24,-24],[24,-24],[-24,24],[24,24]]){
    const mic=mesh(new T.CircleGeometry(.58,20),new T.MeshBasicMaterial({color:'#07080b'}),x,55.025,z);mic.rotation.x=-Math.PI/2;
  }
  // Rear microphone switch and USB-C recess; the control does not sit on the top panel.
  const switchMat=new T.MeshStandardMaterial({color:'#303139',roughness:.65});
  mesh(roundedBox(12.5,5,1.7,2,.35),rubber,0,37,-47.75).name='midi-microphone-switch';
  mesh(roundedBox(10.8,3.5,1.9,1.5,.25),switchMat,0,37,-47.85);
  const muteMap=canvasTexture(128,(c,n)=>{
    c.strokeStyle='#9c9da6';c.lineWidth=7;c.lineCap='round';c.translate(n/2,n/2);
    c.beginPath();c.roundRect(-12,-34,24,42,12);c.moveTo(-24,-6);c.bezierCurveTo(-24,29,24,29,24,-6);c.moveTo(0,20);c.lineTo(0,36);c.moveTo(-13,36);c.lineTo(13,36);c.moveTo(-31,27);c.lineTo(31,-33);c.stroke();
  });
  const mute=mesh(new T.PlaneGeometry(3.2,3.2),new T.MeshBasicMaterial({map:muteMap,transparent:true,depthWrite:false}),0,37,-48.82);mute.rotation.y=Math.PI;
  mesh(roundedBox(12,5.6,1.4,2.2,.3),rubber,0,-43,-47.9).name='midi-usb-c-recess';
  const metal=new T.MeshStandardMaterial({color:'#747780',metalness:.75,roughness:.35});
  mesh(roundedBox(8.8,3.2,1.5,1.4,.25),metal,0,-43,-48.05);
  mesh(roundedBox(7.8,2.4,1.6,1,.2),rubber,0,-43,-48.1);
  mesh(roundedBox(5,.55,1.7,.25,.1),switchMat,0,-43,-48.13);

  // Light visible through the fabric. Keep the panel's established frame API and honour brightness.
  const canvas=document.createElement('canvas');canvas.width=768;canvas.height=384;
  const ctx=canvas.getContext('2d'),clockMap=new T.CanvasTexture(canvas);clockMap.colorSpace=T.SRGBColorSpace;
  const mask=transmission.clone();mask.repeat.set(-60/tile,30/tile);
  mask.offset.set((outline(96,18).getLength()/2+60)/tile,(7-15+55)/tile);mask.needsUpdate=true;
  const screen=mesh(new T.PlaneGeometry(60,30),new T.MeshBasicMaterial({map:clockMap,alphaMap:mask,transparent:true,depthWrite:false,blending:T.AdditiveBlending,toneMapped:false}),0,7,48.035);
  screen.renderOrder=3;screen.visible=false;screen.name='midi-fabric-display';
  const paths=G.DIGIT_X.map(x=>G.segShapes(x).map(([,path])=>new Path2D(path)));
  function display(values) {
    ctx.clearRect(0,0,768,384);ctx.save();ctx.translate(16,62);ctx.scale(2.46,2.46);ctx.fillStyle='#fff4e3';ctx.shadowColor='#fff0db';
    const draw=(index,fn)=>{const a=Math.max(0,Math.min(255,Number(values?.[index])||0));if(!a)return;ctx.globalAlpha=Math.pow(a/255,.65);ctx.shadowBlur=4.5;fn();};
    paths.forEach((digit,i)=>{
      digit.forEach((path,j)=>draw(i*8+j,()=>ctx.fill(path)));
      draw(i*8+7,()=>{ctx.beginPath();ctx.arc(G.DIGIT_X[i]+61,91,2.5,0,Math.PI*2);ctx.fill();});
    });
    for(const [index,x,y,r] of [[G.X_DH,148,32,3.5],[G.X_DL,148,66,3.5],[G.X_DEG,306,-1,3]])draw(index,()=>{ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fill();});
    draw(G.X_DM,()=>ctx.fillRect(141,46,14,4));ctx.restore();clockMap.needsUpdate=true;screen.visible=Array.from(values||[]).some(v=>Number(v)>0);
  }
  return {topY:55,baseY:-55,ring,display};
}
