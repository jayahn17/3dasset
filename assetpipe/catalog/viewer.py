"""Generate the 'control center' — a self-contained HTML view of the twin.

Produces one HTML file with every asset's metadata AND its mesh embedded
inline, plus a tiny dependency-free canvas 3D renderer (orbit on drag). No
server, no CDN, no build step: open it in any browser (even file://).

For production you would replace this with a real web app backed by
``AssetCatalog`` and a three.js/GLB viewer, but this proves the loop
end-to-end and is genuinely useful for eyeballing captures.
"""

from __future__ import annotations

import json
import os

from .store import AssetCatalog


def _read_obj(path: str) -> dict | None:
    if not path or not os.path.exists(path) or not path.lower().endswith(".obj"):
        return None
    verts: list[list[float]] = []
    faces: list[list[int]] = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("v "):
                _, x, y, z = line.split()[:4]
                verts.append([float(x), float(y), float(z)])
            elif line.startswith("f "):
                idx = [int(tok.split("/")[0]) - 1 for tok in line.split()[1:]]
                faces.append(idx)
    return {"v": verts, "f": faces}


def build_viewer(catalog: AssetCatalog, out_path: str) -> str:
    assets = catalog.all()
    payload = []
    for a in assets:
        payload.append(
            {
                "id": a["asset_id"],
                "label": a["label"],
                "category": a["category"],
                "dims": [a["dim_w"], a["dim_h"], a["dim_d"]],
                "location": a["location"] or "unknown",
                "source": a["source"],
                "tags": json.loads(a["tags"] or "[]"),
                "urdf": a["urdf_path"],
                "mesh": _read_obj(a["mesh_path"]),
            }
        )
    html = _HTML_TEMPLATE.replace("__DATA__", json.dumps(payload))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write(html)
    return out_path


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Life Twin - Asset Control Center</title>
<style>
  :root{--bg:#0e1117;--panel:#161b22;--line:#30363d;--txt:#e6edf3;--dim:#8b949e;--accent:#58a6ff}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--txt);display:flex;height:100vh}
  #side{width:300px;border-right:1px solid var(--line);display:flex;flex-direction:column;background:var(--panel)}
  #side h1{font-size:15px;margin:0;padding:16px;border-bottom:1px solid var(--line)}
  #side h1 span{color:var(--accent)}
  #search{margin:12px;padding:8px 10px;background:var(--bg);border:1px solid var(--line);border-radius:6px;color:var(--txt)}
  #list{overflow:auto;flex:1;padding:0 8px 12px}
  .cat{color:var(--dim);text-transform:uppercase;font-size:11px;letter-spacing:.05em;margin:14px 8px 4px}
  .item{padding:8px 10px;border-radius:6px;cursor:pointer;display:flex;justify-content:space-between;gap:8px}
  .item:hover{background:var(--bg)}
  .item.active{background:#1f6feb33;outline:1px solid var(--accent)}
  .item small{color:var(--dim)}
  #main{flex:1;display:flex;flex-direction:column}
  #cv{flex:1;width:100%;display:block;cursor:grab;background:radial-gradient(circle at 50% 40%,#1b2230,#0e1117)}
  #meta{border-top:1px solid var(--line);padding:14px 18px;background:var(--panel);display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
  .kv b{color:var(--dim);font-weight:500;display:block;font-size:11px;text-transform:uppercase}
  .tag{display:inline-block;background:#1f6feb22;color:var(--accent);border-radius:4px;padding:1px 7px;margin:2px 4px 0 0;font-size:12px}
  #empty{margin:auto;color:var(--dim)}
</style></head>
<body>
<div id="side">
  <h1>Life<span>Twin</span> · Asset Control Center</h1>
  <input id="search" placeholder="search label / location…">
  <div id="list"></div>
</div>
<div id="main">
  <canvas id="cv"></canvas>
  <div id="meta"><div id="empty">select an asset →</div></div>
</div>
<script>
const DATA = __DATA__;
const listEl=document.getElementById('list'), searchEl=document.getElementById('search'),
      metaEl=document.getElementById('meta'), cv=document.getElementById('cv'), ctx=cv.getContext('2d');
let current=null, rot={x:-0.5,y:0.7}, drag=null;

function groups(items){const g={};items.forEach(a=>{(g[a.category]=g[a.category]||[]).push(a)});return g;}
function renderList(){
  const q=searchEl.value.toLowerCase();
  const items=DATA.filter(a=>!q||a.label.toLowerCase().includes(q)||(a.location||'').toLowerCase().includes(q));
  listEl.innerHTML='';
  const g=groups(items);
  Object.keys(g).sort().forEach(cat=>{
    const h=document.createElement('div');h.className='cat';h.textContent=cat+' · '+g[cat].length;listEl.appendChild(h);
    g[cat].forEach(a=>{
      const d=document.createElement('div');d.className='item'+(current&&current.id===a.id?' active':'');
      d.innerHTML=`<span>${a.label}</span><small>${a.location}</small>`;
      d.onclick=()=>select(a);listEl.appendChild(d);
    });
  });
  if(!items.length) listEl.innerHTML='<div class="cat">no matches</div>';
}
function select(a){current=a;rot={x:-0.5,y:0.7};renderList();renderMeta(a);draw();}
function renderMeta(a){
  const dim=a.dims.map(x=>(x*100).toFixed(0)).join(' × ')+' cm';
  metaEl.innerHTML=`
   <div class="kv"><b>label</b>${a.label}</div>
   <div class="kv"><b>category</b>${a.category}</div>
   <div class="kv"><b>dimensions</b>${dim}</div>
   <div class="kv"><b>location</b>${a.location}</div>
   <div class="kv"><b>source</b>${a.source}</div>
   <div class="kv"><b>urdf</b>${a.urdf?'✔ exported':'—'}</div>
   <div class="kv" style="grid-column:1/-1"><b>tags</b>${a.tags.map(t=>`<span class=tag>${t}</span>`).join('')}</div>`;
}
// --- tiny painter's-algorithm mesh renderer (no deps) ---
function draw(){
  const dpr=window.devicePixelRatio||1, W=cv.clientWidth, H=cv.clientHeight;
  cv.width=W*dpr;cv.height=H*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,W,H);
  if(!current||!current.mesh){return;}
  const m=current.mesh, cx=W/2, cy=H/2;
  // center + scale mesh to view
  let mn=[1e9,1e9,1e9],mx=[-1e9,-1e9,-1e9];
  m.v.forEach(v=>v.forEach((c,i)=>{mn[i]=Math.min(mn[i],c);mx[i]=Math.max(mx[i],c);}));
  const ctr=mn.map((c,i)=>(c+mx[i])/2), span=Math.max(mx[0]-mn[0],mx[1]-mn[1],mx[2]-mn[2])||1;
  const s=Math.min(W,H)*0.55/span;
  const cxr=Math.cos(rot.x),sxr=Math.sin(rot.x),cyr=Math.cos(rot.y),syr=Math.sin(rot.y);
  const P=m.v.map(v=>{
    let x=v[0]-ctr[0],y=v[1]-ctr[1],z=v[2]-ctr[2];
    let x1=x*cyr+z*syr, z1=-x*syr+z*cyr;      // yaw
    let y1=y*cxr-z1*sxr, z2=y*sxr+z1*cxr;     // pitch
    return {x:cx+x1*s, y:cy-y1*s, z:z2};
  });
  const faces=m.f.map(f=>({f,z:f.reduce((a,i)=>a+P[i].z,0)/f.length})).sort((a,b)=>a.z-b.z);
  faces.forEach(({f})=>{
    // flat shade by face depth
    const t=Math.max(0,Math.min(1,(f.reduce((a,i)=>a+P[i].z,0)/f.length)/span+0.5));
    const c=Math.round(70+t*150);
    ctx.beginPath();f.forEach((i,k)=>{const p=P[i];k?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y);});ctx.closePath();
    ctx.fillStyle=`rgb(${c*0.5|0},${c*0.7|0},${c})`;ctx.fill();
    ctx.strokeStyle='rgba(200,220,255,.35)';ctx.stroke();
  });
}
cv.onmousedown=e=>{drag={x:e.clientX,y:e.clientY};cv.style.cursor='grabbing';};
window.onmouseup=()=>{drag=null;cv.style.cursor='grab';};
window.onmousemove=e=>{if(!drag)return;rot.y+=(e.clientX-drag.x)*0.01;rot.x+=(e.clientY-drag.y)*0.01;drag={x:e.clientX,y:e.clientY};draw();};
window.onresize=draw;searchEl.oninput=renderList;
renderList();if(DATA.length)select(DATA[0]);
</script></body></html>"""
