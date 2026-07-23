/* OnAgain seller UI — vanilla JS against src/api.py */

const S = {
  screen: "upload",
  batchId: null,
  sourceUrl: null,
  garments: [],           // from /parse
  base: "mannequin",
  bases: [],
  status: null,           // /batch poll payload
  copyMode: {},           // garment_number -> keyword|lifestyle
  platform: {},           // garment_number -> platform override
  edits: {},              // garment_number -> {title, price, size, measurements}
  approved: {},           // garment_number -> true
  busyCopy: {},           // garment_number -> true while regen in flight
  copied: {},             // garment_number -> true briefly after copy
  parsing: false,
};
let pollTimer = null;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const PCOL = { poshmark:["#DBEAFE","#1E40AF"], depop:["#F3E8FF","#9333EA"], ebay:["#ECFDF5","#047857"], vinted:["#ECFEFF","#0891B2"] };
const PLAT_RULES = { poshmark:"Brand-led · detailed condition & styling", depop:"Casual tone · aesthetic hashtags", ebay:"Keyword-dense 80-char title · specifics", vinted:"Short & friendly · category hashtags" };
const STEP_LABELS = { identify:"Identifying", vto:"Generating model photo", price:"Researching price", copy:"Drafting listing" };

function go(screen){ S.screen = screen; if(screen==="upload"){ resetBatch(); } render(); }
function resetBatch(){ S.batchId=null; S.sourceUrl=null; S.garments=[]; S.status=null; S.copyMode={}; S.platform={}; S.edits={}; S.approved={}; if(pollTimer){clearInterval(pollTimer);pollTimer=null;} }

/* ---------------- upload + parse ---------------- */

async function pickFile(){
  const inp = document.createElement("input");
  inp.type = "file"; inp.accept = "image/*";
  inp.onchange = () => inp.files[0] && doParse(inp.files[0]);
  inp.click();
}

async function doParse(file){
  S.parsing = true; render();
  const fd = new FormData(); fd.append("photo", file);
  try{
    const r = await fetch("/api/parse", { method:"POST", body:fd });
    const d = await r.json();
    if(!d.gate?.pass){ alert("Photo rejected: " + (d.gate?.reason || "not apparel")); S.parsing=false; render(); return; }
    S.batchId = d.batch_id; S.sourceUrl = d.source_url; S.garments = d.garments;
  }catch(e){ alert("Parse failed: "+e); }
  S.parsing = false; render();
}

async function loadBases(){
  try{ const d = await (await fetch("/api/bases")).json(); S.bases = d.bases; }catch(e){ S.bases=[{name:"mannequin"},{name:"model"}]; }
}

/* ---------------- generate + poll ---------------- */

async function generate(){
  await fetch(`/api/batch/${S.batchId}/generate`, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ base: S.base }),
  });
  S.screen = "processing"; render();
  pollTimer = setInterval(poll, 2000); poll();
}

async function poll(){
  if(!S.batchId) return;
  const d = await (await fetch(`/api/batch/${S.batchId}`)).json();
  S.status = d;
  if(d.status === "done" && S.screen === "processing"){
    clearInterval(pollTimer); pollTimer = null;
    S.screen = "review";
  }
  render();
}

/* ---------------- review helpers ---------------- */

function garment(n){ return (S.status?.garments || []).find(g => g.garment_number === n); }
function edits(n){ return S.edits[n] || (S.edits[n] = {}); }
function currentPlatform(g){ return S.platform[g.garment_number] || (g.channel?.primary || "ebay"); }
function currentVariant(g){
  const mode = S.copyMode[g.garment_number] || "keyword";
  const v = (g.copy?.variants || []).find(x => x.style === mode);
  return v || (g.copy?.variants || [])[0] || { title:"", description:"", hashtags:[] };
}
function displayTitle(g){
  return edits(g.garment_number).title ?? currentVariant(g).title ?? (g.identity?.type || "Garment");
}
function priceOf(g){
  const e = edits(g.garment_number);
  if(e.price) return e.price;
  const m = g.pricing?.suggested_mid;
  return m ? `$${m}` : "—";
}

async function regenCopy(n){
  const g = garment(n); if(!g) return;
  S.busyCopy[n] = true; render();
  const e = edits(n);
  const facts = {};
  ["brand","color","material_estimate","condition_estimate","visible_size"].forEach(k => { if(e[k] !== undefined) facts[k] = e[k]; });
  if(e.size !== undefined) facts.visible_size = e.size;
  try{
    const d = await (await fetch(`/api/batch/${S.batchId}/garment/${n}/regen_copy`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ facts, platform: currentPlatform(g) }),
    })).json();
    g.copy = d.copy; g.identity = d.identity;
  }catch(err){ console.error(err); }
  S.busyCopy[n] = false; render();
}

function factEdit(n, key, value){
  edits(n)[key] = value;
  clearTimeout(factEdit._t?.[n]); (factEdit._t = factEdit._t || {})[n] = setTimeout(() => regenCopy(n), 900);
}
function setPlatform(n, p){ S.platform[n] = p; regenCopy(n); }
function setCopyMode(n, m){ S.copyMode[n] = m; render(); }

function pasteText(g){
  const v = currentVariant(g);
  const id = g.identity || {};
  const e = edits(g.garment_number);
  const specifics = [
    ["Brand", e.brand ?? id.brand ?? "Unbranded"], ["Size", e.size ?? id.visible_size ?? "—"],
    ["Color", e.color ?? id.color ?? "—"], ["Material", e.material_estimate ?? id.material_estimate ?? "—"],
    ["Condition", e.condition_estimate ?? id.condition_estimate ?? "—"],
  ];
  const meas = e.measurements ? `\nMeasurements: ${e.measurements}` : "";
  const tags = (v.hashtags || []).join(" ");
  return `${displayTitle(g)}\n\n${v.description || ""}${meas}\n\n${tags}\n\n` + specifics.map(([k,x]) => `${k}: ${x}`).join("\n");
}

function copyListing(n){
  const g = garment(n); if(!g) return;
  navigator.clipboard.writeText(pasteText(g)).catch(()=>{});
  S.copied[n] = true; render();
  setTimeout(() => { S.copied[n] = false; render(); }, 1600);
}
function downloadPhoto(n){
  const g = garment(n);
  if(g?.vto?.best_url){ const a = document.createElement("a"); a.href = g.vto.best_url; a.download = `onagain_${n}.jpg`; a.click(); }
}
function flagsOf(g){
  const f = [], e = edits(g.garment_number);
  const size = e.size ?? g.identity?.visible_size;
  if(!size) f.push("Confirm size");
  if(!e.measurements) f.push("Add measurements");
  const gf = g.copy?.flags || {};
  if(gf.needs_flaw_photos) f.push("Add flaw photos");
  return f;
}

/* ---------------- renderers ---------------- */

function render(){
  $("crumb").textContent = { upload:"New batch", processing:"Processing", review:"Review" }[S.screen] || "";
  const m = $("main");
  if(S.screen === "upload") m.innerHTML = rUpload();
  else if(S.screen === "processing") m.innerHTML = rProcessing();
  else if(S.screen === "review") m.innerHTML = rReview();
}

function rUpload(){
  let inner;
  if(S.parsing){
    inner = `<div class="dropzone" style="cursor:default">
      <span class="spin" style="width:26px;height:26px"></span>
      <div style="font:500 12px Inter;color:#6B7280;margin-top:12px">Detecting garments…</div></div>`;
  } else if(!S.batchId){
    inner = `<div class="dropzone" onclick="pickFile()">
      <div style="width:48px;height:48px;border-radius:9999px;background:#FDF2EF;display:flex;align-items:center;justify-content:center;margin:0 auto 16px">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M12 16V4M12 4l-5 5M12 4l5 5M4 20h16" stroke="#C4654A" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
      <div style="font-size:16px;font-weight:500;margin-bottom:4px">Drop your garment photo here</div>
      <div style="font-size:13px;color:#6B7280">click to choose a photo of several garments</div></div>`;
  } else {
    const boxes = S.garments.map(g => {
      const b = g.box_pct; if(!b) return "";
      const label = [g.type, g.brand_text].filter(Boolean).join(" · ");
      return `<div class="box" style="left:${b.left}%;top:${b.top}%;width:${b.width}%;height:${b.height}%">
        <span class="boxlabel">${esc(label)}</span></div>`;
    }).join("");
    const baseCards = ["mannequin","model"].map(b => `
      <div class="basecard ${S.base===b?"sel":""}" onclick="S.base='${b}';render()">
        <div style="aspect-ratio:1;border-radius:8px;overflow:hidden;margin-bottom:10px;background:#ece9e2">
          <img src="/api/base/${b}" style="width:100%;height:100%;object-fit:cover"></div>
        <div style="display:flex;align-items:center;justify-content:space-between">
          <span style="font-size:13px;font-weight:500">${b==="mannequin"?"Mannequin":"Model photo"}</span>
          <span style="width:16px;height:16px;border-radius:9999px;background:${S.base===b?"#C4654A":"transparent"};display:flex;align-items:center;justify-content:center;color:#fff;font-size:10px">${S.base===b?"✓":""}</span>
        </div></div>`).join("");
    inner = `<div class="fade">
      <div style="display:grid;grid-template-columns:minmax(240px,340px) 1fr;gap:24px;align-items:start;margin-bottom:24px">
        <div>
          <div style="font-size:12px;font-weight:500;color:#6B7280;margin-bottom:8px">Source · ${S.garments.length} garments detected</div>
          <div style="position:relative;border-radius:12px;overflow:hidden">
            <img src="${S.sourceUrl}" style="width:100%;display:block">${boxes}</div>
        </div>
        <div>
          <div style="font-size:12px;font-weight:500;color:#6B7280;margin-bottom:8px">Render garments as</div>
          <div style="display:flex;gap:12px;max-width:360px">${baseCards}</div>
        </div>
      </div>
      <button class="btn btn-lg" onclick="generate()">Generate ${S.garments.length} listings →</button>
    </div>`;
  }
  return `<div style="max-width:860px;margin:0 auto" class="fade">
    <h1>New batch</h1>
    <p class="sub" style="margin-bottom:24px">Upload one photo of several garments — OnAgain isolates each item.</p>
    ${inner}</div>`;
}

function rProcessing(){
  const gs = S.status?.garments || [];
  const doneCount = gs.filter(g => Object.values(g.progress||{}).every(v => v==="done"||v==="failed") && Object.keys(g.progress||{}).length).length;
  const cards = gs.map(g => {
    const prog = g.progress || {};
    const failed = prog.vto === "failed";
    const allDone = Object.values(prog).length && Object.values(prog).every(v => v==="done"||v==="failed");
    const steps = Object.keys(STEP_LABELS).map(k => {
      const st = prog[k] || "wait";
      let dot, color = "#9CA3AF", weight = "400";
      if(st==="done"){ dot = `<span class="stepdot" style="background:#16A34A;color:#fff">✓</span>`; color="#6B7280"; }
      else if(st==="active"){ dot = `<span class="spin" style="width:14px;height:14px;border-width:2px;border-color:#D97706;border-top-color:transparent"></span>`; color="#1A1A1A"; weight="500"; }
      else if(st==="failed"){ dot = `<span class="stepdot" style="background:#DC2626;color:#fff">!</span>`; color="#DC2626"; weight="500"; }
      else dot = `<span class="stepdot" style="border:1.5px solid #E5E2DB"></span>`;
      return `<div style="display:flex;align-items:center;gap:8px">${dot}<span style="color:${color};font-weight:${weight};font-size:12px">${STEP_LABELS[k]}</span></div>`;
    }).join("");
    const hero = g.vto?.best_url
      ? `<img src="${g.vto.best_url}" style="width:100%;height:100%;object-fit:cover">`
      : failed ? `<span style="font-size:22px;color:#DC2626">⚠</span>`
      : allDone ? `<span class="stepdot" style="width:20px;height:20px;background:#16A34A;color:#fff;font-size:12px">✓</span>`
      : `<span class="spin" style="width:20px;height:20px"></span>`;
    return `<div class="card" style="border-left:${failed?"3px solid #DC2626":"none"}">
      <div style="aspect-ratio:16/11;border-radius:8px;overflow:hidden;background:#efece5;display:flex;align-items:center;justify-content:center;margin-bottom:12px">${hero}</div>
      <div style="font-size:13px;font-weight:500;margin-bottom:10px">${esc(g.identity?.type || g.type || "Garment")}</div>
      <div style="display:flex;flex-direction:column;gap:7px">${steps}</div></div>`;
  }).join("");
  return `<div class="fade">
    <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:20px">
      <div><h1>Processing batch</h1><p class="sub">${doneCount} of ${gs.length||"…"} complete · rendering as ${S.base}</p></div>
      <button class="btn-ghost" onclick="S.screen='review';render()">Skip to review →</button></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px">${cards}</div></div>`;
}

function rReview(){
  const gs = S.status?.garments || [];
  const approvedCount = Object.values(S.approved).filter(Boolean).length;
  const cards = gs.map(g => rReviewCard(g)).join("");
  return `<div class="fade">
    <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:20px">
      <div><h1>Review listings</h1><p class="sub">${approvedCount} of ${gs.length} approved · add sizes, then list</p></div>
      </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(400px,1fr));gap:16px;align-items:stretch">${cards}</div></div>`;
}

function rReviewCard(g){
  const n = g.garment_number;
  const failed = (g.progress?.vto === "failed") && !g.vto?.best_url;
  if(failed){
    return `<div class="card" style="border-left:3px solid #DC2626;display:flex;flex-direction:column">
      <div style="aspect-ratio:4/5;border-radius:8px;overflow:hidden;background:#f3e7e5;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;margin-bottom:12px">
        <img src="${g.crop_url}" style="width:100%;height:100%;object-fit:cover;opacity:.5"></div>
      <div style="font-size:13px;font-weight:500;margin-bottom:8px">${esc(g.identity?.type || g.type || "Garment")}</div>
      <div class="flag" style="align-self:flex-start;background:#FEF2F2;color:#DC2626;margin-bottom:10px">⚠ VTO failed</div>
      <p style="font-size:12px;color:#6B7280;margin:0 0 12px;line-height:1.5">${esc(g.vto?.ranking_reason || "Re-photograph with spacing between garments so each item is isolated.")}</p>
    </div>`;
  }
  if(S.approved[n]){
    return `<div class="card" style="display:flex;flex-direction:column">
      <div style="position:relative;aspect-ratio:4/5;border-radius:8px;overflow:hidden;margin-bottom:12px">
        <img src="${g.vto?.best_url || g.crop_url}" style="width:100%;height:100%;object-fit:cover">
        <span style="position:absolute;top:9px;left:9px;background:#F0FDF4;color:#16A34A;font:500 11px Inter;padding:3px 8px;border-radius:9999px">✓ Approved</span></div>
      <div style="font-size:13px;font-weight:500;margin-bottom:10px">${esc(displayTitle(g))}</div>
      <div style="font-size:11px;color:#6B7280;margin-bottom:12px">Listed to <b style="color:#1A1A1A">${esc(currentPlatform(g))}</b></div>
      <div style="display:flex;gap:8px;margin-top:auto">
        <button class="btn-ghost" style="flex:1" onclick="copyListing(${n})">${S.copied[n]?"Copied ✓":"Copy listing"}</button>
        <button class="btn-ghost" onclick="downloadPhoto(${n})">Photo</button>
      </div></div>`;
  }
  const id = g.identity || {}, e = edits(n);
  const v = currentVariant(g);
  const plat = currentPlatform(g);
  const kw = (S.copyMode[n] || "keyword") === "keyword";
  const platformPills = ["poshmark","depop","ebay","vinted"].map(p => {
    const on = p === plat, col = PCOL[p];
    return `<span class="pill" style="${on?`background:${col[0]};color:${col[1]};border-color:transparent`:""}" onclick="setPlatform(${n},'${p}')">${p[0].toUpperCase()+p.slice(1)}</span>`;
  }).join("");
  const flags = flagsOf(g).map(f => `<span class="flag">⚠ ${f}</span>`).join("");
  const size = e.size ?? id.visible_size ?? "";
  const spec = (key, val) => `<div class="specrow"><span class="speck">${key}</span>
    <input class="specv" value="${esc(val)}" onchange="factEdit(${n},'${{Brand:"brand",Color:"color",Material:"material_estimate",Condition:"condition_estimate"}[key]}',this.value)" style="${key==="Brand"?"border-bottom-color:#C4654A;font-weight:600":""}"></div>`;
  return `<div class="card" style="display:flex;flex-direction:column">
    <div style="position:relative;aspect-ratio:4/5;border-radius:8px;overflow:hidden;margin-bottom:12px;background:#efece5">
      <img src="${g.vto?.best_url || g.crop_url}" style="width:100%;height:100%;object-fit:cover">
      <span style="position:absolute;top:9px;left:9px;background:#F0FDF4;color:#16A34A;font:500 11px Inter;padding:3px 8px;border-radius:9999px">✓ Complete</span>
      <img src="${g.crop_url}" style="position:absolute;bottom:8px;right:8px;width:40px;height:50px;border-radius:5px;border:2px solid #fff;object-fit:cover">
    </div>
    <input class="field" style="font-weight:500;font-size:13px;margin-bottom:10px" value="${esc(displayTitle(g))}" onchange="edits(${n}).title=this.value;render()">
    <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:12px">
      <input value="${esc(priceOf(g))}" onchange="edits(${n}).price=this.value" style="width:58px;background:transparent;border:none;border-bottom:1px dashed #C4654A;font:500 16px Inter;padding:0 0 1px">
      <span style="font-size:11px;color:#9CA3AF">${g.pricing?.suggested_low?`$${g.pricing.suggested_low}–${g.pricing.suggested_high}`:"no comp data"}</span>
      <span style="font-size:11px;color:#C4654A;font-weight:500">${g.pricing?.comp_count||0} comps</span></div>
    <div style="display:flex;gap:8px;margin-bottom:12px">
      <div style="width:76px"><div class="microlabel">Size</div>
        <input class="field" style="border-color:${size?"#E5E2DB":"#F0C89A"}" placeholder="e.g. M" value="${esc(size)}" onchange="factEdit(${n},'size',this.value)"></div>
      <div style="flex:1"><div class="microlabel">Measurements</div>
        <input class="field" style="font-weight:400;border-color:${e.measurements?"#E5E2DB":"#F0C89A"}" placeholder="Waist 32in, Inseam 32in…" value="${esc(e.measurements||"")}" onchange="edits(${n}).measurements=this.value;render()"></div></div>
    <div style="margin-bottom:12px"><div class="microlabel">Item specifics</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 16px;background:#FAFAF7;border:1px solid #E5E2DB;border-radius:8px;padding:10px 11px">
        ${spec("Brand", e.brand ?? id.brand ?? "Unbranded")}
        <div class="specrow"><span class="speck">Size</span><span style="font:500 11px Inter">${esc(size||"—")}</span></div>
        ${spec("Color", e.color ?? id.color ?? "")}
        ${spec("Material", e.material_estimate ?? id.material_estimate ?? "")}
        ${spec("Condition", e.condition_estimate ?? id.condition_estimate ?? "")}
      </div></div>
    <div style="margin-bottom:12px">
      <div style="display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:6px">
        <span class="microlabel" style="margin:0">List on</span>
        <span style="font:400 10px Inter;color:#9CA3AF">${PLAT_RULES[plat]||""}</span></div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">${platformPills}</div>
      ${g.channel?.primary ? `<div style="display:flex;align-items:center;gap:5px;font:500 10px Inter;color:#C4654A;margin-top:7px"><span style="width:5px;height:5px;border-radius:50%;background:#C4654A"></span>Recommended · ${esc(g.channel.primary_reasoning||g.channel.primary)}</div>`:""}
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <span class="microlabel" style="margin:0">Copy style</span>
      <div style="display:inline-flex;background:#FAFAF7;border:1px solid #E5E2DB;border-radius:9999px;padding:2px">
        <span style="background:${kw?"#C4654A":"transparent"};color:${kw?"#fff":"#6B7280"};font:500 10px Inter;padding:3px 10px;border-radius:9999px;cursor:pointer" onclick="setCopyMode(${n},'keyword')">Keyword</span>
        <span style="background:${kw?"transparent":"#C4654A"};color:${kw?"#6B7280":"#fff"};font:500 10px Inter;padding:3px 10px;border-radius:9999px;cursor:pointer" onclick="setCopyMode(${n},'lifestyle')">Lifestyle</span></div></div>
    <div style="position:relative;overflow:hidden;background:#fff;border:1px solid #E5E2DB;border-radius:8px;padding:11px;margin-bottom:10px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px">
        <span style="font:600 9px Inter;color:#9CA3AF;text-transform:uppercase;letter-spacing:.06em">Listing preview · ${esc(plat)} · plain text</span>
        <span style="cursor:pointer;font:500 10px Inter;color:${S.copied[n]?"#16A34A":"#6B7280"}" onclick="copyListing(${n})">${S.copied[n]?"✓ Copied":"Copy"}</span></div>
      <div style="font:600 12px Inter;line-height:1.35;margin-bottom:7px">${esc(displayTitle(g))}</div>
      <div style="font:400 11.5px Inter;color:#4B4B4B;line-height:1.5;white-space:pre-line">${esc(v.description||"")}\n\n${esc((v.hashtags||[]).join(" "))}</div>
      ${S.busyCopy[n]?`<div style="position:absolute;inset:0;background:rgba(255,255,255,.74);display:flex;align-items:center;justify-content:center;gap:8px"><span class="spin" style="width:15px;height:15px;border-width:2px"></span><span style="font:500 11px Inter;color:#6B7280">updating copy…</span></div>`:""}
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;background:#F5F3EE;border:1px dashed #DDD8CF;border-radius:8px;padding:8px 10px;margin-bottom:12px">
      <span style="font:500 11px Inter;color:#6B7280">Seller voice — match my tone</span>
      <span style="font:600 9px Inter;color:#9CA3AF;background:#FAFAF7;border:1px solid #E5E2DB;padding:2px 7px;border-radius:9999px;text-transform:uppercase;letter-spacing:.06em">Soon</span></div>
    ${flags?`<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">${flags}</div>`:""}
    <div style="display:flex;gap:8px;margin-top:auto">
      <button class="btn-ghost" onclick="downloadPhoto(${n})">Photo</button>
      <button class="btn" style="flex:1" onclick="S.approved[${n}]=true;render()">Approve & list</button>
    </div>
  </div>`;
}

loadBases().then(render);
