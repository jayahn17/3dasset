"""Self-contained WebGL viewer for a scanned point cloud.

Same philosophy as the control-center viewer: ONE html file, points
embedded (base64), no server / CDN / build step. Works from file:// and
from the capture worker's /blobs static mount — including in the Quest
browser (touch orbit + pinch zoom).

``RENDER_JS`` (decode + mat4 + ``makeCloudViewer``) is shared with the
capture worker's live page, whose cloud updates while you scan.
"""

from __future__ import annotations

import base64
import json
import os
import struct


def build_scan_viewer(
    out_path: str,
    xyz: list[tuple[float, float, float]],
    rgb: list[tuple[int, int, int]],
    title: str = "scan",
    stats: dict | None = None,
    links: dict[str, str] | None = None,
) -> str:
    """Write the viewer HTML. ``links`` (label -> href) become download
    buttons (e.g. {"scene.ply": "scene.ply", "scene.splat": "scene.splat"})."""
    n = len(xyz)
    pos = base64.b64encode(
        struct.pack(f"<{3 * n}f", *(c for p in xyz for c in p))
    ).decode("ascii")
    col = base64.b64encode(bytes(c for p in rgb for c in p)).decode("ascii")
    meta = {"n": n, "title": title, "stats": stats or {}, "links": links or {}}
    html = (
        _HTML_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__RENDER_JS__", RENDER_JS)
        .replace("__META__", json.dumps(meta))
        .replace("__POS__", pos)
        .replace("__COL__", col)
    )
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write(html)
    return out_path


#: Shared cloud renderer: `makeCloudViewer(canvas)` -> {setCloud(Float32Array,
#: Uint8Array), reset()}. Orbit on drag, wheel/pinch zoom, dblclick reset.
#: The camera auto-frames the first cloud, then stays put across setCloud
#: calls (so a live-updating scan doesn't fight the user's viewpoint).
RENDER_JS = r"""
"use strict";
function b64bytes(s){const b=atob(s),a=new Uint8Array(b.length);
  for(let i=0;i<b.length;i++)a[i]=b.charCodeAt(i);return a;}
function makeCloudViewer(canvas){
  const gl=canvas.getContext("webgl",{antialias:true});
  const vs=`attribute vec3 p;attribute vec3 c;uniform mat4 mvp;uniform float ps;
  varying vec3 vc;void main(){gl_Position=mvp*vec4(p,1.);
  gl_PointSize=clamp(ps/gl_Position.w,1.0,10.0);vc=c;}`;
  const fs=`precision mediump float;varying vec3 vc;
  void main(){gl_FragColor=vec4(vc,1.);}`;
  function sh(t,src){const s=gl.createShader(t);gl.shaderSource(s,src);
    gl.compileShader(s);
    if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw gl.getShaderInfoLog(s);
    return s;}
  const prog=gl.createProgram();
  gl.attachShader(prog,sh(gl.VERTEX_SHADER,vs));
  gl.attachShader(prog,sh(gl.FRAGMENT_SHADER,fs));
  gl.linkProgram(prog);gl.useProgram(prog);
  const bufP=gl.createBuffer(),bufC=gl.createBuffer();
  const aP=gl.getAttribLocation(prog,"p"),aC=gl.getAttribLocation(prog,"c");
  const uMVP=gl.getUniformLocation(prog,"mvp"),uPS=gl.getUniformLocation(prog,"ps");
  let N=0,ctr=[0,0,0],rad=1,framed=false;
  let yaw=0.6,pitch=0.35,dist=2.6;
  function setCloud(posF32,colU8){
    N=Math.floor(posF32.length/3);
    gl.bindBuffer(gl.ARRAY_BUFFER,bufP);
    gl.bufferData(gl.ARRAY_BUFFER,posF32,gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER,bufC);
    gl.bufferData(gl.ARRAY_BUFFER,colU8,gl.DYNAMIC_DRAW);
    if(!N)return;
    const lo=[1e30,1e30,1e30],hi=[-1e30,-1e30,-1e30];
    for(let i=0;i<N;i++)for(let k=0;k<3;k++){
      const v=posF32[3*i+k];if(v<lo[k])lo[k]=v;if(v>hi[k])hi[k]=v;}
    ctr=[(lo[0]+hi[0])/2,(lo[1]+hi[1])/2,(lo[2]+hi[2])/2];
    rad=Math.max(hi[0]-lo[0],hi[1]-lo[1],hi[2]-lo[2])/2||1;
    if(!framed){dist=rad*2.6;framed=true;}
  }
  function reset(){yaw=0.6;pitch=0.35;dist=rad*2.6;}
  function persp(fov,asp,n,f){const t=1/Math.tan(fov/2);
    return [t/asp,0,0,0, 0,t,0,0, 0,0,(f+n)/(n-f),-1, 0,0,2*f*n/(n-f),0];}
  function lookAt(e,c,up){
    let z=[e[0]-c[0],e[1]-c[1],e[2]-c[2]];const zl=Math.hypot(...z);
    z=z.map(v=>v/zl);
    let x=[up[1]*z[2]-up[2]*z[1],up[2]*z[0]-up[0]*z[2],up[0]*z[1]-up[1]*z[0]];
    const xl=Math.hypot(...x);x=x.map(v=>v/xl);
    const y=[z[1]*x[2]-z[2]*x[1],z[2]*x[0]-z[0]*x[2],z[0]*x[1]-z[1]*x[0]];
    return [x[0],y[0],z[0],0, x[1],y[1],z[1],0, x[2],y[2],z[2],0,
      -(x[0]*e[0]+x[1]*e[1]+x[2]*e[2]),
      -(y[0]*e[0]+y[1]*e[1]+y[2]*e[2]),
      -(z[0]*e[0]+z[1]*e[1]+z[2]*e[2]),1];}
  function mul(a,b){const o=new Array(16).fill(0);
    for(let i=0;i<4;i++)for(let j=0;j<4;j++)for(let k=0;k<4;k++)
      o[4*j+i]+=a[4*k+i]*b[4*j+k];return o;}
  function draw(){
    const w=canvas.clientWidth,h=canvas.clientHeight,
          dpr=window.devicePixelRatio||1;
    if(canvas.width!==w*dpr||canvas.height!==h*dpr){
      canvas.width=w*dpr;canvas.height=h*dpr;}
    gl.viewport(0,0,canvas.width,canvas.height);
    gl.clearColor(0.055,0.067,0.09,1);
    gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    if(N){
      gl.bindBuffer(gl.ARRAY_BUFFER,bufP);
      gl.enableVertexAttribArray(aP);
      gl.vertexAttribPointer(aP,3,gl.FLOAT,false,0,0);
      gl.bindBuffer(gl.ARRAY_BUFFER,bufC);
      gl.enableVertexAttribArray(aC);
      gl.vertexAttribPointer(aC,3,gl.UNSIGNED_BYTE,true,0,0);
      const cp=Math.cos(pitch),eye=[ctr[0]+dist*cp*Math.sin(yaw),
        ctr[1]+dist*Math.sin(pitch),ctr[2]+dist*cp*Math.cos(yaw)];
      const mvp=mul(persp(0.9,w/h,rad*0.01,rad*40),lookAt(eye,ctr,[0,1,0]));
      gl.uniformMatrix4fv(uMVP,false,new Float32Array(mvp));
      gl.uniform1f(uPS,(h*dpr)*rad*0.012);
      gl.drawArrays(gl.POINTS,0,N);
    }
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
  let dragXY=null,pinch=null;
  const clampP=v=>Math.max(-1.45,Math.min(1.45,v));
  canvas.addEventListener("mousedown",e=>dragXY=[e.clientX,e.clientY]);
  window.addEventListener("mousemove",e=>{if(!dragXY)return;
    yaw-=(e.clientX-dragXY[0])*0.006;
    pitch=clampP(pitch+(e.clientY-dragXY[1])*0.006);
    dragXY=[e.clientX,e.clientY];});
  window.addEventListener("mouseup",()=>dragXY=null);
  canvas.addEventListener("wheel",e=>{e.preventDefault();
    dist=Math.max(rad*0.1,dist*Math.exp(e.deltaY*0.0012));},{passive:false});
  canvas.addEventListener("dblclick",reset);
  canvas.addEventListener("touchstart",e=>{e.preventDefault();
    if(e.touches.length===1)
      dragXY=[e.touches[0].clientX,e.touches[0].clientY];
    else if(e.touches.length===2){dragXY=null;
      pinch=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,
                       e.touches[0].clientY-e.touches[1].clientY);}},
    {passive:false});
  canvas.addEventListener("touchmove",e=>{e.preventDefault();
    if(e.touches.length===1&&dragXY){
      yaw-=(e.touches[0].clientX-dragXY[0])*0.008;
      pitch=clampP(pitch+(e.touches[0].clientY-dragXY[1])*0.008);
      dragXY=[e.touches[0].clientX,e.touches[0].clientY];
    }else if(e.touches.length===2&&pinch){
      const d=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,
                         e.touches[0].clientY-e.touches[1].clientY);
      dist=Math.max(rad*0.1,dist*pinch/d);pinch=d;}},{passive:false});
  canvas.addEventListener("touchend",()=>{dragXY=null;pinch=null;});
  return {setCloud,reset};
}
"""

_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ - assetpipe scan</title>
<style>
  :root{--bg:#0e1117;--panel:#161b22;--line:#30363d;--txt:#e6edf3;--dim:#8b949e;--accent:#58a6ff}
  *{box-sizing:border-box;margin:0}
  html,body{height:100%;background:var(--bg);color:var(--txt);
    font:14px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;overflow:hidden}
  canvas{display:block;width:100vw;height:100vh;touch-action:none}
  #hud{position:fixed;top:12px;left:12px;background:rgba(22,27,34,.88);
    border:1px solid var(--line);border-radius:8px;padding:10px 14px;max-width:78vw}
  #hud h1{font-size:15px;margin-bottom:2px}
  #hud .dim{color:var(--dim);font-size:12px}
  #hud a{display:inline-block;margin:6px 8px 0 0;padding:3px 10px;border-radius:6px;
    border:1px solid var(--line);color:var(--accent);text-decoration:none;font-size:12px}
  #hud a:hover{border-color:var(--accent)}
  #hint{position:fixed;bottom:10px;left:12px;color:var(--dim);font-size:12px}
</style></head><body>
<canvas id="c"></canvas>
<div id="hud"><h1>__TITLE__</h1><div class="dim" id="sub"></div><div id="links"></div></div>
<div id="hint">drag = orbit &nbsp;·&nbsp; wheel / pinch = zoom &nbsp;·&nbsp; double-click = reset</div>
<script>__RENDER_JS__</script>
<script>
"use strict";
const META = __META__;
const sub = document.getElementById("sub");
const st = Object.entries(META.stats).map(([k,v])=>k+": "+v).join(" · ");
sub.textContent = META.n.toLocaleString()+" points"+(st?" · "+st:"");
const linksEl = document.getElementById("links");
for(const [label,href] of Object.entries(META.links)){
  const a=document.createElement("a");a.textContent="⬇ "+label;a.href=href;
  a.setAttribute("download","");linksEl.appendChild(a);
}
const viewer = makeCloudViewer(document.getElementById("c"));
viewer.setCloud(new Float32Array(b64bytes("__POS__").buffer), b64bytes("__COL__"));
</script></body></html>
"""
