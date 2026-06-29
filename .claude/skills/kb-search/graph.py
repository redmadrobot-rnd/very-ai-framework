#!/usr/bin/env python3
"""GitMark graph — построить граф онтологии БЗ как self-contained HTML.

Отдельный скрипт (не часть основного CLI gitmark.py): переиспользует пакет gm/
для обхода/резолва, строит узлы-документы и типизированные рёбра-связи, рендерит
один HTML без внешних зависимостей (canvas + vanilla JS, оффлайн, CSP-safe).

    python3 .claude/skills/kb-search/graph.py [--root .] [-o docs/kb-graph.html]

Рёбра: типизированные связи из frontmatter `links:` (documents/depends_on/supersedes/
relates_to/implemented_by/part_of) + инлайновые md-ссылки в тексте (как relates_to).
Узлы раскрашены по node_type. Источник правды — markdown; HTML регенерируется.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gm import force_utf8_io
from gm.core import KB_DIR, LINK_RE, area_of, iter_md, nfc, repo_root, title_of
from gm.lint import parse_frontmatter, strip_code

# Windows console (cp1251) crashes on ✓/Cyrillic output — force UTF-8.
force_utf8_io()

# Типы связей онтологии (ключи под `links:`); inline-ссылки → relates_to.
LINK_TYPES = ("documents", "depends_on", "supersedes", "relates_to",
              "implemented_by", "part_of")

# Цвета узлов по node_type (совпадают с палитрой отчётов).
NODE_COLORS = {
    "service": "#7c9cff", "reference": "#00d3a7", "runbook": "#f59e0b",
    "gotcha": "#ef4444", "decision": "#a78bfa", "plan": "#38bdf8",
    "guide": "#34d399", "report": "#fbbf24", "index": "#94a3b8",
    "memory": "#f472b6", "untyped": "#525866",
}
EDGE_COLORS = {
    "documents": "#34d399", "implemented_by": "#34d399",
    "depends_on": "#7c9cff", "supersedes": "#ef4444",
    "part_of": "#94a3b8", "relates_to": "#3a4252",
}


def _resolve_doc(src_rel: str, href: str, known: set) -> str | None:
    """Резолвим ссылку в существующий md-документ из known (иначе None)."""
    h = nfc(href.split("#")[0].strip())
    if not h or h.startswith(("http", "mailto:")) or not h.endswith(".md"):
        return None
    import posixpath
    cands = [h.lstrip("./"), posixpath.normpath((Path(nfc(src_rel)).parent / h).as_posix())]
    for c in cands:
        if c in known:
            return c
    base = Path(h).name
    hits = [r for r in known if r == base or r.endswith("/" + base)]
    return hits[0] if len(hits) == 1 else None


def build_graph(root: Path) -> dict:
    """Граф онтологии: документы + хабы-папки.

    Скелет — рёбра-containment (папка→док, `own`); поверх — типизированные связи
    между документами (`ref`). Всё выводится из чтения md и пути (area_of) —
    никакого индекса/БД и никакой доп. меты в документах.
    """
    docs = list(iter_md(root))
    known = {nfc(p.relative_to(root).as_posix()) for p in docs}
    nodes, node_ids = [], {}      # node_ids: id → индекс в nodes (для doc и area)
    edges, seen_edges = [], set()
    areas = {}                    # area → число документов

    def ensure_area(area):
        aid = "area::" + area
        if aid not in node_ids:
            node_ids[aid] = len(nodes)
            nodes.append({"id": aid, "title": area, "kind": "area",
                          "type": "_area", "area": area, "deg": 0})
        return aid

    def add_edge(s, t, kind, ltype=None):
        if not t or s == t:
            return
        key = (s, t, ltype or kind)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({"s": s, "t": t, "kind": kind, "type": ltype or kind})

    for p in docs:
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text("utf-8", errors="replace")
        except Exception:
            continue
        area = area_of(rel)
        node_ids[rel] = len(nodes)
        nodes.append({"id": rel, "title": title_of(text, rel), "kind": "doc",
                      "type": (parse_frontmatter(text) or {}).get("node_type") or "untyped",
                      "area": area, "deg": 0})
        areas[area] = areas.get(area, 0) + 1
        # containment: папка → документ (скелет графа)
        add_edge(ensure_area(area), rel, "own")
        # типизированные связи + инлайновые ссылки → ref
        fm = parse_frontmatter(text) or {}
        links_fm = fm.get("links") if isinstance(fm.get("links"), dict) else {}
        for ltype in LINK_TYPES:
            for tgt in (links_fm.get(ltype) or []):
                d = _resolve_doc(rel, tgt, known)
                if d:
                    add_edge(rel, d, "ref", ltype)
        for mt in LINK_RE.finditer(strip_code(text)):
            d = _resolve_doc(rel, mt.group(1), known)
            if d:
                add_edge(rel, d, "ref", "relates_to")

    # степень (для размеров): для area — число доков, для doc — все инцидентные рёбра
    for e in edges:
        for nid in (e["s"], e["t"]):
            if nid in node_ids:
                nodes[node_ids[nid]]["deg"] += 1

    # BFS от точки входа (CLAUDE.md|AGENTS.md|README.md|первый) по own∪ref
    from collections import deque
    adj = {}
    for e in edges:
        adj.setdefault(e["s"], []).append(e["t"])
        adj.setdefault(e["t"], []).append(e["s"])
    root_id = next((c for c in (KB_DIR + "/README.md", KB_DIR + "/index.md") if c in node_ids),
                   nodes[0]["id"] if nodes else None)
    level, parent = ({root_id: 0}, {root_id: None}) if root_id else ({}, {})
    dq = deque([root_id] if root_id else [])
    while dq:
        u = dq.popleft()
        for v in adj.get(u, []):
            if v not in level:
                level[v] = level[u] + 1
                parent[v] = u
                dq.append(v)
    tree = set()
    for v, par in parent.items():
        if par is not None:
            tree.add((par, v)); tree.add((v, par))
    for n in nodes:
        n["lvl"] = level.get(n["id"], -1)
        n["root"] = (n["id"] == root_id)
    for e in edges:
        e["tree"] = (e["s"], e["t"]) in tree

    types = sorted({n["type"] for n in nodes if n["kind"] == "doc"})
    n_ref = sum(1 for e in edges if e["kind"] == "ref")
    return {"nodes": nodes, "edges": edges, "root": root_id,
            "stats": {"docs": len(docs), "areas": len(areas), "links": n_ref,
                      "types": types}}


_HTML = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KB graph · __TITLE__</title>
<style>
*{margin:0;box-sizing:border-box}
html,body{height:100%;background:#0b0e14;color:#e6e9ef;font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;overflow:hidden}
#hud{position:fixed;top:12px;left:14px;z-index:9;pointer-events:none}
#hud h1{font-size:15px;font-weight:600;letter-spacing:-.3px}
#hud .s{color:#8b93a7;font-size:12px;margin-top:2px}
#legend{position:fixed;top:12px;right:14px;z-index:9;background:#141925cc;border:1px solid #262e3f;border-radius:10px;padding:10px 12px;backdrop-filter:blur(6px)}
#legend div{display:flex;align-items:center;gap:7px;font-size:12px;padding:2px 0;color:#c7cdda}
#legend i{width:10px;height:10px;border-radius:50%;display:inline-block}
#tip{position:fixed;z-index:10;pointer-events:none;background:#1b2230;border:1px solid #2e3850;border-radius:7px;padding:6px 9px;font-size:12.5px;display:none;max-width:340px}
#tip b{color:#a5b4fc} #tip .t{color:#8b93a7;font-size:11px}
#hint{position:fixed;bottom:12px;left:14px;z-index:9;color:#5b6478;font-size:11.5px}
canvas{display:block}
</style></head><body>
<div id="hud"><h1>KB graph · __TITLE__</h1><div class="s" id="stat"></div></div>
<div id="legend"></div>
<div id="tip"></div>
<div id="hint">тащи узел · колесо — зум · фон — пан · подписи доков — при наведении/зуме · двойной клик — путь</div>
<canvas id="c"></canvas>
<script>
const DATA=__DATA__;
const NC=__NODECOLORS__, EC=__EDGECOLORS__;
const cv=document.getElementById('c'),ctx=cv.getContext('2d');
let W,H,DPR=Math.max(1,window.devicePixelRatio||1);
function resize(){W=innerWidth;H=innerHeight;cv.width=W*DPR;cv.height=H*DPR;cv.style.width=W+'px';cv.style.height=H+'px';ctx.setTransform(DPR,0,0,DPR,0,0);}
resize();addEventListener('resize',resize);

const N=DATA.nodes, E=DATA.edges, id2i={};
N.forEach((n,i)=>{id2i[n.id]=i;
  // радиальная заготовка по уровню BFS — помогает форс-симу не схлопнуться
  const lv=n.lvl<0?6:n.lvl, ang=i*2.399;
  n.x=W/2+Math.cos(ang)*(60+lv*90); n.y=H/2+Math.sin(ang)*(60+lv*90);
  n.vx=0;n.vy=0;
  n.r=n.root?13:(n.kind==='area'?7+Math.min(11,n.deg*0.45):3+Math.min(7,Math.sqrt(n.deg)*1.6));});
const ED=E.map(e=>({s:id2i[e.s],t:id2i[e.t],kind:e.kind,type:e.type,tree:e.tree}))
          .filter(e=>e.s!=null&&e.t!=null);

let view={k:.9,x:0,y:0};
const sx=n=>n.x*view.k+view.x, sy=n=>n.y*view.k+view.y;
const wx=mx=>(mx-view.x)/view.k, wy=my=>(my-view.y)/view.k;

let alpha=1, drag=null, pinned=null, hover=null;
function step(){
  if(alpha<0.02 && !drag){requestAnimationFrame(draw);return;}
  const k=alpha;
  // отталкивание (O(n^2) — ок для сотен узлов)
  for(let i=0;i<N.length;i++){const a=N[i];
    for(let j=i+1;j<N.length;j++){const b=N[j];
      let dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy+0.01;
      if(d2<160000){const d=Math.sqrt(d2),f=2200/d2,fx=dx/d*f,fy=dy/d*f;
        a.vx+=fx*k;a.vy+=fy*k;b.vx-=fx*k;b.vy-=fy*k;}}}
  // ДВЕ пружины: own (папка→док) короткая и тугая → кластеры; ref длинная и слабая
  for(const e of ED){const a=N[e.s],b=N[e.t];
    let dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)+0.01;
    const L=e.kind==='own'?64:150, str=e.kind==='own'?0.028:0.008;
    const f=(d-L)*str*k,fx=dx/d*f,fy=dy/d*f;
    a.vx+=fx;a.vy+=fy;b.vx-=fx;b.vy-=fy;}
  // лёгкая гравитация к центру + интегрирование
  for(const n of N){if(n===pinned)continue;
    n.vx+=(W/2-n.x)*0.0006*k;n.vy+=(H/2-n.y)*0.0006*k;
    n.x+=n.vx;n.y+=n.vy;n.vx*=0.85;n.vy*=0.85;}
  alpha*=0.985;
  draw();requestAnimationFrame(step);
}
function draw(){
  ctx.clearRect(0,0,W,H);ctx.lineWidth=1;
  // рёбра: tree ярче, own — призрачно-серые, ref — по типу с низкой alpha
  for(const e of ED){const a=N[e.s],b=N[e.t];
    if(e.kind==='own'){ctx.strokeStyle='rgba(120,128,148,.07)';}
    else{ctx.strokeStyle=EC[e.type]||'#3a4252';ctx.globalAlpha=e.tree?.5:.16;}
    ctx.beginPath();ctx.moveTo(sx(a),sy(a));ctx.lineTo(sx(b),sy(b));ctx.stroke();ctx.globalAlpha=1;}
  for(const n of N){const x=sx(n),y=sy(n),r=Math.max(2,n.r*view.k);
    if(n.kind==='area'){ctx.fillStyle='#1b2230';ctx.beginPath();ctx.arc(x,y,r,0,6.2832);ctx.fill();
      ctx.strokeStyle='#3a4252';ctx.lineWidth=1.4;ctx.stroke();}
    else{ctx.fillStyle=NC[n.type]||NC.untyped;ctx.globalAlpha=n.root?1:.9;
      ctx.beginPath();ctx.arc(x,y,r,0,6.2832);ctx.fill();ctx.globalAlpha=1;}
    if(n.root){ctx.strokeStyle='#34d399';ctx.lineWidth=2;ctx.stroke();}
    // ПОДПИСИ: только хабы-папки, вход, наведение или сильный зум — иначе каша
    if(n.kind==='area'||n.root||n===hover||view.k>1.7){
      ctx.fillStyle=(n.kind==='area'||n.root)?'#e6e9ef':'#cfd6e4';
      ctx.font=((n.kind==='area'||n.root)?'600 12px':'10px')+' sans-serif';
      const lbl=n.root?(n.title+' ◀ вход'):(n.kind==='area'?n.title:n.title.slice(0,32));
      ctx.fillText(lbl,x+r+4,y+3.5);}}
}
step();

const lg=document.getElementById('legend');
DATA.stats.types.forEach(t=>{const d=document.createElement('div');
  d.innerHTML=`<i style="background:${NC[t]||NC.untyped}"></i>${t}`;lg.appendChild(d);});
const al=document.createElement('div');al.innerHTML=`<i style="background:#1b2230;border:1.4px solid #3a4252"></i>папка (area)`;lg.appendChild(al);
document.getElementById('stat').textContent=DATA.stats.docs+' документов · '+DATA.stats.areas+' папок · '+DATA.stats.links+' связей';

const tip=document.getElementById('tip');
function pick(mx,my){let best=null,bd=160;
  for(const n of N){const dx=sx(n)-mx,dy=sy(n)-my,d=dx*dx+dy*dy;if(d<bd){bd=d;best=n;}}return best;}
let panning=false,last=null;
cv.addEventListener('mousedown',e=>{const n=pick(e.clientX,e.clientY);
  if(n){drag=n;pinned=n;}else panning=true;last=[e.clientX,e.clientY];});
addEventListener('mousemove',e=>{
  if(drag){drag.x=wx(e.clientX);drag.y=wy(e.clientY);drag.vx=drag.vy=0;alpha=Math.max(alpha,.3);}
  else if(panning){view.x+=e.clientX-last[0];view.y+=e.clientY-last[1];last=[e.clientX,e.clientY];draw();}
  else{hover=pick(e.clientX,e.clientY);
    if(hover){tip.style.display='block';tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';
      const sub=hover.kind==='area'?(hover.deg+' документов'):(hover.type+' · '+hover.id);
      tip.innerHTML=`<b>${hover.title}</b><br><span class="t">${sub}</span>`;}
    else tip.style.display='none';
    if(alpha<0.02)draw();}});
addEventListener('mouseup',()=>{drag=null;panning=false;pinned=null;});
cv.addEventListener('wheel',e=>{e.preventDefault();const f=e.deltaY<0?1.12:.89;
  const mx=e.clientX,my=e.clientY,bx=wx(mx),by=wy(my);
  view.k=Math.max(.2,Math.min(5,view.k*f));view.x=mx-bx*view.k;view.y=my-by*view.k;
  if(alpha<0.02)draw();},{passive:false});
cv.addEventListener('dblclick',e=>{const n=pick(e.clientX,e.clientY);if(n)prompt('путь к файлу:',n.id);});
</script></body></html>"""


def cmd_graph(root: Path, out: Path) -> dict:
    g = build_graph(root)
    payload = json.dumps(g, ensure_ascii=False).replace("</", "<\\/")
    html = (_HTML
            .replace("__DATA__", payload)
            .replace("__NODECOLORS__", json.dumps(NODE_COLORS))
            .replace("__EDGECOLORS__", json.dumps(EDGE_COLORS))
            .replace("__TITLE__", root.name))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return {"out": str(out), **g["stats"]}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gitmark-graph",
                                 description="Построить HTML-граф онтологии БЗ")
    ap.add_argument("--root", default=None, help="корень репо (по умолчанию — авто по .git)")
    ap.add_argument("-o", "--out", default=None, help="куда писать HTML (default docs/kb-graph.html)")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve() if a.root else repo_root(Path.cwd())
    out = Path(a.out) if a.out else (root / "docs" / "kb-graph.html")
    r = cmd_graph(root, out)
    print(f"✓ graph: {r['docs']} документов · {r['links']} связей → {Path(r['out']).as_posix()}")


if __name__ == "__main__":
    main()
