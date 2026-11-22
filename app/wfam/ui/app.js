"use strict";
const $ = s => document.querySelector(s);
let S = null, logSeq = 0, filter = "all", drawerOpen = false, zipKey = null, wasRunning = false, firstLoad = true;
let view = "installed", libsOpen = false, hidOpen = false;

function h(tag, attrs, ...kids){
  const e = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs||{})){
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()){ if (k === null || k === undefined || k === false) continue; e.append(k.nodeType ? k : document.createTextNode(String(k))); }
  return e;
}
function ask(title, body, yes, input, no){
  return new Promise(res=>{
    $("#askT").textContent=title; $("#askB").textContent=body; $("#askYes").textContent=yes||"OK"; $("#askNo").textContent=no||"Cancel";
    const inp=$("#askIn"); inp.hidden = input===undefined; inp.value = input||"";
    const m=$("#mAsk"); m.classList.add("show");
    const done=v=>{ m.classList.remove("show"); $("#askYes").onclick=$("#askNo").onclick=m.onclick=inp.onkeydown=null; res(v); };
    $("#askYes").onclick=()=>done(input===undefined ? true : inp.value.trim()||false); $("#askNo").onclick=()=>done(false); m.onclick=e=>{ if(e.target===m) done(false); };
    inp.onkeydown=e=>{ if(e.key==="Enter") $("#askYes").click(); };
    (input===undefined ? $("#askYes") : inp).focus();
  });
}
function toast(msg){ const t=$("#toast"); t.textContent=msg; t.style.display="block"; clearTimeout(toast._t); toast._t=setTimeout(()=>t.style.display="none",5000); }
async function post(path, body, extraHeaders){
  const isBlob = body instanceof Blob;
  const r = await fetch(path,{method:"POST",headers:Object.assign({"X-WFAM":"1"}, isBlob?{"Content-Type":"application/octet-stream"}:{"Content-Type":"application/json"}, extraHeaders||{}),body:isBlob?body:JSON.stringify(body||{})});
  const j = await r.json().catch(()=>({}));
  if (!r.ok) throw new Error(j.error || ("HTTP "+r.status));
  refresh(true);
  return j;
}
const act = (path, body, h2) => post(path, body, h2).catch(e => toast(e.message));
function fmtDate(iso){ if(!iso) return ""; const d=new Date(iso); if (isNaN(d)) return ""; return d.toLocaleDateString(undefined,{year:"numeric",month:"short",day:"numeric"}); }
function ago(iso){ if(!iso) return "never"; const s=(Date.now()-new Date(iso))/1000; if(s<90) return "just now"; if(s<3600) return Math.round(s/60)+" min ago"; if(s<86400) return Math.round(s/3600)+" h ago"; return Math.round(s/86400)+" d ago"; }
function fmtNum(n){ if (n>=1e6) return (n/1e6).toFixed(n>=1e7?0:1)+"M"; if (n>=1e3) return (n/1e3).toFixed(n>=1e4?0:1)+"k"; return String(n); }
function fmtSize(b){ if(!b) return ""; return b>=1048576 ? (b/1048576).toFixed(1)+" MB" : Math.max(1,Math.round(b/1024))+" KB"; }
// Icons in My addons are reused between redraws (that list is redrawn while a job runs), so they're
// decoded once and don't flicker. Only that list reuses them: an image can only be in one place, and
// Browse / the detail panel showing the same icon must not take it away from the list (or vice versa).
const logoCache = new Map();
let logoTick = 0, logoTickQueued = false;
// An icon that starts loading but never finishes (slow or stuck image server) shows the letter instead
// of an empty box: checked 12 s after it scrolls into view.
const logoWatch = "IntersectionObserver" in window ? new IntersectionObserver(es=>{
  for (const e of es){
    if (!e.isIntersecting) continue;
    const img = e.target; logoWatch.unobserve(img);
    setTimeout(()=>{ if (img.isConnected && (!img.complete || !img.naturalWidth)){ img.dataset.bad = "1"; img.replaceWith(img._fb()); } }, 12000);
  }
}) : null;
function watchLogo(img, fb){ if (!logoWatch) return; img._fb = fb; img.addEventListener("load", ()=>logoWatch.unobserve(img), {once:true}); logoWatch.observe(img); }
function hueOf(name){ let x = 0; for (const ch of String(name||"")) x = (x*31 + ch.charCodeAt(0)) >>> 0; return x % 360; }
function logoEl(src, name, cls, reuse){
  const fb=()=>h("div",{class:(cls||"logo")+" fb",style:"--hue:"+hueOf(name)},((name||"?").replace(/^[^A-Za-z0-9]+/,"")||"?")[0].toUpperCase());
  if (!src) return fb();
  const make = ()=>{ const img = h("img",{class:cls||"logo",src,alt:"",loading:"lazy",decoding:"async",referrerpolicy:"no-referrer",
    onerror:function(){ this.dataset.bad = "1"; this.replaceWith(fb()); }}); watchLogo(img, fb); return img; };
  if (!reuse) return make();
  if (!logoTickQueued){ logoTickQueued = true; queueMicrotask(()=>{ logoTick++; logoTickQueued = false; }); }
  const key = (cls||"logo") + "|" + src;
  let img = logoCache.get(key);
  if (img && img.dataset.bad) return fb();
  if (img && img._tick === logoTick) return make();        // same icon twice in one redraw
  if (!img){ img = make(); if (logoCache.size > 600) logoCache.clear(); logoCache.set(key, img); }
  img._tick = logoTick;
  return img;
}

function clientName(n){
  n = parseInt(n,10); if (!n) return "another client";
  if (n >= 16000 && n < 17000){ const cur = parseInt((S && S.config.interface) || "16001",10); return n < cur ? "an older WoW Forever patch" : n > cur ? "a newer WoW Forever patch" : "WoW Forever"; }
  if (n < 20000) return "Classic Era";
  if (n < 30000) return "TBC Anniversary";
  if (n < 40000) return "Titan-Reforged Classic";
  if (n < 50000) return "Cataclysm Classic";
  if (n < 60000) return "MoP Classic";
  if (n >= 100000) return "Retail";
  return "another client";
}
function compatNote(ifs, file){
  const names = [...new Set(ifs.map(clientName))].join(" / ");
  return h("div",{class:"compat",title:`${file||".toc"} says Interface ${ifs.join(", ")}. WoW Forever expects ${(S && S.config.interface) || "16001"}, so the game may list it as out of date or not load it. Check for updates — a newer version may fix it.`}, "⚠ Made for " + names + " (" + ifs.join(", ") + ")");
}
const STATUS = {
  ok:["Up to date","ok"], update:["Update available","update"], update_manual:["Update — manual download","update_manual"],
  manual:["Manual download needed","manual"], missing:["Folders missing","missing"], pinned:["Pinned","pinned"],
  unchecked:["Not checked yet","unchecked"], not_installed:["Not installed","not_installed"],
  awaiting:["Waiting for download…","pinned"]
};
const isUpd = a => a.status==="update"||a.status==="update_manual";
const isBad = a => a.status==="manual"||a.status==="missing"||a.compat==="mismatch"||(a.missing_deps||[]).length>0;
const rank = a => isUpd(a) ? 0 : isBad(a) ? 1 : a.status==="awaiting" ? 2 : 3;
// My addons sort: a menu next to the search box, or click a column heading (again to flip the order)
const MS = {by:"status", dir:1};
const MS_DEFAULT_DIR = {status:1, name:1, installed:-1, latest:-1};
// big UI packs: a chip appears next to "Other folders" when you have one, showing it and its plugins
const SUITES = [{id:"elvui", name:"ElvUI", re:/^elvui/i}, {id:"tukui", name:"Tukui", re:/^tukui/i}, {id:"ellesmere", name:"EllesmereUI", re:/^ellesmere/i}];
const suiteOf = x => SUITES.find(su=>inSuite(x, su)) || null;
const inSuite = (x, su) => su.re.test(x.name||x.title||"") || (x.folders||[]).some(f=>su.re.test(f)) || (x.deps||[]).some(d=>su.re.test(d));
const SRC_LABEL = {curseforge:"CurseForge", wago:"Wago", github:"GitHub", tukui:"tukui.org"};
let mSrc = "";
const byName = (a,b)=>(a.name||"").localeCompare(b.name||"", undefined, {sensitivity:"base"});
function mCompare(a, b){
  const d = MS.dir;
  switch (MS.by){
    case "name": return d*byName(a,b);
    case "installed": return d*String(a.installed_at||"").localeCompare(String(b.installed_at||"")) || byName(a,b);
    case "latest": return d*String(a.latest_date||a.installed_file_date||"").localeCompare(String(b.latest_date||b.installed_file_date||"")) || byName(a,b);
    default: return d*(rank(a)-rank(b)) || byName(a,b);
  }
}
function setSort(by, dir){
  MS.dir = dir || (MS.by===by ? -MS.dir : MS_DEFAULT_DIR[by] || 1); MS.by = by;
  saveUi(); render();
}
function sortTh(label, by, cls){
  const on = MS.by===by;
  return h("th",{class:"sortable" + (on?" on":"") + (cls?" "+cls:""), title:"Sort by " + label.toLowerCase() + (on?" — click again to reverse":""), onclick:()=>setSort(by)},
    label, on ? h("span",{class:"ar"}, MS.dir>0 ? "▲" : "▼") : null);
}

// ---------- popover menus ----------
function openMenu(anchor, items){
  const pop = $("#pop"); pop.replaceChildren();
  for (const it of items){
    if (!it) continue;
    if (it === "-"){ pop.append(h("hr")); continue; }
    if (it.head){ pop.append(h("div",{class:"hd"}, it.head)); continue; }
    const el = it.href ? h("a",{href:it.href, target:it.download?null:"_blank", download:it.download||null, class:it.danger?"danger":null}, it.label)
                       : h("button",{class:it.danger?"danger":null, disabled:!!it.disabled}, it.label);
    el.addEventListener("click", e=>{ closeMenu(); if (it.run){ e.preventDefault(); it.run(); } });
    pop.append(el);
  }
  pop.hidden = false;
  const r = anchor.getBoundingClientRect(), w = pop.offsetWidth, hh = pop.offsetHeight;
  pop.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.right - w)) + "px";
  pop.style.top = (r.bottom + 6 + hh > innerHeight ? Math.max(8, r.top - hh - 6) : r.bottom + 6) + "px";
}
function closeMenu(){ $("#pop").hidden = true; }
document.addEventListener("mousedown", e=>{ if (!$("#pop").hidden && !e.target.closest("#pop")) closeMenu(); }, true);
$("#scroll").addEventListener("scroll", closeMenu);
window.addEventListener("resize", closeMenu);

// ---------- main render ----------
function render(){
  const c = S.config, busy = S.job.running;
  const d = c.addons_dir || "";
  const short = d ? d.split(/[\\/]/).filter(Boolean).slice(-3).join("\\") : "";
  $("#path").replaceChildren(d ? h("span",{title:d}, h("b",{},"AddOns: "), "…\\"+short) : "No AddOns folder chosen yet");
  $("#verTxt").textContent = "Version " + (S.app && S.app.version || "");
  $("#bQuit").hidden = !!c.hosted;
  const m = S.managed;
  const nIns = m.filter(a=>a.folders.length).length, nUpd = m.filter(isUpd).length, nBad = m.filter(isBad).length;
  $("#sAll").textContent = nIns; $("#sUpd").textContent = nUpd; $("#sBad").textContent = nBad; $("#sUn").textContent = S.unmanaged.length;
  for (const [sel,n] of [[".stat.upd",nUpd],[".stat.bad",nBad],['.stat[data-f="unmanaged"]',S.unmanaged.length]]) $(sel).classList.toggle("zero", !n);
  $("#tabN").textContent = nIns; $("#tabN").classList.toggle("hot", nUpd>0);
  if (nUpd) $("#tabN").textContent = nIns + " · " + nUpd + "↑";
  $("#gameWarn").hidden = !S.game_running || view==="chars";
  $("#chGame").hidden = !S.game_running;
  $("#bScan").disabled = busy;
  renderBackups(); renderDiag();
  if (!busy && sending !== null) sending = null;
  if (!busy) pumpQueue();
  renderBrowse();
  document.querySelectorAll(".stat").forEach(e=>e.classList.toggle("on", e.dataset.f===filter));
  const nU = m.filter(a=>a.status==="update").length;
  $("#bUpdAll").textContent = nU ? `Update all (${nU})` : "Update all";
  $("#bUpdAll").classList.toggle("primary", nU > 0);
  $("#bUpdAll").title = nU ? "Install every update that's ready" : "Nothing to update right now — this checks first and installs anything new";
  for (const id of ["#bCheck","#bUpdAll","#bAdd"]) $(id).disabled = busy;
  if (view==="installed"){ const checked = m.map(a=>a.checked_at).filter(Boolean).sort().pop(); $("#vSub").textContent = m.length ? "Last check: " + ago(checked) : ""; }
  if (DT.id) renderDetail();
  renderSelfUpdate(); checkBatch();

  // UI-pack chips (only for packs you have) and the source menu (only when there's more than one source)
  const suitesHave = SUITES.filter(su=>m.some(a=>a.folders.length && suiteOf(a)===su) || S.unmanaged.some(g=>suiteOf(g)===su));
  const nUi = m.filter(a=>a.folders.length && suiteOf(a)).length + S.unmanaged.filter(g=>suiteOf(g)).length;
  if (filter==="ui" && !nUi) filter = "all";
  $("#suites").replaceChildren(...(nUi ? [h("button",{class:"stat suite" + (filter==="ui" ? " on" : ""), "data-f":"ui",
    title:"Your UI packs and their plugins: " + suitesHave.map(su=>su.name).join(", "), onclick:()=>{ filter = filter==="ui" ? "all" : "ui"; saveUi(); render(); }}, "UI ", h("b",{}, String(nUi)))] : []));
  const nErr = m.filter(a=>a.errors).length, nUnused = m.filter(a=>a.unused).length;
  if ((filter==="errors" && !nErr) || (filter==="unused" && !nUnused)) filter = "all";
  const chip = (f, label, n, cls, tip) => h("button",{class:"stat suite " + cls + (filter===f ? " on" : ""), "data-f":f, title:tip,
    onclick:()=>{ filter = filter===f ? "all" : f; saveUi(); render(); }}, label + " ", h("b",{}, String(n)));
  $("#suites").append(...[nErr ? chip("errors", "Errors", nErr, "err", "Addons with Lua errors in BugGrabber (last 14 days)") : null,
    nUnused ? chip("unused", "Unused", nUnused, "unused", "Switched off on every character — you could remove them") : null].filter(Boolean));
  const srcCount = {};
  for (const a of m) if (a.folders.length) srcCount[a.source||"curseforge"] = (srcCount[a.source||"curseforge"]||0) + 1;
  const srcs = Object.keys(srcCount).sort((x,y)=>srcCount[y]-srcCount[x]);
  if (mSrc && !srcCount[mSrc]) mSrc = "";
  const sel = $("#msrc"), want = ["", ...srcs].map(x=>x + ":" + (x ? srcCount[x] : nIns)).join("|");
  if (sel.dataset.k !== want){
    sel.dataset.k = want;
    sel.replaceChildren(h("option",{value:""}, "All sources"), ...srcs.map(x=>h("option",{value:x}, (SRC_LABEL[x]||x) + " (" + srcCount[x] + ")")));
  }
  sel.value = mSrc; sel.hidden = srcs.length < 2;
  const suite = filter==="ui";

  const q = $("#q").value.trim().toLowerCase();
  let rows = m.filter(a => !q || (a.name||"").toLowerCase().includes(q) || a.folders.join(" ").toLowerCase().includes(q) || (a.authors||[]).join(" ").toLowerCase().includes(q));
  if (filter==="updates") rows = rows.filter(isUpd);
  if (filter==="problems") rows = rows.filter(isBad);
  if (filter==="unmanaged") rows = [];
  if (suite) rows = rows.filter(a=>suiteOf(a));
  if (filter==="errors") rows = rows.filter(a=>a.errors);
  if (filter==="unused") rows = rows.filter(a=>a.unused);
  if (mSrc) rows = rows.filter(a=>(a.source||"curseforge")===mSrc);
  rows.sort(mCompare);

  const card = $("#managedCard");
  card.hidden = filter==="unmanaged";
  if (!c.has_key || !c.addons_dir_ok){
    card.replaceChildren(setupCard(c));
  } else if (!m.length){
    card.replaceChildren(h("div",{class:"empty"}, h("b",{},"No addons yet"), h("br"), "Find addons in Browse, or paste CurseForge links. Addons you copied into AddOns by hand are listed below.",
      h("div",{class:"acts2"}, h("button",{class:"btn primary",onclick:()=>showView("browse")},"Browse addons"), h("button",{class:"btn",onclick:openAdd},"Paste links"))));
  } else if (!rows.length){
    card.replaceChildren(h("div",{class:"empty"},"Nothing matches this filter."));
  } else {
    const main = [], libs = [];
    for (const a of rows) ((a.library && rank(a)===3 && !q && MS.by==="status" && !suite && filter==="all") ? libs : main).push(a);
    const tb = h("tbody");
    if (suite){   // grouped under each UI pack: "ElvUI (3)", "EllesmereUI (1)" …
      for (const su of SUITES){
        const g = main.filter(a=>suiteOf(a)===su);
        if (!g.length) continue;
        tb.append(h("tr",{class:"grp head"}, h("td",{colspan:narrow() ? 4 : 5}, su.name + " ", h("span",{class:"dim"}, "(" + g.length + ")"))));
        for (const a of g) tb.append(addonRow(a, busy));
      }
    } else for (const a of main) tb.append(addonRow(a, busy));
    if (libs.length){
      tb.append(h("tr",{class:"grp",onclick:()=>{ libsOpen=!libsOpen; render(); }}, h("td",{colspan:narrow() ? 4 : 5}, (libsOpen?"▾ ":"▸ ") + "Libraries ", h("span",{class:"dim"},"("+libs.length+") — shared code other addons need"))));
      if (libsOpen) for (const a of libs) tb.append(addonRow(a, busy));
    }
    card.replaceChildren(h("table",{}, h("thead",{}, h("tr",{}, sortTh("Addon","name"), sortTh("Installed","installed"), sortTh("Latest for Forever","latest","hide-s"), sortTh("Status","status"), h("th",{}))), tb));
  }

  // other folders
  const un = suite ? S.unmanaged.filter(g=>suiteOf(g)) : (mSrc || filter==="errors" || filter==="unused") ? [] : S.unmanaged, hid = S.hidden_folders || [];
  $("#unC").textContent = un.length;
  const showUn = (filter==="all"||filter==="unmanaged"||(suite && un.length)) && c.addons_dir_ok && !mSrc;
  $("#unH").style.display = showUn && (un.length || filter==="unmanaged") ? "" : "none";
  $("#unCard").style.display = $("#unH").style.display;
  if (!un.length){
    $("#unCard").replaceChildren(h("div",{class:"empty"},"Every folder in AddOns is managed here."));
  } else {
    const tb = h("tbody");
    for (const g of un){
      tb.append(h("tr",{},
        h("td",{}, h("div",{style:"font-weight:600"}, g.title), h("div",{class:"dim",style:"font-size:12px"}, g.recognized ? (g.forever===false ? "✓ recognised on CurseForge — but this copy isn't the WoW Forever version" : "✓ recognised on CurseForge") : g.wago_id ? "✓ found on Wago" + (g.wago_version ? " (" + g.wago_version + ")" : "") : (g.has_toc ? g.folders.join(", ") : "no .toc file — WoW will ignore this folder")),
          (g.used_by||[]).length ? h("div",{class:"dim",style:"font-size:12px"}, "Library used by " + g.used_by.join(", ")) : null),
        h("td",{}, h("span",{class:"vv"}, g.version || "—")),
        h("td",{class:"hide-s"}, g.compat==="mismatch" ? compatNote(g.interfaces, g.toc_file) : h("span",{class:"dim"}, g.interfaces.join(", ")||"—"),
          (g.missing_deps||[]).length ? h("div",{class:"compat",style:"color:var(--red)"},"⚠ Needs: "+g.missing_deps.join(", ")) : null),
        h("td",{}, h("div",{class:"acts"}, g.recognized
          ? h("button",{class:"btn small primary",disabled:busy,title:g.forever===false?"Install the WoW Forever version and track it for updates":"Track it for updates — nothing is downloaded",onclick:()=>act("/api/adopt",{mod_id:g.project_id})}, g.forever===false ? "Install Forever version" : "Manage")
          : g.project_id
          ? h("button",{class:"btn small",disabled:busy,title:"Reinstall the WoW Forever version from CurseForge and track it for updates",onclick:()=>act("/api/manage",{project_id:g.project_id})},"Manage with updates")
          : g.wago_id
          ? h("button",{class:"btn small primary",disabled:busy,title:"Install it from Wago (CurseForge too if it's there — the newest wins) and keep it updated",onclick:()=>act("/api/add_wago",{id:g.wago_id})},"Manage with updates")
          : null,
          h("button",{class:"btn small ghost",title:"You look after this one yourself (e.g. Zygor): hide it here",onclick:()=>act("/api/ignore",{folders:g.folders,ignore:true})},"Hide")))
      ));
    }
    $("#unCard").replaceChildren(h("table",{}, h("thead",{}, h("tr",{}, h("th",{},"Folder / title"), h("th",{},"Version (.toc)"), h("th",{class:"hide-s"},"Interface"), h("th",{}))), tb));
  }
  const hb = $("#hidBox");
  hb.style.display = showUn && !suite ? "" : "none";
  if (!hid.length) hb.replaceChildren();
  else hb.replaceChildren(h("button",{class:"linkbtn",style:"color:var(--dim)",onclick:()=>{ hidOpen=!hidOpen; render(); }}, (hidOpen?"▾ ":"▸ ") + hid.length + " hidden folder" + (hid.length>1?"s":"") + " you look after yourself"),
    hidOpen ? h("div",{class:"card",style:"margin-top:8px"}, ...hid.map(g=>h("div",{class:"bk"}, h("div",{class:"n"}, h("b",{},g.title), h("span",{class:"dim",style:"margin-left:8px;font-size:12px"}, g.folders.join(", "))), h("button",{class:"btn small",onclick:()=>act("/api/ignore",{folders:g.folders,ignore:false})},"Show again")))) : null);

  // status bar
  const lb = $("#logbox");
  for (const e of S.logs){
    if (e.seq <= logSeq) continue;
    lb.append(h("div",{class:"lg "+e.level}, h("span",{class:"tm"},e.t), e.msg));
    logSeq = e.seq;
  }
  while (lb.children.length > 400) lb.firstChild.remove();
  lb.scrollTop = lb.scrollHeight;
  const vis = [...lb.querySelectorAll(".lg:not(.debug)")];
  const last = vis[vis.length-1];
  $("#dLast").textContent = busy ? (S.job.title + "… " + (last?last.textContent.slice(8):"")) : (last ? last.textContent.slice(8) : "Ready.");
  const dot = $("#dDot");
  if (busy){ if (!dot.classList.contains("spin")) dot.className="spin"; }
  else dot.className = "dot " + (S.job.ok===false ? "err" : S.job.ok ? "ok" : "");
  if (busy && !wasRunning && /^Updating WoW Forever Addons/.test(S.job.title || "")){ setDrawer(true); }
  wasRunning = busy;
}

function addonRow(a, busy){
  const [label, cls] = STATUS[a.status] || [a.status,"unchecked"];
  const acts = [];
  if (a.status==="update"||a.status==="missing") acts.push(h("button",{class:"btn small primary",disabled:busy,onclick:()=>act("/api/update",{key:a.key})}, a.status==="missing"?"Reinstall":"Update"));
  if (a.status==="awaiting"){
    acts.push(h("a",{class:"btn small",href:a.download_page,target:"_blank",rel:"noopener",title:"Opens CurseForge's download page again"},"Download page ↗"));
    acts.push(h("button",{class:"btn small",disabled:busy,onclick:()=>pickZip(a.key)},"Install zip…"));
    acts.push(h("button",{class:"btn small ghost",onclick:()=>act("/api/cancel_wait",{key:a.key})},"Cancel"));
  }
  if (a.status==="manual"||a.status==="update_manual"){
    if (S.config.auto_manual) acts.push(h("button",{class:"btn small primary",disabled:busy,title:"Opens CurseForge's download page; the addon installs by itself when the download finishes",onclick:()=>act("/api/update",{key:a.key})}, a.status==="manual"?"Download & install":"Download & update"));
    else acts.push(h("a",{class:"btn small",href:a.download_page,target:"_blank",rel:"noopener"},"Download page ↗"));
    acts.push(h("button",{class:"btn small",disabled:busy,onclick:()=>pickZip(a.key)},"Install zip…"));
  }
  if ((a.missing_deps||[]).length) acts.push(h("button",{class:"btn small primary",disabled:busy,title:"Install what this addon needs",onclick:()=>act("/api/fix_deps",{key:a.key})},"Install missing"));
  acts.push(h("button",{class:"btn small ghost more",title:"More",onclick:e=>openMenu(e.currentTarget, rowMenu(a, busy))},"⋯"));
  const latest = a.latest_version
    ? h("div",{class:"vv",title:a.latest_version + (a.latest_from ? " — newest on " + (SRC_NAME[a.latest_from]||a.latest_from) : "")}, shortVer(a.latest_version),
        h("div",{class:"d"}, (a.latest_from && a.latest_from !== "curseforge" ? (SRC_NAME[a.latest_from]||a.latest_from) + " · " : "") + fmtDate(a.latest_date)))
    : h("div",{class:"vv dim"}, a.checked_at ? "no Forever file" : "—");
  const inst = a.installed_version ? h("div",{class:"vv",title:a.installed_version}, shortVer(a.installed_version),
      h("div",{class:"d"}, (a.source==="github"?"GitHub · ":"") + (a.source==="wago"?"Wago · ":"") + (a.external?"changed outside the manager · ":"") + (a.manual?"manual zip · ":"") + (a.installed_release_type!=="Release"? a.installed_release_type+" · ":"") + fmtDate(a.installed_date)))
    : h("span",{class:"dim"},"—");
  const tr = h("tr",{class:"arow",oncontextmenu:e=>{ if (e.target.closest("a,input")) return; e.preventDefault();
      openMenu({getBoundingClientRect:()=>{ const w = $("#pop").offsetWidth; return {left:e.clientX, right:e.clientX + w, top:e.clientY - 6, bottom:e.clientY - 6}; }}, rowMenu(a, !!(S && S.job.running))); }},
    h("td",{}, h("div",{class:"nm"}, logoEl(a.logo, a.name, null, true), h("div",{style:"min-width:0"}, h("div",{class:"t",title:a.name}, a.name), (filter==="unused" ? h("div",{class:"s",style:"color:var(--amber)"}, "Off on every character") : h("div",{class:"s",title:a.summary||""}, a.summary || (a.authors||[]).slice(0,2).join(", ")))))),
    h("td",{}, inst),
    h("td",{class:"hide-s"}, latest),
    h("td",{}, ...statusCell(a, label, cls), a.compat==="mismatch" ? compatNote(a.toc_interfaces, a.toc_file) : null,
      (a.missing_deps||[]).length ? h("div",{class:"compat",style:"color:var(--red)",title:"The addon's .toc says it can't load without these"},"⚠ Needs: "+a.missing_deps.join(", ")) : null,
      a.errors ? h("button",{class:"errl",title:a.errors.message,onclick:()=>showErrors(a.key)},
        a.errors.since_update ? "⚠ Errors since this update" : `⚠ ${a.errors.n} Lua error${a.errors.n===1?"":"s"}`) : null),
    h("td",{}, h("div",{class:"acts"}, acts)));
  tr.addEventListener("click", e=>{ if (!e.target.closest("button,a") && !a.wago_only && !a.tukui) openDetail(a.key); });
  return tr;
}
function rowMenu(a, busy){
  return [
    a.wago_only || a.tukui ? null : {label:"Details…", run:()=>openDetail(a.key)},
    a.latest_version && !a.wago_only && ["update","update_manual","ok","pinned"].includes(a.status) ? {label:"What's new", run:()=>showChangelog(a.key)} : null,
    a.errors ? {label:"Lua errors…", run:()=>showErrors(a.key)} : null,
    "-",
    {label: a.pinned ? "Unpin — allow updates again" : "Pin — keep this version", disabled:busy, run:()=>act("/api/pin",{key:a.key,pinned:!a.pinned})},
    a.previous_version ? {label:"Revert to "+a.previous_version, disabled:busy, run:async ()=>{ if (await ask(`Revert ${a.name}?`, `Goes back to ${a.previous_version} and pins it so Update all skips it. You can switch back the same way.`, "Revert")) act("/api/revert",{key:a.key}); }} : null,
    {label:"Install from a zip…", disabled:busy, run:()=>pickZip(a.key)},
    a.url ? {label: a.wago_only ? "Open on Wago ↗" : a.tukui ? "Open on tukui.org ↗" : "Open on CurseForge ↗", href:a.url} : null,
    a.wago_url && !a.wago_only ? {label:"Open on Wago ↗", href:a.wago_url} : null,
    "-",
    {label:"Remove", danger:true, disabled:busy, run:async ()=>{ if (await ask(`Remove ${a.name}?`, a.folders.length ? `These folders will be deleted from AddOns:\n${a.folders.join("\n")}` : "It will no longer be tracked.", "Remove")) act("/api/remove",{key:a.key}); }},
  ];
}

// ---------- updates of the program itself ----------
function renderSelfUpdate(){
  const u = S.self_update, box = $("#selfUpd");
  if (!u) selfOffered = "";
  box.hidden = !u;
  if (!u) return;
  offerSelfUpdate(u);
  if (box.dataset.v === u.version + String(S.job.running)) return;
  box.dataset.v = u.version + String(S.job.running);
  box.replaceChildren(h("b",{},"Version " + u.version + " is ready"), h("button",{class:"btn small primary",disabled:S.job.running,onclick:async ()=>{
    if (await ask("Update WoW Forever Addons to " + u.version + "?", (u.notes ? u.notes.slice(0,700) + "\n\n" : "") + "The program closes, updates itself and opens again. Your addons and settings stay as they are.", "Update now")) act("/api/self_update");
  }},"Update now"), " ", h("a",{href:u.page,style:"font-size:12px"},"What's new ↗"));
}
// ---------- what the last update changed ----------
let batchSeen = null;
// The summary never pops up by itself: a small "✓ N updated" button next to Update all opens it.
function checkBatch(){
  const b = S.last_batch, btn = $("#bLast");
  btn.hidden = !(b && b.items && b.items.length);
  if (btn.hidden) return;
  const fresh = Date.now() - new Date(b.at).getTime() < 24*3600*1000;
  btn.textContent = "✓ " + b.items.length + " updated" + (fresh ? "" : " · " + new Date(b.at).toLocaleDateString());
}
$("#bLast").onclick = ()=>showBatch();
function showBatch(){
  const b = S.last_batch; if (!b) return;
  $("#btT").textContent = b.items.length === 1 ? "1 addon updated" : b.items.length + " addons updated";
  $("#btS").textContent = new Date(b.at).toLocaleString(undefined,{dateStyle:"medium",timeStyle:"short"}) + " · if something breaks, “Undo this update” puts the earlier versions back.";
  $("#btL").replaceChildren(...b.items.map(it=>h("div",{class:"bl"}, h("div",{class:"n"}, h("b",{},it.name), h("div",{class:"v"}, (it.from||"?") + "  →  " + (it.to||"?"))),
    h("button",{class:"btn small ghost",onclick:()=>{ $("#mBatch").classList.remove("show"); showChangelog(it.key); }},"What's new"))));
  $("#mBatch").classList.add("show");
}
$("#btClose").onclick = ()=>$("#mBatch").classList.remove("show");
$("#mBatch").addEventListener("click", e=>{ if (e.target.id==="mBatch") $("#mBatch").classList.remove("show"); });
$("#btUndo").onclick = async ()=>{ $("#mBatch").classList.remove("show"); if (await ask("Undo the last update?", "Puts back the versions you had before for: " + S.last_batch.items.map(i=>i.name).join(", ") + ".\n\nThey get pinned so Update all leaves them alone — unpin them when fixed versions are out.", "Undo")) act("/api/undo_batch"); };

// ---------- detail panel ----------
const DT = {id:null, data:null, error:"", tab:"about", cl:{}, loading:false};
function openDetail(id){
  id = String(id);
  if (DT.id !== id){ DT.data = null; DT.error = ""; DT.cl = {}; DT.tab = "about"; }
  DT.id = id; DT.loading = true;
  $("#detail").classList.add("show"); $("#shade").classList.add("show"); $("#detail").setAttribute("aria-hidden","false");
  renderDetail();
  post("/api/details",{id:+id}).then(d=>{ if (DT.id===id){ DT.data=d; DT.loading=false; renderDetail(); } })
    .catch(e=>{ if (DT.id===id){ DT.error=e.message; DT.loading=false; renderDetail(); } });
}
function closeDetail(){ DT.id = null; $("#detail").classList.remove("show"); $("#shade").classList.remove("show"); $("#detail").setAttribute("aria-hidden","true"); }
const OKTAGS = new Set("P BR B STRONG I EM U S UL OL LI A H1 H2 H3 H4 H5 H6 IMG SPAN DIV BLOCKQUOTE PRE CODE HR TABLE THEAD TBODY TR TD TH SMALL SUP SUB CENTER FIGURE FIGCAPTION DL DT DD".split(" "));
const DROP = new Set("SCRIPT STYLE IFRAME OBJECT EMBED FORM INPUT BUTTON TEXTAREA SELECT SVG MATH LINK META NOSCRIPT TEMPLATE VIDEO AUDIO SOURCE FRAME FRAMESET BASE".split(" "));
function safeUrl(u){ try{ const x = new URL(u, "https://www.curseforge.com/"); return /^https?:$/.test(x.protocol) ? x.href : null; }catch(e){ return null; } }
// The description is HTML written by the addon's author: rebuilt from scratch with only harmless parts.
function sanitize(html){
  const doc = new DOMParser().parseFromString(html||"", "text/html");
  const walk = (node, out) => {
    for (const ch of [...node.childNodes]){
      if (ch.nodeType === 3){ out.append(ch.textContent); continue; }
      if (ch.nodeType !== 1) continue;
      const tag = ch.tagName;
      if (DROP.has(tag)) continue;
      if (!OKTAGS.has(tag)){ walk(ch, out); continue; }
      const el = document.createElement(tag==="CENTER" ? "DIV" : tag);
      if (tag==="A"){ const u = safeUrl(ch.getAttribute("href")); if (u) el.setAttribute("href", u); }
      if (tag==="IMG"){ const u = safeUrl(ch.getAttribute("src")); if (!u) continue; el.src = u; el.loading = "lazy"; el.referrerPolicy = "no-referrer"; el.alt = ch.getAttribute("alt")||""; el.onclick = ()=>lightbox(u); }
      if (tag==="TD"||tag==="TH"){ const cs = ch.getAttribute("colspan"); if (cs && /^\d+$/.test(cs)) el.setAttribute("colspan", cs); }
      walk(ch, el); out.append(el);
    }
  };
  const box = h("div",{class:"desc"}); walk(doc.body, box); return box;
}
function lightbox(u){ $("#lbImg").src = u; $("#lightbox").classList.add("show"); }
function renderDetail(){
  if (!DT.id) return;
  const d = DT.data, a = S ? S.managed.find(x=>x.key===DT.id) : null, busy = S && S.job.running;
  const name = d ? d.name : (a ? a.name : (B.results.find(r=>String(r.id)===DT.id)||{}).name || "Loading…");
  const logo = d ? d.logo : (a ? a.logo : (B.results.find(r=>String(r.id)===DT.id)||{}).logo);
  const acts = [];
  if (a && a.folders.length){
    if (a.status==="update") acts.push(h("button",{class:"btn primary",disabled:busy,onclick:()=>act("/api/update",{key:a.key})},"Update to "+(a.update_version||a.latest_version||"latest")));
    else acts.push(h("span",{class:"pill p-"+(STATUS[a.status]||["","unchecked"])[1]}, (STATUS[a.status]||[a.status])[0]));
  } else if (a && a.status==="awaiting") acts.push(h("span",{class:"tag warn"},"Waiting for download…"));
  else if (queue.includes(+DT.id) || sending===+DT.id) acts.push(h("button",{class:"btn primary",disabled:true},"Installing…"));
  else acts.push(h("button",{class:"btn primary",disabled:busy || !(S && S.config.has_key),onclick:()=>installFromBrowse(+DT.id)},"Install"));
  if (d && d.url) acts.push(h("a",{class:"btn",href:d.url},"CurseForge ↗"));
  if (d && d.source) acts.push(h("a",{class:"btn",href:d.source},"Source ↗"));
  if (a) acts.push(h("button",{class:"btn icon",title:"More",onclick:e=>openMenu(e.currentTarget, rowMenu(a, busy))},"⋯"));
  const meta = [];
  if (d){ meta.push(h("span",{class:"tag"},"⬇ "+fmtNum(d.downloads))); if (d.updated) meta.push(h("span",{class:"tag"},"Updated "+fmtDate(d.updated))); for (const c of d.categories.slice(0,3)) meta.push(h("span",{class:"tag"},c)); if (d.manual_only) meta.push(h("span",{class:"tag warn",title:"The author only allows downloads on CurseForge's own site"},"CurseForge-site download")); }
  $("#dHead").replaceChildren(logoEl(logo, name),
    h("div",{style:"flex:1;min-width:0"}, h("div",{class:"ttl"}, name), h("div",{class:"by"}, d && d.authors.length ? "by "+d.authors.join(", ") : (a && a.authors ? "by "+a.authors.join(", ") : "")),
      h("div",{class:"sacts"}, acts), meta.length ? h("div",{class:"smeta"}, meta) : null),
    h("button",{class:"btn icon ghost x",title:"Close (Esc)",onclick:closeDetail},"✕"));
  document.querySelectorAll(".stab").forEach(t=>t.classList.toggle("on", t.dataset.t===DT.tab));
  const body = $("#dBody");
  if (DT.error){ body.replaceChildren(h("div",{class:"empty"}, h("b",{},"Couldn't load the details"), h("br"), DT.error)); return; }
  if (!d){ body.replaceChildren(h("div",{class:"empty"}, h("span",{class:"spin",style:"display:inline-block;vertical-align:middle;margin-right:8px"}), "Loading…")); return; }
  if (DT.tab==="about"){
    const kids = [];
    if (d.summary) kids.push(h("p",{class:"muted",style:"margin:0 0 14px;font-size:14px"}, d.summary));
    if (d.screenshots.length) kids.push(h("div",{class:"shots"}, ...d.screenshots.map(s=>h("img",{src:s.thumb||s.url,alt:s.title,title:s.title,loading:"lazy",referrerpolicy:"no-referrer",onclick:()=>lightbox(s.url)}))));
    kids.push(d.description ? sanitize(d.description) : h("div",{class:"dim"},"No description."));
    body.replaceChildren(...kids);
  } else if (DT.tab==="versions"){
    if (!d.files.length){ body.replaceChildren(h("div",{class:"empty"},"No WoW Forever files.")); return; }
    body.replaceChildren(h("p",{class:"dim",style:"margin:0 0 10px;font-size:12.5px"},"Installing a specific version pins it, so Update all leaves it alone until you unpin it."),
      h("div",{class:"fl"}, ...d.files.map(f=>{
        const cl = DT.cl[f.id];
        return h("div",{class:"fr",style:"flex-wrap:wrap"},
          h("div",{class:"n"}, h("b",{title:f.name}, f.name), h("span",{}, fmtDate(f.date) + (f.size ? " · "+fmtSize(f.size) : ""))),
          f.type!=="Release" ? h("span",{class:"tag warn"}, f.type) : null,
          f.installed ? h("span",{class:"tag ok"},"Installed") : null,
          h("button",{class:"btn small ghost",onclick:()=>toggleCl(f.id)}, cl ? "Hide notes" : "What's new"),
          !f.installed && !d.manual_only ? h("button",{class:"btn small",disabled:busy,onclick:async ()=>{ if (await ask(`Install ${f.name}?`, "This exact version is installed and pinned, so Update all skips it until you unpin it.", "Install")) act("/api/install_file",{mod_id:d.id,file_id:f.id}); }},"Install") : null,
          cl ? h("div",{class:"cl",style:"flex-basis:100%"}, cl) : null);
      })));
  } else {
    const kv = [["Project ID", d.id], ["Released", fmtDate(d.released)], ["Last updated", fmtDate(d.updated)], ["Downloads", d.downloads.toLocaleString()], ["Categories", d.categories.join(", ")||"—"]];
    if (a){ kv.push(["Installed", (a.installed_version||"—") + (a.source && a.source!=="curseforge" ? " (from "+a.source+")" : "")]); kv.push(["Folders", a.folders.join(", ")||"—"]); if ((a.missing_deps||[]).length) kv.push(["Missing", a.missing_deps.join(", ")]); if (a.toc_interfaces) kv.push(["Interface (.toc)", (a.toc_interfaces||[]).join(", ")||"—"]); }
    const links = [d.url && ["CurseForge", d.url], d.source && ["Source code", d.source], d.issues && ["Report a problem", d.issues], d.wiki && ["Wiki", d.wiki]].filter(Boolean);
    body.replaceChildren(h("div",{class:"kv"}, ...kv.flatMap(([k,v])=>[h("div",{class:"k"},k), h("div",{class:"dd"}, String(v))]),
      h("div",{class:"k"},"Links"), h("div",{}, ...links.map(([t,u],i)=>[i?" · ":"", h("a",{href:u},t+" ↗")]))));
  }
}
async function toggleCl(fid){
  if (DT.cl[fid]){ delete DT.cl[fid]; renderDetail(); return; }
  DT.cl[fid] = "Loading…"; renderDetail();
  try{ const r = await post("/api/file_changelog",{mod_id:DT.data.id,file_id:fid}); DT.cl[fid] = r.text; }catch(e){ DT.cl[fid] = e.message; }
  renderDetail();
}
document.querySelectorAll(".stab").forEach(t=>t.onclick=()=>{ DT.tab=t.dataset.t; renderDetail(); $("#dBody").scrollTop=0; });
$("#shade").onclick = closeDetail;
$("#lightbox").onclick = ()=>$("#lightbox").classList.remove("show");

// ---------- Browse ----------
const B = {q:"", sort:"2", cat:"", results:[], total:0, next:null, loading:false, error:"", loaded:false, seq:0, catsLoaded:false, src:"cf", all:false, hidden:0};
const queue = [];
let sending = null;
async function bSearch(append){
  if (B.src === "wago") return wSearch();
  if (B.src === "auras") return aSearch(append);
  if (B.loading && append) return;
  const my = ++B.seq;
  B.loading = true; B.error = "";
  if (!append){ B.results = []; B.next = null; }
  renderBrowse();
  try{
    const r = await fetch("/api/search",{method:"POST",headers:{"X-WFAM":"1","Content-Type":"application/json"},
      body:JSON.stringify({q:B.q, sort:B.sort, category:B.cat, index: append ? B.next : 0, all:B.all})});
    const j = await r.json().catch(()=>({}));
    if (my !== B.seq) return;
    if (!r.ok) throw new Error(j.error || ("HTTP "+r.status));
    B.results = append ? B.results.concat(j.results) : j.results;
    B.hidden = (append ? B.hidden : 0) + (j.hidden || 0);
    B.total = j.total; B.next = j.next; B.loaded = true;
  }catch(e){ if (my === B.seq) B.error = e.message; }
  if (my === B.seq){ B.loading = false; renderBrowse(); }
}
async function loadCats(){
  if (B.catsLoaded) return;
  try{
    const r = await post("/api/categories",{});
    const sel = $("#bcat");
    for (const c of r.categories) sel.append(h("option",{value:c.id}, c.name));
    B.catsLoaded = true;
    if (B.cat){ sel.value = B.cat; if (sel.value !== B.cat) B.cat = ""; }
  }catch(e){}
}
function installFromBrowse(id){
  if (queue.includes(id) || sending === id) return;
  queue.push(id); pumpQueue(); renderBrowse(); if (DT.id) renderDetail();
}
async function pumpQueue(){
  if (sending !== null || !queue.length || !S || S.job.running) return;
  const id = queue.shift();
  sending = id;
  const r = B.results.find(x=>x.id===id) || {};
  try{ await post("/api/install_best",{id: typeof id === "number" ? id : null, wago_id: r.wago_id || null}); }
  catch(e){ toast(e.message); sending = null; }
  renderBrowse();
}
async function wSearch(){
  const my = ++B.seq;
  B.loading = true; B.error = ""; B.results = []; B.next = null; renderBrowse();
  if (B.q.length === 1){ B.loading = false; B.loaded = false; renderBrowse(); return; }
  try{ const r = await post("/api/wago_search",{q:B.q, all:B.all}); if (my !== B.seq) return; B.results = r.results; B.hidden = r.hidden || 0; B.total = r.results.length; B.loaded = true; }
  catch(e){ if (my === B.seq) B.error = e.message; }
  if (my === B.seq){ B.loading = false; renderBrowse(); }
}
// ---------- ElvUI & Tukui from tukui.org ----------
const TK = {results:[], loading:false, loaded:false, error:"", seq:0};
async function tSearch(){
  const my = ++TK.seq; TK.loading = true; TK.error = "";
  try{ const r = await post("/api/tukui_list",{}); if (my !== TK.seq) return; TK.results = r.results; TK.loaded = true; }
  catch(e){ if (my === TK.seq) TK.error = e.message; }
  if (my === TK.seq){ TK.loading = false; renderBrowse(); }
}
// ElvUI & Tukui aren't on CurseForge or Wago: they show up in the normal addon search when the name matches.
function tukuiMatches(){
  const q = (B.q||"").toLowerCase();
  if (q.length < 2 || B.cat || !TK.loaded) return [];
  return TK.results.filter(r => (r.name + " " + r.slug).toLowerCase().includes(q) || q.includes(r.slug));
}
function tukuiCard(r, busy){
  const managed = S && S.managed.find(a=>a.key===r.key);
  const installed = (managed && managed.folders.length) || r.installed;
  const btn = installed
    ? (managed && managed.status === "update" ? h("button",{class:"btn small primary",disabled:busy,onclick:()=>act("/api/update",{key:r.key})},"Update to " + r.version) : h("span",{class:"tag ok"},"Installed " + ((managed && managed.installed_version) || r.installed_version || "")))
    : h("button",{class:"btn small primary",disabled:busy,onclick:()=>act("/api/add_tukui",{slug:r.slug})},"Install");
  return h("div",{class:"res",title:r.summary}, logoEl(r.logo, r.name),
    h("div",{class:"body"}, h("div",{class:"t"}, r.name), h("div",{class:"by"}, r.author ? "by " + r.author : ""),
      h("div",{class:"sum"}, r.summary),
      h("div",{class:"meta"}, h("span",{}, "v" + r.version.replace(/^v/i,"")), r.date ? h("span",{}, fmtDate(r.date)) : null,
        h("a",{href:r.url,target:"_blank",rel:"noopener",title:"ElvUI and Tukui are only on tukui.org. Installed ones are kept up to date with your other addons."},"tukui.org ↗"),
        r.no_forever ? h("span",{class:"tag warn",title:"The last download had no WoW Forever version in it. Try again when a new version is out."},"no Forever version yet") : null,
        h("span",{class:"sp"}), btn)));
}
// ---------- WeakAuras (ForeverAuras) from wago.io ----------
const A = {cat:"", sort:"installs", results:[], next:null, total:0, page:0, loading:false, loaded:false, error:"", seq:0, catsLoaded:false};
async function aSearch(append){
  if (A.loading && append) return;
  const my = ++A.seq;
  A.loading = true; A.error = "";
  if (!append){ A.results = []; A.next = null; A.page = 0; }
  if (!A.catsLoaded) loadAuraCats();
  renderBrowse();
  try{
    const page = append ? A.next : 0;
    const r = await post("/api/aura_search",{q:B.q, category:A.cat, sort:A.sort, page});
    if (my !== A.seq) return;
    A.results = append ? A.results.concat(r.results) : r.results; A.next = r.next; A.total = r.total; A.loaded = true;
  }catch(e){ if (my === A.seq) A.error = e.message; }
  if (my === A.seq){ A.loading = false; renderBrowse(); }
}
async function loadAuraCats(){
  try{
    const r = await post("/api/aura_categories",{});
    const sel = $("#acat"); sel.replaceChildren(h("option",{value:""},"All categories"));
    for (const g of r.groups) sel.append(h("optgroup",{label:g.name}, ...g.items.map(c=>h("option",{value:c.id}, c.name))));
    sel.value = A.cat; A.catsLoaded = true;
  }catch(e){}
}
async function copyAura(ident, btn){
  const old = btn.textContent; btn.disabled = true; btn.textContent = "Copying…";
  try{
    const r = await post("/api/aura_code",{id:ident});
    try{ await navigator.clipboard.writeText(r.code); }catch(e){ const ta=h("textarea",{style:"position:fixed;opacity:0"}); ta.value=r.code; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
    toast("Import string copied — in game type /fa, click Import and paste.");
    btn.textContent = "Copied ✓"; setTimeout(()=>{ btn.textContent = old; btn.disabled = false; }, 2000); return;
  }catch(e){ toast(e.message); }
  btn.textContent = old; btn.disabled = false;
}
function renderAuras(){
  const box = $("#bres");
  const parts = [];
  if (S && S.config.addons_dir_ok && !S.foreverauras)
    parts.push(h("div",{class:"card",style:"padding:14px 16px;margin-bottom:12px"}, h("b",{},"ForeverAuras isn't installed. "),
      "Auras need the ForeverAuras addon (WoW Forever's WeakAuras). ", h("a",{href:"https://github.com/neroxrw/foreverauras/releases"},"Get ForeverAuras ↗")));
  if (A.error) parts.push(h("div",{class:"card"}, h("div",{class:"empty"}, h("b",{},"wago.io search failed"), h("br"), A.error)));
  else if (!A.results.length) parts.push(h("div",{class:"card"}, h("div",{class:"empty"}, A.loading ? "Loading ForeverAuras from wago.io…" : (A.loaded ? "No ForeverAuras match that." : ""))));
  else parts.push(h("div",{class:"grid"}, ...A.results.map(r=>
    h("div",{class:"res",title:r.summary},
      logoEl(r.thumb, r.name),
      h("div",{class:"body"}, h("div",{class:"t"}, r.name), h("div",{class:"by"}, r.author ? "by " + r.author : ""),
        h("div",{class:"sum"}, r.categories.length ? r.categories.join(" · ") + (r.summary ? " — " : "") : "", r.summary),
        h("div",{class:"meta"}, h("span",{}, "⬇ " + fmtNum(r.installs)), r.stars ? h("span",{}, "★ " + fmtNum(r.stars)) : null,
          r.date ? h("span",{}, fmtDate(new Date(r.date * (r.date < 1e12 ? 1000 : 1)).toISOString())) : null,
          h("a",{href:r.url,onclick:e=>e.stopPropagation()},"wago.io ↗"), h("span",{class:"sp"}),
          h("button",{class:"btn small primary",title:"Copies the import string — in game type /fa, click Import and paste",onclick:e=>{ e.stopPropagation(); copyAura(r.id, e.currentTarget); }},"Copy string")))))));
  box.replaceChildren(...parts);
  $("#srcHint").textContent = "ForeverAuras from wago.io. “Copy string”, then in game: /fa → Import → paste.";
  setMetaPlain(A.loaded && !A.error ? (A.total >= 10000 ? "10,000+" : A.total.toLocaleString()) + " ForeverAuras" + (B.q ? ` matching “${B.q}”` : "") + (A.cat ? " in " + $("#acat").selectedOptions[0].textContent : "") : "");
  $("#bmore").hidden = !(A.next !== null && !A.error);
  $("#bMoreBtn").disabled = A.loading; $("#bMoreBtn").textContent = A.loading ? "Loading…" : "Show more";
}
function setMetaPlain(t){ $("#bmeta").replaceChildren(t); }
const W = {cat:"", sort:"pop"};
function wagoCats(){
  const sel = $("#wcat"), names = [...new Set(B.results.flatMap(r=>r.categories||[]))].sort((a,b)=>a.localeCompare(b));
  if (W.cat && !names.includes(W.cat)) names.unshift(W.cat);
  const key = names.join("|");
  if (sel.dataset.k !== key){ sel.dataset.k = key; sel.replaceChildren(h("option",{value:""},"All categories"), ...names.map(n=>h("option",{value:n}, n))); }
  sel.value = W.cat; sel.hidden = !names.length;
}
function wagoShown(){
  let list = B.results.filter(r => !W.cat || (r.categories||[]).includes(W.cat));
  const by = {dl:(a,b)=>b.downloads-a.downloads, new:(a,b)=>String(b.updated||"").localeCompare(String(a.updated||"")), name:(a,b)=>a.name.localeCompare(b.name)}[W.sort];
  return by ? [...list].sort(by) : list;
}
function renderWago(box){
  if (B.error){ box.replaceChildren(h("div",{class:"card"}, h("div",{class:"empty"}, h("b",{},"Wago search failed"), h("br"), B.error))); return; }
  const shown = wagoShown();
  if (!shown.length){ box.replaceChildren(h("div",{class:"card"}, h("div",{class:"empty"}, B.loading ? (B.q ? "Searching Wago…" : "Loading Wago's popular addons…") : (B.q.length === 1 ? "Type one more letter…" : W.cat ? "Nothing in that category here." : "Nothing on Wago matches that.")))); return; }
  box.replaceChildren(h("div",{class:"grid"}, ...shown.map(r=>{
    const busyNow = S && S.job.running;
    const btn = r.installed ? h("span",{class:"tag ok"},"Installed ✓")
      : h("button",{class:"btn small primary",disabled:busyNow,title:"If CurseForge has it for WoW Forever too, it's installed from there and Wago is kept as a second source — whichever has the higher version number wins. Otherwise it's installed and updated from Wago.",onclick:e=>{ e.stopPropagation(); act("/api/add_wago",{id:r.id}); }},"Install");
    return h("div",{class:"res",title:r.summary},
      logoEl(r.logo, r.name),
      h("div",{class:"body"}, h("div",{class:"t"}, r.name), h("div",{class:"by"}, r.authors.length ? "by "+r.authors.join(", ") : ""),
        h("div",{class:"sum"}, r.summary),
        h("div",{class:"meta"}, r.downloads ? h("span",{}, "⬇ "+fmtNum(r.downloads)) : null, r.updated ? h("span",{}, fmtDate(r.updated)) : null, h("a",{href:r.url,onclick:e=>e.stopPropagation()},"Wago ↗"), noForever(r), h("span",{class:"sp"}), btn)));
  })));
}
const SRC_NAME = {curseforge:"CurseForge", wago:"Wago", github:"GitHub", tukui:"tukui.org"};
// Newest version across CurseForge, Wago (and GitHub when the addon links one), and where it comes from.
function verLine(r){
  if (!r.version) return null;
  const others = (r.sources||[]).filter(x=>x !== r.version_from && x !== "github").map(x=>{
    const v = x === "curseforge" ? r.cf_version : x === "wago" ? r.wago_version : null;
    return v && v !== r.version ? SRC_NAME[x] + ": " + v : null; }).filter(Boolean);
  return h("div",{class:"ver",title:"Sources: " + (r.sources||[]).map(x=>SRC_NAME[x]||x).join(", ") + (others.length ? "\n" + others.join("\n") : "")},
    h("b",{}, r.version), h("span",{class:"dim"}, " · " + (SRC_NAME[r.version_from] || r.version_from)),
    (r.sources||[]).length > 1 ? h("span",{class:"dim"}, "  (also on " + (r.sources||[]).filter(x=>x !== r.version_from).map(x=>SRC_NAME[x]||x).join(", ") + ")") : null);
}
// "LayerSwap-1.14.36" -> "1.14.36": the addon's name in front of the version only takes room.
// First start: everything needed right on the page — paste a key (CurseForge or Wago, it tells
// which), and the AddOns folder is usually found by itself.
function setupCard(c){
  const out = h("div",{class:"help",style:"min-height:18px;margin-top:6px"});
  const inp = h("input",{type:"password",placeholder:"Paste your CurseForge API key here (Ctrl+V)",autocomplete:"off",spellcheck:"false",style:"flex:1;min-width:0"});
  const save = async ()=>{ const k = inp.value.trim(); if (!k) return;
    out.style.color = "var(--muted)"; out.textContent = "Checking the key…";
    try{ const r = await post("/api/save_key",{key:k}); out.style.color = r.ok ? "var(--green)" : "var(--red)"; out.textContent = r.message; if (r.ok){ inp.value = ""; refresh(); } }
    catch(e){ out.style.color = "var(--red)"; out.textContent = e.message; } };
  inp.addEventListener("paste", ()=>setTimeout(save, 50));
  inp.addEventListener("keydown", e=>{ if (e.key === "Enter") save(); });
  const pasteBtn = h("button",{class:"btn",onclick:async ()=>{ try{ inp.value = (await navigator.clipboard.readText()).trim(); save(); }catch(e){ inp.focus(); out.textContent = "Click the box and press Ctrl+V."; } }},"Paste");
  const steps = h("ol",{style:"text-align:left;margin:10px auto 0;max-width:560px;color:var(--muted);font-size:13px;line-height:1.6"},
    h("li",{}, "Open ", h("a",{href:"https://console.curseforge.com/"},"console.curseforge.com ↗"), " and sign in (free)."),
    h("li",{}, "Go to ", h("b",{},"API keys"), " and copy your key."),
    h("li",{}, "Paste it above — it's checked and saved by itself."));
  return h("div",{class:"empty",style:"padding:26px 22px"},
    h("b",{style:"font-size:16px"}, c.has_key ? "Pick your WoW Forever AddOns folder" : "One step left: your CurseForge key"),
    c.has_key ? h("div",{style:"margin-top:8px"}, "The folder wasn't found automatically. ", h("button",{class:"btn primary",onclick:()=>openSettings()},"Choose folder"))
      : h("div",{}, h("div",{class:"row setupkey",style:"display:flex;gap:8px;max-width:560px;margin:14px auto 0"}, inp, pasteBtn), out, steps,
          h("div",{class:"dim",style:"font-size:12px;margin-top:10px"}, "Each person uses their own free key — it's stored only on this PC. A Wago key (optional) can be pasted here too.")));
}
// "Attune-Forever-v16001-44.zip" → "v16001-44", "Books_on_map_Foreverv2.3.2.zip" → "v2.3.2" (full text on hover)
function shortVer(v){
  v = String(v||"").replace(/\.(zip|rar|7z)$/i, "");
  const m = v.match(/^[A-Za-z][A-Za-z0-9 '_.-]*?(?:[-_ ]+(?=v?\d)|(?<=[a-z])(?=v\d))/);
  return m && m[0].length && m[0].length < v.length ? v.slice(m[0].length) : v;
}
const narrow = () => matchMedia("(max-width:1050px)").matches;
function statusCell(a, label, cls){
  const upd = a.update_version && (a.status==="update"||a.status==="update_manual");
  const text = upd ? "Update → " + shortVer(a.update_version) : label;
  const from = upd && a.update_from ? "from " + (SRC_NAME[a.update_from]||a.update_from) : (a.tukui ? "tukui.org" : "");
  const tip = upd ? "Update to " + a.update_version + (a.update_from ? " from " + (SRC_NAME[a.update_from]||a.update_from) : "") : label;
  return [h("span",{class:"pill p-"+cls,title:tip}, h("span",{class:"tx"}, text)), from ? h("div",{class:"from" + (upd ? "" : " nar")}, from) : null];
}
function noForever(r){
  return r.forever === false ? h("span",{class:"tag warn",title:"No WoW Forever build is listed for this addon — it was probably made for another version of WoW and may not work."},"no Forever version") : null;
}
function setMeta(text){
  const el = $("#bmeta"); el.replaceChildren(text);
  if (!text) return;
  const flip = (label)=>h("button",{class:"linkbtn",onclick:()=>{ B.all = !B.all; bSearch(false); }}, label);
  if (B.all) el.append(" · ", flip("Only WoW Forever addons"));
  else if (B.hidden) el.append(` · ${B.hidden} for other WoW versions hidden · `, flip("Show them"));
}
function renderBrowse(){
  if ($("#vBrowse").hidden) return;
  if (B.src !== "auras") B.src = "cf";
  $("#vTitle").textContent = B.src === "auras" ? "WeakAuras for WoW Forever" : "Browse addons";
  $("#bcat").hidden = $("#bsort").hidden = B.src !== "cf";
  $("#acat").hidden = $("#asort").hidden = B.src !== "auras";
  $("#bq").placeholder = B.src === "auras" ? "Search ForeverAuras on wago.io — class, cooldowns, raid, rotation…" : "Search WoW Forever addons — bags, quests, bosses, ElvUI…";
  if (B.src === "auras"){ $("#wsort").hidden = $("#wcat").hidden = true; renderAuras(); $("#srcBar").hidden = !$("#srcHint").textContent; return; }
  $("#wsort").hidden = B.src !== "wago";
  if (B.src === "wago") wagoCats(); else $("#wcat").hidden = true;
  $("#srcHint").textContent = ""; $("#srcBar").hidden = true;
  const tk = tukuiMatches(), busyNow = !!(S && S.job.running);
  if (B.src === "wago"){ renderWago($("#bres")); const n = wagoShown().length; setMeta(B.loaded && !B.error ? n + " addon" + (n===1?"":"s") + (B.q ? ` on Wago matching “${B.q}”` : " — popular on Wago") + (W.cat ? " in " + W.cat : "") : ""); $("#bmore").hidden = true; return; }
  const box = $("#bres");
  const managed = new Map((S ? S.managed : []).map(a=>[String(a.key), a]));
  if (S && (!S.config.has_key || !S.config.addons_dir_ok)){
    box.replaceChildren(h("div",{class:"card"}, h("div",{class:"empty"}, h("b",{},"Finish setup first"), h("div",{class:"acts2"}, h("button",{class:"btn primary",onclick:()=>openSettings()},"Open settings")))));
    $("#bmeta").textContent=""; $("#bmore").hidden=true; return;
  }
  if (B.error){ box.replaceChildren(h("div",{class:"card"}, h("div",{class:"empty"}, h("b",{},"Search failed"), h("br"), B.error))); }
  else if (!B.results.length && tk.length){ box.replaceChildren(h("div",{class:"grid"}, ...tk.map(r=>tukuiCard(r, busyNow)))); }
  else if (!B.results.length){
    box.replaceChildren(h("div",{class:"card"}, h("div",{class:"empty"}, B.loading ? "Searching CurseForge…" : (B.loaded ? "No WoW Forever addons match that search." : ""))));
  } else {
    box.replaceChildren(h("div",{class:"grid"}, ...tk.map(r=>tukuiCard(r, busyNow)), ...B.results.map(r=>{
      const a = managed.get(String(r.id)) || (r.wago_id ? managed.get("wago-" + r.wago_id) : null);
      let btn;
      if (a && a.folders.length) btn = h("span",{class:"tag ok"}, a.status==="update" ? "Installed · update ready" : "Installed ✓");
      else if (a && a.status==="awaiting") btn = h("span",{class:"tag warn",title:"Finish the download on CurseForge's page — it installs by itself"},"Waiting for download…");
      else if (sending===r.id) btn = h("button",{class:"btn small",disabled:true},"Installing…");
      else if (queue.includes(r.id)) btn = h("button",{class:"btn small",disabled:true},"Queued");
      else btn = h("button",{class:"btn small primary",onclick:e=>{ e.stopPropagation(); installFromBrowse(r.id); }},"Install");
      const via = r.manual_only ? (S && ((r.github && S.config.use_github) || (S.config.has_wago_key && S.config.use_wago))
          ? h("span",{class:"tag ok",title:"The author only allows downloads on CurseForge's own site, but publishes releases elsewhere ("+(r.github||"Wago")+"). If a WoW Forever build is there, it is installed from there; otherwise CurseForge's download page opens."}, S.config.has_wago_key && S.config.use_wago ? "tries Wago & GitHub first" : "tries GitHub first")
          : h("span",{class:"tag",title:"The author only allows downloads on CurseForge's own site. Install opens that page and the addon installs by itself when the download finishes."},"downloads via CurseForge site")) : null;
      const card = h("div",{class:"res",title:r.summary},
        logoEl(r.logo, r.name),
        h("div",{class:"body"},
          h("div",{class:"t"}, r.name),
          h("div",{class:"by"}, r.authors.length ? "by "+r.authors.join(", ") : ""),
          h("div",{class:"sum"}, r.summary),
          verLine(r),
          h("div",{class:"meta"}, r.downloads ? h("span",{}, "⬇ "+fmtNum(r.downloads)) : null, (r.version_date || r.updated) ? h("span",{}, fmtDate(r.version_date || r.updated)) : null, via, noForever(r), h("span",{class:"sp"}), btn)));
      card.addEventListener("click", e=>{ if (!e.target.closest("button,a") && typeof r.id === "number") openDetail(r.id); });
      return card;
    })));
  }
  setMeta(B.loaded && !B.error ? (B.all ? (B.total >= 10000 ? "10,000+ addons" : `${B.total.toLocaleString()} addon${B.total===1?"":"s"}`) + " on CurseForge, all game versions"
                                         : "WoW Forever addons") + (B.q ? ` matching “${B.q}”` : "") + (tk.length ? ` · ${tk.length} from tukui.org` : "") : "");
  $("#bmore").hidden = !(B.next !== null && !B.error);
  $("#bMoreBtn").disabled = B.loading; $("#bMoreBtn").textContent = B.loading ? "Loading…" : "Show more";
}

// ---------- backups / changelog / diagnostics ----------
// a small dot on Backups when another PC's setup is newer — no popup
function renderSyncDot(){
  const tab = document.querySelector(".tab[data-v='backups']"); if (!tab) return;
  const want = !!S.sync_offer, has = tab.querySelector(".newdot");
  if (want && !has) tab.append(h("span",{class:"newdot",title:"A newer setup from another PC is in your sync folder"}));
  if (!want && has) has.remove();
}
function renderBackups(){
  renderSyncDot();
  if ($("#vBackups").hidden) return;
  const busy = S.job.running;
  if (document.activeElement !== $("#fSync") && $("#fSync").dataset.v !== S.config.sync_dir){ $("#fSync").value = S.config.sync_dir; $("#fSync").dataset.v = S.config.sync_dir; }
  $("#bSetupExport").disabled = $("#bSetupRestore").disabled = busy;
  $("#bSetupExport").textContent = S.config.sync_dir_ok ? "Save to sync folder now" : "Export everything";
  $("#syncStatus").textContent = !S.config.sync_dir ? "" : !S.config.sync_dir_ok ? "⚠ The sync folder can't be found right now."
    : S.sync_last ? "This PC last saved its setup there " + ago(S.sync_last) + "." : "This PC saves its setup there the next time something changes.";
  const o = S.sync_offer, ob = $("#syncOffer");
  if (ob.dataset.k !== JSON.stringify(o) + busy){
    ob.dataset.k = JSON.stringify(o) + busy;
    ob.replaceChildren(...(o ? [h("div",{class:"syncoffer"}, h("span",{style:"flex:1"}, h("b",{}, o.computer), " saved a newer setup " + ago(o.created) + (o.addons ? ` (${o.addons} addons)` : "") + "."),
      h("button",{class:"btn small primary",disabled:busy,onclick:async ()=>{ if (await ask("Restore the setup from " + o.computer + "?", "Copies its addon settings, keybinds and macros here and installs its addons. Nothing is removed, and your current settings are backed up first. WoW must be closed.", "Restore")) act("/api/setup_restore",{file:o.file}); }},"Restore"),
      h("button",{class:"btn small ghost",onclick:()=>act("/api/sync_dismiss",{computer:o.computer,created:o.created})},"Not now"))] : []));
  }
  $("#bBackupNow").disabled = busy || !S.wtf_found;
  const box = $("#bkList");
  if (!S.wtf_found){ box.replaceChildren(h("div",{class:"empty"},"No WTF folder yet — it appears after you've logged in to WoW Forever once.")); return; }
  if (!S.backups.length){ box.replaceChildren(h("div",{class:"empty"},"No backups yet. One is made automatically the next time addons change, or click “Back up now”.")); return; }
  box.replaceChildren(...S.backups.map((b,i)=>h("div",{class:"bk"},
    h("div",{class:"n"}, new Date(b.date).toLocaleString(undefined,{dateStyle:"medium",timeStyle:"short"}), i===0 ? h("span",{class:"tag ok",style:"margin-left:10px"},"newest") : null),
    h("span",{class:"dim"}, b.size),
    h("button",{class:"btn small",disabled:busy,onclick:async ()=>{ if (await ask("Restore addon settings?", `Replaces your current WTF folder with the copy from ${new Date(b.date).toLocaleString()}. WoW must be closed. Your current settings are backed up first, so this can be undone.`, "Restore")) act("/api/restore",{name:b.name}); }},"Restore"))));
}
async function showChangelog(key){
  $("#clT").textContent = "What's new"; $("#clB").textContent = "Loading…"; $("#clU").removeAttribute("href"); $("#clU").hidden = false; $("#clAct").replaceChildren();
  $("#mLog").classList.add("show");
  try{ const r = await post("/api/changelog",{key}); $("#clT").textContent = r.title; $("#clB").textContent = r.text; if (r.url) $("#clU").href = r.url; }
  catch(e){ $("#clB").textContent = e.message; }
}
// "Report ↗": the addon's bug tracker. GitHub gets a new issue with the errors filled in; anywhere else
// the errors are copied first so they can be pasted.
function reportLink(a, text){
  const target = a.issues_url || a.url; if (!target) return null;
  const body = "**Addon:** " + a.name + " " + (a.installed_version||"") + "\n**Game:** WoW Forever (interface " + (S.config.interface||"?") + ")\n\n```\n" + text.slice(0, 4000) + "\n```\n\n_Reported from BugGrabber via WoW Forever Addons_";
  const gh = target.match(/^https:\/\/github\.com\/([^/]+\/[^/#?]+)/i);
  const href = gh ? `https://github.com/${gh[1]}/issues/new?title=${encodeURIComponent("Lua error: " + (a.errors ? a.errors.message : "").slice(0,120))}&body=${encodeURIComponent(body)}` : target;
  return h("a",{class:"btn",href,target:"_blank",rel:"noopener",title: gh ? "Opens a new GitHub issue with the errors filled in" : "Copies the errors and opens the addon's page for bug reports — paste them there",
    onclick:()=>{ if (!gh) navigator.clipboard.writeText(body).then(()=>toast("Errors copied — paste them in the report.")).catch(()=>{}); }}, "Report ↗");
}
// Lua errors BugGrabber saved for one addon, with what you can do about them
async function showErrors(key){
  const a = S.managed.find(x=>x.key===key); if (!a) return;
  $("#clT").textContent = a.name + " — Lua errors"; $("#clU").hidden = true; $("#clB").textContent = "Loading…";
  $("#mLog").classList.add("show");
  let r; try{ r = await post("/api/errors",{key}); } catch(e){ $("#clB").textContent = e.message; return; }
  const when = t => t ? new Date(t*1000).toLocaleString(undefined,{day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"}) : "";
  const text = r.errors.map(e=>`${e.count}× · ${when(e.time)}\n${e.message}${e.stack ? "\n" + e.stack.split("\n").slice(0,4).join("\n") : ""}`).join("\n\n");
  $("#clB").replaceChildren(h("p",{class:"sub",style:"margin:0 0 10px"}, "From BugGrabber, last 14 days. " + (a.errors && a.errors.since_update ? "These started after the update to " + shortVer(a.installed_version) + "." : "")),
    ...(r.errors.length ? r.errors.map(e=>h("div",{class:"errbox"}, h("div",{class:"eh"}, e.count + "× · " + when(e.time)), e.message + (e.stack ? "\n\n" + e.stack.split("\n").slice(0,6).join("\n") : ""))) : [h("div",{class:"muted"},"No errors left.")]));
  const busy = !!S.job.running, close = ()=>$("#mLog").classList.remove("show");
  $("#clAct").replaceChildren(
    a.status==="update" ? h("button",{class:"btn primary",disabled:busy,onclick:()=>{ close(); act("/api/update",{key}); }},"Update to " + shortVer(a.latest_version)) : null,
    a.previous_version ? h("button",{class:"btn" + (a.errors && a.errors.since_update && a.status!=="update" ? " primary" : ""),disabled:busy,title:"Goes back to the version before and pins it",onclick:async ()=>{ close(); await post("/api/errors_ignore",{key}).catch(()=>{}); act("/api/revert",{key}); }},"Revert to " + shortVer(a.previous_version)) : null,
    h("button",{class:"btn",onclick:()=>{ navigator.clipboard.writeText(a.name + " " + (a.installed_version||"") + "\n\n" + text).then(()=>toast("Copied.")).catch(()=>{}); }},"Copy"),
    reportLink(a, text),
    h("button",{class:"btn ghost",title:"Hide these until new ones show up",onclick:async ()=>{ close(); await post("/api/errors_ignore",{key}); refresh(true); }},"Ignore"));
}
const DGI = {ok:"✔", warn:"!", fail:"✖", info:"i"};
function renderDiag(){
  if (!$("#mDiag").classList.contains("show")) return;
  const d = S.diag, box = $("#dgBody");
  if (!d){ box.replaceChildren(h("div",{class:"muted"},"Starting…")); return; }
  const rows = d.results.map(r=>h("div",{class:"dg"}, h("span",{class:"i "+r.status}, DGI[r.status]||"·"), h("span",{class:"tt"}, r.title), h("span",{class:"dd"}, r.detail)));
  if (d.running) rows.push(h("div",{class:"dg"}, h("span",{class:"spin"}), h("span",{class:"dd"},"Running…")));
  box.replaceChildren(...rows);
  $("#dgRun").disabled = S.job.running;
}
function diagText(){
  const d = S.diag; if (!d) return "";
  return "WoW Forever Addon Manager — diagnostics " + d.at + "\n" + d.results.map(r=>`[${r.status.toUpperCase()}] ${r.title}: ${r.detail}`).join("\n");
}

// ---------- characters ----------
let CH = null, chEdits = {};   // chEdits["i|folder"] = true/false
async function loadChars(){
  $("#chBox").replaceChildren(h("div",{class:"empty"},"Reading your WTF folder…"));
  try{ CH = await (await fetch("/api/characters")).json(); }catch(e){ CH = null; }
  chEdits = {};
  renderChars();
}
const chKey = (i, folder) => i + "|" + folder;
function chState(a, i){ const k = chKey(i, a.folder); return k in chEdits ? chEdits[k] : a.on[i]; }
function renderChars(){
  const box = $("#chBox"), n = Object.keys(chEdits).length;
  $("#chSave").hidden = $("#chDiscard").hidden = !n;
  $("#chSave").textContent = n ? `Save changes (${n})` : "Save changes";
  $("#chSave").disabled = !!(S && S.job.running);
  renderProfiles();
  if (!CH){ box.replaceChildren(h("div",{class:"empty"},"Couldn't read the WTF folder.")); return; }
  if (!CH.wtf_found || !CH.characters.length){ box.replaceChildren(h("div",{class:"empty"},"No characters found yet — they appear after you've logged in to them once.")); return; }
  const all = $("#chAll").checked;
  const rows = chRows(all);
  const head = h("tr",{}, h("th",{},"Addon"), ...CH.characters.map((c,i)=>h("th",{title:c.account+" · "+c.realm + (c.has_file?"":" (no AddOns.txt yet)")},
    h("span",{class:"cn"}, c.name), h("small",{}, c.realm), h("button",{class:"linkbtn",style:"font-size:11px;margin-top:2px",onclick:e=>openMenu(e.currentTarget, charMenu(i))},"options ▾"))));
  const body = rows.map(a=>h("tr",{}, h("td",{}, h("div",{style:"font-weight:600"}, a.title), h("div",{class:"dim",style:"font-size:12px"}, a.folder + (a.default==="disabled" ? " · off by default" : ""))),
    ...a.states.map((st,i)=>{
      const on = chState(a, i), changed = chKey(i,a.folder) in chEdits;
      const txt = changed ? (on?"✓":"✗") : (st==="enabled" ? "✓" : st==="disabled" ? "✗" : "○");
      return h("td",{class:"cell " + (changed||st ? (on?"on":"off") : "def") + (changed?" chg":""), title:(on?"on":"off")+" — click to switch", onclick:()=>{ const k=chKey(i,a.folder); const v=!on; if (v===a.on[i]) delete chEdits[k]; else chEdits[k]=v; renderChars(); }}, txt);
    })));
  const counts = h("tr",{}, h("td",{class:"dim"},"Switched on"), ...CH.characters.map((c,i)=>h("td",{class:"dim"}, String(rows.filter(a=>chState(a,i)).length)+"/"+rows.length)));
  box.replaceChildren(h("div",{class:"mxwrap"}, h("table",{class:"mx"}, h("thead",{}, head), h("tbody",{}, ...body, counts))));
}
function chRows(all){ return CH.addons.filter(a => all || a.addon === null || a.addon === undefined || !CH.addons.some(b => b !== a && b.addon && b.addon === a.addon && b.folder.length < a.folder.length)); }
function setChar(i, fn){ for (const a of CH.addons){ const v = fn(a); if (v===undefined) continue; const k = chKey(i,a.folder); if (v===a.on[i]) delete chEdits[k]; else chEdits[k]=v; } renderChars(); }
function charMenu(i){
  const others = CH.characters.map((c,j)=>j).filter(j=>j!==i);
  const profs = Object.keys((S && S.config.addon_profiles) || {});
  return [
    {head: CH.characters[i].name},
    {label:"Switch everything on", run:()=>setChar(i, ()=>true)},
    {label:"Switch everything off", run:()=>setChar(i, ()=>false)},
    others.length ? "-" : null,
    ...others.map(j=>({label:"Copy from "+CH.characters[j].name+" ("+CH.characters[j].realm+")", run:()=>setChar(i, a=>chState(a,j))})),
    profs.length ? "-" : null,
    ...profs.map(p=>({label:"Use profile “"+p+"”", run:()=>{ const st = S.config.addon_profiles[p]; setChar(i, a=> a.folder.toLowerCase() in st ? st[a.folder.toLowerCase()] : undefined); }})),
    "-",
    {label:"Save as profile…", run:async ()=>{ const name = await ask("Save as profile", "Saves which addons "+CH.characters[i].name+" has switched on, so you can use the same set on other characters.", "Save", ""); if (!name) return; const st = {}; for (const a of CH.addons) st[a.folder] = chState(a,i); act("/api/profile",{name, states:st}); }},
  ];
}
function renderProfiles(){
  const box = $("#profBox"), profs = Object.keys((S && S.config.addon_profiles) || {});
  if (!profs.length){ box.replaceChildren(h("div",{class:"dim",style:"font-size:12.5px"},"Tip: “options ▾” under a character saves its addon set as a profile, copies another character's set, or switches everything on or off.")); return; }
  box.replaceChildren(h("h2",{style:"margin-top:8px"},"Profiles"), h("div",{class:"card"}, ...profs.map(p=>{ const st = S.config.addon_profiles[p], n = Object.values(st).filter(Boolean).length;
    return h("div",{class:"bk"}, h("div",{class:"n"}, h("b",{},p), h("span",{class:"dim",style:"margin-left:8px;font-size:12px"}, n+" addons on")), h("button",{class:"btn small ghost danger",onclick:async ()=>{ if (await ask(`Delete profile “${p}”?`, "Characters keep their current addons.", "Delete")) act("/api/profile",{name:p, states:null}); }},"Delete")); })));
}
$("#chSave").onclick = async ()=>{
  const by = {};
  for (const [k,v] of Object.entries(chEdits)){ const [i, folder] = [k.slice(0,k.indexOf("|")), k.slice(k.indexOf("|")+1)]; (by[i] = by[i] || {})[folder] = v; }
  const changes = Object.entries(by).map(([i, states])=>Object.assign({}, CH.characters[+i], {states}));
  try{ await post("/api/characters_save",{changes}); chEdits = {}; setTimeout(loadChars, 900); }catch(e){ toast(e.message); }
};
$("#chDiscard").onclick = ()=>{ chEdits = {}; renderChars(); };

// ---------- remember where the user was (view, sorting, filters) ----------
let uiRestored = false, uiTimer = null;
function saveUi(){
  if (!uiRestored) return;
  clearTimeout(uiTimer);
  uiTimer = setTimeout(()=>post("/api/ui_state",{state:{view, filter, msrc:mSrc, msort:MS.by, mdir:MS.dir, bsort:B.sort, bcat:B.cat, asort:A.sort, acat:A.cat}}).catch(()=>{}), 600);
}
function restoreUi(u){
  const okSel = (sel, v) => [...$(sel).options].some(o=>o.value===String(v));
  if (["status","name","installed","latest"].includes(u.msort)){ MS.by = u.msort; MS.dir = +u.mdir === -1 ? -1 : 1; }
  if (["curseforge","wago","github","tukui"].includes(u.msrc)) mSrc = u.msrc;
  if (["all","updates","problems","unmanaged","ui","errors","unused"].includes(u.filter)){ filter = u.filter; document.querySelectorAll(".stat").forEach(e=>e.classList.toggle("on", e.dataset.f===filter)); }
  if (u.bsort && okSel("#bsort", u.bsort)){ B.sort = String(u.bsort); $("#bsort").value = B.sort; }
  if (u.bcat) B.cat = String(u.bcat);            // the category lists load later and pick these up
  if (u.asort && okSel("#asort", u.asort)){ A.sort = String(u.asort); $("#asort").value = A.sort; }
  if (u.acat) A.cat = String(u.acat);
  if (["browse","auras","chars","backups"].includes(u.view) && S.config.has_key && S.config.addons_dir_ok) showView(u.view);
  uiRestored = true;
}

// ---------- navigation ----------
const TITLES = {installed:"My addons", browse:"Browse addons", auras:"WeakAuras for WoW Forever", chars:"Characters", backups:"Backups"};
const BQ = {cf:"", auras:""};   // each search keeps its own text
function showView(v){
  if (v==="settings"){ openSettings(); return; }
  closeSettings(true);
  view = v; closeMenu();
  document.querySelectorAll(".side .tab").forEach(t=>t.classList.toggle("on", t.dataset.v===v));
  $("#vTitle").textContent = TITLES[v]; $("#vSub").textContent = "";
  $("#vInstalled").hidden = v!=="installed"; $("#vBrowse").hidden = v!=="browse" && v!=="auras"; $("#vBackups").hidden = v!=="backups"; $("#vChars").hidden = v!=="chars";
  $("#scroll").scrollTop = 0;
  if (v==="chars") loadChars();
  if (v==="backups") renderBackups();
  if (v==="browse" || v==="auras"){
    const src = v==="auras" ? "auras" : "cf";
    if (B.src !== src){ BQ[B.src] = B.q; B.src = src; B.q = BQ[src] || ""; $("#bq").value = B.q; }
    if (src==="cf"){ loadCats(); if (!TK.loaded && !TK.loading) tSearch(); }
    const st = src==="auras" ? A : B;
    if (!st.loaded && !st.loading) bSearch(false); else renderBrowse();
    setTimeout(()=>$("#bq").focus(),0);
  }
  saveUi();
  if (S) render();
}
function setDrawer(open){ drawerOpen=open; $("#drawer").classList.toggle("open",open); $("#dTog").textContent = open?"Hide activity ▼":"Show activity ▲"; if (open){ const lb=$("#logbox"); lb.scrollTop=lb.scrollHeight; } }

let timer=null;
async function refresh(soon){
  clearTimeout(timer);
  if (soon){ timer=setTimeout(refresh,250); return; }
  try{
    const r = await fetch("/api/state?since="+logSeq+"&etag="+(S && S.etag || ""));
    const j = await r.json();
    if (!j.same){ S = j; render(); if (view==="chars") renderChars(); }
    // First start: the key can be pasted right on My addons; Settings opens only if the folder is missing.
    if (firstLoad){ firstLoad=false; restoreUi(S.config.ui_state || {}); if (!S.config.addons_dir_ok) openSettings(true); }
  }catch(e){ $("#dLast").textContent = "Lost connection to the manager — is it still running?"; }
  timer = setTimeout(refresh, S && S.warming ? 300 : S && S.job.running ? 900 : (document.hidden ? 30000 : 5000));
}
document.addEventListener("visibilitychange", ()=>{ if (!document.hidden) refresh(); });

// ---------- settings (a page of its own) ----------
function openSettings(setup){
  const c = S.config;
  closeDetail(); closeMenu();
  document.querySelectorAll(".side .tab").forEach(t=>t.classList.toggle("on", t.dataset.v==="settings"));
  $("#setupSteps").hidden = !(setup===true);
  $("#sTitle").textContent = setup===true ? "Welcome — quick setup" : "Settings";
  $("#keyResult").style.display="none"; $("#wagoResult").style.display="none";
  $("#fWago").value = ""; $("#fWago").placeholder = c.has_wago_key ? "Saved" + (c.wago_key_hint?" (ends in "+c.wago_key_hint+")":"") + " — leave empty to keep" : "Paste your Wago key";
  $("#fUseWago").checked = c.use_wago !== false;
  $("#fKey").value = ""; $("#fKey").placeholder = c.has_key ? "Saved" + (c.key_hint?" (ends in "+c.key_hint+")":"") + " — leave empty to keep" : "Paste your API key";
  $("#fDir").value = c.addons_dir || "";
  const sel = $("#fType");
  if (c.version_type_id && ![...sel.options].some(o=>o.value==String(c.version_type_id))) sel.append(h("option",{value:c.version_type_id}, (c.version_type_name||"id "+c.version_type_id)));
  sel.value = c.version_type_id ? String(c.version_type_id) : "auto";
  $("#fChan").value = c.channel || "release";
  $("#fIf").value = c.interface_auto ? "" : (c.interface || "");
  $("#fIf").placeholder = c.interface_detected ? "Automatic — " + c.interface_detected : "Automatic — " + (c.interface || "16001");
  $("#fIfHelp").textContent = c.interface_detected
    ? "Read from the game itself, so it follows WoW Forever patches. Type a number only to override it."
    : "Read from the game once you've started WoW Forever. Until then " + (c.interface || "16001") + " is used.";
  $("#fDeps").checked = c.auto_dependencies !== false;
  $("#fAutoMan").checked = c.auto_manual !== false;
  $("#fDel").checked = c.delete_after_install !== false;
  $("#fGh").checked = c.use_github !== false;
  $("#fBk").checked = c.backup_wtf !== false;
  $("#fWin").checked = c.app_window !== false; $("#fBkN").value = c.backup_keep || 3;
  $("#hostBox").hidden = !c.hosted; $("#winBox").hidden = !!c.hosted;
  $("#fTray").checked = c.tray !== false; $("#fBgH").value = String(c.bg_check_hours ?? 3);
  $("#fBgUp").checked = c.bg_auto_update !== false; $("#fAuto").checked = !!c.autostart;
  $("#fScale").value = c.ui_scale || "100";
  $("#fDl").value = c.downloads_dir || ""; $("#fDl").placeholder = c.downloads_default || "";
  $("#fBkDir").value = c.backup_dir || ""; $("#fBkDir").placeholder = c.backup_dir_default || "";
  $("#selfRepo").replaceChildren(...(c.update_repo ? ["Program updates come from ", h("a",{href:"https://github.com/"+c.update_repo}, "github.com/"+c.update_repo), " (checked every 6 hours and when you click Check for updates). ",
    h("button",{class:"linkbtn",onclick:async ()=>{ try{ const r = await post("/api/check_self_update",{}); toast(r.update ? "A new version is ready — see the menu." : "You have the newest version."); }catch(e){ toast(e.message); } }},"Check now")] : []));
  $("#mSettings").classList.add("show"); $("#mSettings").scrollTop = 0;
  if (setup===true && !c.addons_dir) detect();
}
function closeSettings(silent){
  if (!$("#mSettings").classList.contains("show")) return;
  $("#mSettings").classList.remove("show");
  if (!silent) document.querySelectorAll(".side .tab").forEach(t=>t.classList.toggle("on", t.dataset.v===view));
}
async function detect(){
  const box = $("#dirChips"); box.replaceChildren(h("span",{class:"dim",style:"font-size:12px"},"Searching…"));
  try{
    const r = await post("/api/detect",{});
    box.replaceChildren();
    if (!r.dirs.length){ box.append(h("span",{class:"dim",style:"font-size:12px"},"No World of Warcraft folder found automatically — paste the path.")); return; }
    for (const d of r.dirs) box.append(h("button",{class:"chip",onclick:()=>{$("#fDir").value=d.path;}}, h("b",{},d.flavor), " ", d.path, d.forever?"":" (not Forever)"));
    const best = r.dirs.find(d=>d.flavor==="_forever_");
    if (best && !$("#fDir").value) $("#fDir").value = best.path;
  }catch(e){ box.replaceChildren(h("span",{class:"dim"},e.message)); }
}
async function loadTypes(){
  const key = $("#fKey").value.trim();
  try{
    if (key) await post("/api/settings",{api_key:key});
    const r = await post("/api/version_types",{});
    const sel = $("#fType"), cur = sel.value;
    sel.replaceChildren(h("option",{value:"auto"},"Automatic (WoW Forever)"), ...r.types.map(t=>h("option",{value:t.id}, t.name+"  (id "+t.id+")")));
    sel.value = [...sel.options].some(o=>o.value===cur) ? cur : "auto";
  }catch(e){ toast(e.message); }
}
async function saveSettings(){
  const sel = $("#fType");
  const body = {addons_dir:$("#fDir").value, channel:$("#fChan").value, interface:$("#fIf").value,
    version_type_id: sel.value==="auto" ? null : sel.value, version_type_name: sel.value==="auto" ? "" : sel.options[sel.selectedIndex].text.replace(/\s+\(id \d+\)$/,""),
    auto_dependencies:$("#fDeps").checked, auto_manual:$("#fAutoMan").checked,
    delete_after_install:$("#fDel").checked, downloads_dir:$("#fDl").value, use_github:$("#fGh").checked,
    backup_wtf:$("#fBk").checked, backup_keep:$("#fBkN").value, backup_dir:$("#fBkDir").value,
    use_wago:$("#fUseWago").checked, app_window:$("#fWin").checked};
  if (S.config && S.config.hosted) Object.assign(body, {tray:$("#fTray").checked, bg_check_hours:+$("#fBgH").value,
    bg_auto_update:$("#fBgUp").checked, autostart:$("#fAuto").checked, ui_scale:$("#fScale").value});
  const wk = $("#fWago").value.trim(); if (wk) body.wago_key = wk;
  const key = $("#fKey").value.trim(); if (key) body.api_key = key;
  try{ await post("/api/settings", body); closeSettings(); }
  catch(e){ toast(e.message); }
}

// ---------- add addons ----------
function openAdd(){ $("#mAdd").classList.add("show"); setTimeout(()=>$("#links").focus(),0); }
function closeAdd(){ $("#mAdd").classList.remove("show"); }
function pickZip(key){ zipKey = key || null; $("#file").value=""; $("#file").click(); }
$("#file").addEventListener("change", ()=>{
  const f = $("#file").files[0]; if (!f) return;
  const hdr = {"X-Filename": encodeURIComponent(f.name)}; if (zipKey) hdr["X-Mod-Key"] = zipKey;
  act("/api/install_zip", f, hdr);
});

$("#bAddOpen").onclick = openAdd;
$("#addCancel").onclick = closeAdd;
$("#mAdd").addEventListener("click", e=>{ if (e.target.id==="mAdd") closeAdd(); });
$("#toBrowse").onclick = e=>{ e.preventDefault(); closeAdd(); showView("browse"); };
$("#bAdd").onclick = ()=>{ const t=$("#links").value.trim(); if(!t){ $("#links").focus(); return; } act("/api/add",{text:t}).then(r=>{ if (r){ $("#links").value=""; closeAdd(); } }); };
$("#links").addEventListener("keydown", e=>{ if (e.key==="Enter" && (e.ctrlKey||e.metaKey)) $("#bAdd").click(); });
$("#bMoreIns").onclick = e=>openMenu(e.currentTarget, [
  S && S.last_batch ? {label:"Show the last update ("+S.last_batch.items.length+")", run:showBatch} : null,
  S && S.last_batch ? {label:"Undo the last update", disabled:S.job.running, run:()=>$("#btUndo").onclick()} : null,
  S && S.last_batch ? "-" : null,
  {label:"Install from a zip file…", disabled:S && S.job.running, run:()=>pickZip(null)},
  {label:"Share / move my list…", run:openShare},
  {label:"Import a list file…", run:()=>{ $("#importFile").value=""; $("#importFile").click(); }},
  {label:"Export my list", href:"/api/export", download:"wow-forever-addons.json"},
  "-",
  {label:"Look up hand-installed addons", disabled:S && S.job.running, run:()=>act("/api/scan")},
  {label:"Open the AddOns folder", run:()=>act("/api/open_folder")},
]);
$("#bCheck").onclick = ()=>act("/api/check");
$("#bUpdAll").onclick = ()=>act("/api/update_all");
$("#bCancel").onclick = ()=>closeSettings();
$("#bSave").onclick = saveSettings;
$("#bDetect").onclick = detect;
$("#bTypes").onclick = loadTypes;
$("#bTestWago").onclick = async ()=>{
  const out = $("#wagoResult"); out.style.display="block"; out.style.color="var(--muted)"; out.textContent="Testing…";
  try{ const r = await post("/api/test_wago",{wago_key:$("#fWago").value}); out.style.color = r.ok ? "var(--green)" : "var(--red)"; out.textContent = (r.ok?"✔ ":"✖ ") + r.message; }
  catch(e){ out.style.color="var(--red)"; out.textContent="✖ "+e.message; }
};
$("#bTestKey").onclick = async ()=>{
  const out = $("#keyResult"); out.style.display="block"; out.style.color="var(--muted)"; out.textContent="Testing…";
  try{ const r = await post("/api/test_key",{api_key:$("#fKey").value}); out.style.color = r.ok ? "var(--green)" : "var(--red)"; out.textContent = (r.ok?"✔ ":"✖ ") + r.message; }
  catch(e){ out.style.color="var(--red)"; out.textContent="✖ "+e.message; }
};
for (const [id, outId] of [["#fKey","#keyResult"],["#fWago","#wagoResult"]]){
  $(id).addEventListener("paste", ()=>setTimeout(async ()=>{
    const k = $(id).value.trim(), out = $(outId); if (!k) return;
    out.style.display = ""; out.style.color = "var(--muted)"; out.textContent = "Checking the key…";
    try{ const r = await post("/api/save_key",{key:k}); out.style.color = r.ok ? "var(--green)" : "var(--red)"; out.textContent = (r.ok?"✔ ":"✖ ") + r.message;
      if (r.ok){ $(id).value = ""; await refresh(); const c = S.config;
        $("#fKey").placeholder = c.has_key ? "Saved" + (c.key_hint?" (ends in "+c.key_hint+")":"") + " — leave empty to keep" : "Paste your API key";
        $("#fWago").placeholder = c.has_wago_key ? "Saved" + (c.wago_key_hint?" (ends in "+c.wago_key_hint+")":"") + " — leave empty to keep" : "Paste your Wago key";
        $("#fUseWago").checked = c.use_wago !== false; } }
    catch(e){ out.style.color = "var(--red)"; out.textContent = "✖ " + e.message; }
  }, 50));
}
$("#dbar").onclick = ()=>setDrawer(!drawerOpen);
document.querySelectorAll(".side .tab").forEach(t=>t.onclick=()=>showView(t.dataset.v));
let bTimer=null;
$("#bq").addEventListener("input", ()=>{ clearTimeout(bTimer); bTimer=setTimeout(()=>{ B.q=$("#bq").value.trim(); bSearch(false); }, 350); });
$("#wcat").addEventListener("change", ()=>{ W.cat = $("#wcat").value; renderBrowse(); });
$("#wsort").addEventListener("change", ()=>{ W.sort = $("#wsort").value; renderBrowse(); });
$("#bq").addEventListener("keydown", e=>{ if (e.key==="Enter"){ clearTimeout(bTimer); B.q=$("#bq").value.trim(); bSearch(false); } });
$("#bsort").onchange = ()=>{ B.sort=$("#bsort").value; saveUi(); bSearch(false); };
$("#bcat").onchange = ()=>{ B.cat=$("#bcat").value; saveUi(); bSearch(false); };
$("#msrc").onchange = ()=>{ mSrc = $("#msrc").value; saveUi(); render(); };
let resizeT = null; addEventListener("resize", ()=>{ clearTimeout(resizeT); resizeT = setTimeout(()=>{ if (S) render(); }, 150); });
$("#bMoreBtn").onclick = ()=>bSearch(true);
$("#acat").addEventListener("change", ()=>{ A.cat = $("#acat").value; saveUi(); bSearch(false); });
$("#asort").addEventListener("change", ()=>{ A.sort = $("#asort").value; saveUi(); bSearch(false); });

$("#bBackupNow").onclick = ()=>act("/api/backup_now");
$("#bSetupExport").onclick = ()=>act("/api/setup_export");
$("#bSetupRestore").onclick = ()=>{ $("#fileSetup").value = ""; $("#fileSetup").click(); };
$("#fileSetup").addEventListener("change", async ()=>{
  const f = $("#fileSetup").files[0]; if (!f) return;
  if (await ask("Restore " + f.name + "?", "Copies the addon settings, keybinds and macros in it here and installs its addons. Nothing is removed, and your current settings are backed up first. WoW must be closed.", "Restore")) act("/api/setup_restore", f);
});
$("#bSyncSave").onclick = ()=>act("/api/settings",{sync_dir:$("#fSync").value.trim()}).then(r=>{ if (r) toast($("#fSync").value.trim() ? "Sync folder saved." : "Sync turned off."); });
$("#chReload").onclick = loadChars;
$("#chAll").onchange = renderChars;
$("#bShortcut").onclick = ()=>act("/api/shortcuts");
window.addEventListener("pagehide", ()=>{ try{ navigator.sendBeacon("/api/bye", ""); }catch(e){} });
// In the desktop program there is no browser around the page: outside links open in your web browser.
document.addEventListener("click", e=>{
  const a = e.target && e.target.closest && e.target.closest("a[href]");
  if (!a) return;
  let u; try { u = new URL(a.href, location.href); } catch(_) { return; }
  if (u.origin === location.origin || !/^https?:$/.test(u.protocol)) return;
  if (window.chrome && window.chrome.webview){ e.preventDefault(); post("/api/open_url", {url:u.href}).catch(()=>{}); }
}, true);
$("#bScan").onclick = ()=>act("/api/scan");
async function openShare(){
  $("#shCode").value = "…"; $("#shIn").value = ""; $("#shCount").textContent = "";
  $("#mShare").classList.add("show");
  try{ const r = await post("/api/share_code",{}); $("#shCode").value = r.code; $("#shCount").textContent = r.count + " addon" + (r.count===1?"":"s"); }
  catch(e){ $("#shCode").value = e.message; }
}
$("#shCopy").onclick = async ()=>{ const t = $("#shCode").value; try{ await navigator.clipboard.writeText(t); }catch(e){ $("#shCode").select(); document.execCommand("copy"); } $("#shCopy").textContent = "Copied ✓"; setTimeout(()=>$("#shCopy").textContent="Copy code", 2000); };
$("#shClose").onclick = ()=>$("#mShare").classList.remove("show");
$("#mShare").addEventListener("click", e=>{ if (e.target.id==="mShare") $("#mShare").classList.remove("show"); });
$("#shGo").onclick = ()=>{ const t = $("#shIn").value.trim(); if (!t) return; $("#mShare").classList.remove("show"); act("/api/import_text",{text:t}); };
$("#importFile").addEventListener("change", ()=>{ const f=$("#importFile").files[0]; if (f) act("/api/import", f); });
$("#clClose").onclick = ()=>$("#mLog").classList.remove("show");
$("#mLog").addEventListener("click", e=>{ if (e.target.id==="mLog") $("#mLog").classList.remove("show"); });
$("#bDiag").onclick = ()=>{ $("#mDiag").classList.add("show"); renderDiag(); if (!S.diag || !S.diag.running) act("/api/diagnose"); };
$("#dgRun").onclick = ()=>act("/api/diagnose");
$("#dgClose").onclick = ()=>$("#mDiag").classList.remove("show");
$("#dgCopy").onclick = async ()=>{ const t = diagText(); try{ await navigator.clipboard.writeText(t); $("#dgCopy").textContent="Copied ✓"; }catch(e){ const ta=h("textarea",{style:"position:fixed;opacity:0"}); ta.value=t; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove(); $("#dgCopy").textContent="Copied ✓"; } setTimeout(()=>$("#dgCopy").textContent="Copy report",2000); };
$("#q").addEventListener("input", render);
document.querySelectorAll(".stat").forEach(e=>e.onclick=()=>{ filter = (filter===e.dataset.f && filter!=="all") ? "all" : e.dataset.f; saveUi(); render(); });
// A new program version is offered once (Update now / Later). It is never installed without asking.
let selfOffered = "";
async function offerSelfUpdate(u){
  if (selfOffered === u.version || S.config.self_update_dismissed === u.version || S.job.running || document.querySelector(".modal.show")) return;
  selfOffered = u.version;
  const yes = await ask("WoW Forever Addons " + u.version + " is available", (u.notes ? u.notes.slice(0,700) + "\n\n" : "") + "Update now? The program closes, updates itself and opens again. Your addons and settings stay as they are.\n\nChoose Later to keep this version — you can update any time from the box in the menu.", "Update now", undefined, "Later");
  if (yes) act("/api/self_update");
  else { try{ await post("/api/settings",{self_update_dismissed:u.version}); }catch(e){} }
}
async function selfCheck(){
  const b = $("#bSelfChk"); b.disabled = true; b.textContent = "Checking…";
  try{
    const r = await post("/api/check_self_update",{});
    if (r.update){ await refresh(); const u = S.self_update;
      if (u && await ask("Update WoW Forever Addons to " + u.version + "?", (u.notes ? u.notes.slice(0,700) + "\n\n" : "") + "The program closes, updates itself and opens again. Your addons and settings stay as they are.", "Update now")) act("/api/self_update"); }
    else toast("You have the newest version (" + r.current + ").");
  }catch(e){ toast(e.message); }
  b.disabled = false; b.textContent = "Check for program update";
}
$("#bSelfChk").onclick = selfCheck;
$("#bQuit").onclick = async ()=>{ if(!(await ask("Stop the addon manager?", "Your addons stay installed. Start it again any time from the desktop or Start menu shortcut.", "Quit"))) return; try{ await post("/api/quit",{}); }catch(e){} clearTimeout(timer); document.body.replaceChildren(h("div",{class:"empty",style:"padding-top:20vh"}, h("b",{},"The addon manager has stopped."), h("br"), "You can close this window. Start it again from the desktop or Start menu shortcut.")); };
// keyboard: Esc closes the top-most thing, Ctrl+F searches
document.addEventListener("keydown", e=>{
  if (e.key==="Escape"){
    if (!$("#pop").hidden) return closeMenu();
    if ($("#lightbox").classList.contains("show")) return $("#lightbox").classList.remove("show");
    for (const id of ["#mAsk","#mLog","#mDiag","#mAdd","#mBatch"]) if ($(id).classList.contains("show")){ if (id==="#mAsk") $("#askNo").click(); else $(id).classList.remove("show"); return; }
    if (DT.id) return closeDetail();
    if ($("#mSettings").classList.contains("show") && S.config.has_key && S.config.addons_dir_ok) return closeSettings();
  }
  if ((e.ctrlKey||e.metaKey) && e.key.toLowerCase()==="f"){ e.preventDefault(); (view==="browse"||view==="auras" ? $("#bq") : $("#q")).focus(); }
  if (e.key==="F5" && !e.ctrlKey){ e.preventDefault(); if (!document.querySelector(".modal.show") && !$("#bCheck").disabled) $("#bCheck").click(); }
  if ((e.ctrlKey||e.metaKey) && !e.shiftKey && !e.altKey && /^[1-5]$/.test(e.key)){ e.preventDefault(); showView(["installed","browse","auras","chars","backups"][+e.key-1]); }
});
document.addEventListener("contextmenu", e=>{ if (!e.target.closest("input,textarea,.desc,.cl,.logbox")) e.preventDefault(); });
refresh();
