/* OnAgain seller UI — vanilla JS against src/api.py */

const S = {
  screen: "inventory",
  inventory: [],
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
  busyImg: {},            // garment_number -> true while VTO re-render in flight
  copied: {},             // garment_number -> true briefly after copy
  copiedLink: {},         // garment_number -> true briefly after try-on link copy
  parsing: false,
  publicBase: "",          // public URL for shareable try-on links (from /api/config)
};
let pollTimer = null;

// fetch the public base URL once so try-on links are shareable (not localhost)
fetch("/api/config").then(r => r.json()).then(d => { S.publicBase = d.public_base_url || ""; }).catch(() => {});

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const PCOL = { poshmark:["#DBEAFE","#1E40AF"], depop:["#F3E8FF","#9333EA"], ebay:["#ECFDF5","#047857"], vinted:["#ECFEFF","#0891B2"] };
const PLAT_RULES = { poshmark:"Brand-led · detailed condition & styling", depop:"Casual tone · aesthetic hashtags", ebay:"Keyword-dense 80-char title · specifics", vinted:"Short & friendly · category hashtags" };

function go(screen){ S.screen = screen; if(screen==="upload"){ resetBatch(); } if(screen==="inventory"){ loadInventory(); } render(); }

async function loadInventory(){
  try{ const d = await (await fetch("/api/inventory")).json(); S.inventory = d.listings || []; }
  catch(e){ S.inventory = []; }
  render();
}
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
  S.screen = "processing";
  await poll();                                   // seed S.status before first paint
  pollTimer = setInterval(poll, 700);             // tight poll so step updates + done feel live
}

async function poll(){
  if(!S.batchId) return;
  try{
    const r = await fetch(`/api/batch/${S.batchId}`);
    if(r.status === 404){
      // batch is gone (server restarted — in-memory batches don't survive). Stop polling
      // forever and tell the user, instead of spinning on a dead batch.
      if(pollTimer){ clearInterval(pollTimer); pollTimer = null; }
      alert("This batch was lost (the server restarted). Start a new batch.");
      resetBatch(); go("upload");
      return;
    }
    const d = await r.json();
    S.status = d;
    // flip to review once the backend batch is done — hold on the completed cards for
    // 2s first so "3 of 3 complete" is visible, then advance.
    if(d.status === "done" && S.screen === "processing" && !S._advancing){
      if(pollTimer){ clearInterval(pollTimer); pollTimer = null; }
      S._advancing = true;
      render();                                   // paint the all-done state
      setTimeout(() => { S._advancing = false; S.screen = "review"; render(); }, 2000);
      return;
    }
  }catch(e){ console.error("poll error", e); return; }   // keep the interval alive
  try{ render(); }catch(e){ console.error("render error", e); }
}

/* ---------------- review helpers ---------------- */

function garment(n){ return (S.status?.garments || []).find(g => g.garment_number === n); }
function edits(n){ return S.edits[n] || (S.edits[n] = {}); }
function currentPlatform(g){ return S.platform[g.garment_number] || (g.channel?.primary || "ebay"); }
function currentVariant(g){
  // one copy per listing — the keyword variant (search-optimized), no style toggle
  const v = (g.copy?.variants || []).find(x => x.style === "keyword");
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

// update ONLY one review card's DOM — avoids the full-screen flash on copy/platform change
function refreshCard(n){
  const el = document.getElementById(`rcard-${n}`);
  const g = garment(n);
  if(el && g){ el.innerHTML = rReviewCard(g); }
  else { render(); }   // fallback if the card isn't mounted (e.g. not on review screen)
}

async function regenCopy(n){
  const g = garment(n); if(!g) return;
  S.busyCopy[n] = true; refreshCard(n);
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
  S.busyCopy[n] = false; refreshCard(n);
}

function factEdit(n, key, value){
  edits(n)[key] = value;
  clearTimeout(factEdit._t?.[n]); (factEdit._t = factEdit._t || {})[n] = setTimeout(() => regenCopy(n), 900);
}
function setPlatform(n, p){ S.platform[n] = p; regenCopy(n); }

function baseLabel(b){ return {"mannequin":"female mannequin","mannequin-male":"male mannequin","model":"model photo"}[b] || b; }
function garmentId(n){ const g = garment(n); return (g && g.garment_id) || `${S.batchId}_${n}`; }
function tryonBase(){ return S.publicBase || location.origin; }
function tryonUrl(n){ return `${tryonBase()}/tryon/${garmentId(n)}`; }
function tryonLink(g){ return tryonUrl(g.garment_number).replace(/^https?:\/\//,""); }
function copyTryonLink(n){
  navigator.clipboard.writeText(tryonUrl(n)).catch(()=>{});
  S.copiedLink[n] = true; refreshCard(n);       // patch one card, no full-page flash
  setTimeout(() => { S.copiedLink[n] = false; refreshCard(n); }, 1600);
}

async function approveListing(n){
  const g = garment(n); if(!g) return;
  const e = edits(n), id = g.identity || {};
  const payload = {
    garment_id: garmentId(n),
    title: displayTitle(g),
    price: priceOf(g),
    platform: currentPlatform(g),
    brand: e.brand ?? id.brand ?? null,
  };
  try{
    await fetch(`/api/batch/${S.batchId}/garment/${n}/approve`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(payload),
    });
  }catch(err){ console.error(err); }
  S.approved[n] = true; render();
}

async function regenImage(n){
  const g = garment(n); if(!g) return;
  S.busyImg[n] = true; refreshCard(n);
  try{
    const d = await (await fetch(`/api/batch/${S.batchId}/garment/${n}/regen_image`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ base: S.base }),
    })).json();
    g.vto = d.vto;
    g._rev = (g._rev || 0) + 1;   // cache-bust the <img> so the new render shows
  }catch(err){ alert("Regenerate failed: " + err); }
  S.busyImg[n] = false; refreshCard(n);
}

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
  const tryon = `👗 Try it on yourself: ${tryonLink(g)}`;
  return `${displayTitle(g)}\n\n${v.description || ""}${meas}\n\n${tryon}\n\n${tags}\n\n` + specifics.map(([k,x]) => `${k}: ${x}`).join("\n");
}

function copyListing(n){
  const g = garment(n); if(!g) return;
  navigator.clipboard.writeText(pasteText(g)).catch(()=>{});
  S.copied[n] = true; refreshCard(n);          // patch one card, no full-page flash
  setTimeout(() => { S.copied[n] = false; refreshCard(n); }, 1600);
}
async function saveBlob(src, filename){
  const blob = await (await fetch(src)).blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function downloadPhoto(n){
  const g = garment(n);
  try{
    // download BOTH: the original garment crop and the mannequin render
    if(g?.crop_url) await saveBlob(g.crop_url, `onagain_${n}_original.jpg`);
    if(g?.vto?.best_url) await saveBlob(g.vto.best_url + "?download=1", `onagain_${n}_mannequin.jpg`);
    if(!g?.crop_url && !g?.vto?.best_url) alert("No photos to download yet.");
  }catch(e){ alert("Download failed: " + e); }
}
// resolved size + its source ('tag' | 'profile_default' | null), honoring seller edits
function sizeOf(g){
  const e = edits(g.garment_number), id = g.identity || {};
  if(e.size !== undefined) return { size: e.size, source: e.size ? "edited" : null };
  return { size: id.size ?? id.visible_size ?? "", source: id.size_source ?? (id.visible_size ? "tag" : null) };
}

function flagsOf(g){
  const f = [], e = edits(g.garment_number);
  const s = sizeOf(g);
  // Confirm size when missing OR came from the profile default (not the tag/edit)
  if(!s.size || s.source === "profile_default") f.push("Confirm size");
  if(!e.measurements) f.push("Add measurements");
  const gf = g.copy?.flags || {};
  if(gf.needs_flaw_photos) f.push("Add flaw photos");
  return f;
}

/* ---------------- renderers ---------------- */

function render(){
  $("crumb").textContent = { inventory:"Inventory", upload:"New batch", processing:"Processing", review:"Review" }[S.screen] || "";
  const m = $("main");
  // processing screen updates in place across polls to avoid full-DOM flicker
  if(S.screen === "processing" && render._screen === "processing"){ patchProcessing(); return; }
  render._screen = S.screen;
  if(S.screen === "inventory") m.innerHTML = rInventory();
  else if(S.screen === "upload") m.innerHTML = rUpload();
  else if(S.screen === "processing") m.innerHTML = rProcessing();
  else if(S.screen === "review") m.innerHTML = rReview();
}

/* In-place update of the processing cards — touches only the step rows and hero
   that changed, so images and layout never flicker. */
function patchProcessing(){
  const gs = S.status?.garments || [];
  // if the processing DOM isn't actually present (e.g. re-entered screen), rebuild it
  if(gs.length && !document.getElementById(`steps-${gs[0].garment_number}`)){
    document.getElementById("main").innerHTML = rProcessing();
    return;
  }
  const doneCount = gs.filter(g => Object.values(g.progress||{}).length &&
    Object.values(g.progress||{}).every(v => v==="done"||v==="failed")).length;
  const sub = document.getElementById("proc-sub");
  if(sub) sub.textContent = `${doneCount} of ${gs.length} complete · rendering as ${baseLabel(S.base)}`;
  gs.forEach(g => {
    const steps = document.getElementById(`steps-${g.garment_number}`);
    if(steps) steps.innerHTML = stepRows(g);
    const hero = document.getElementById(`hero-${g.garment_number}`);
    if(hero){ const h = heroHtml(g); if(hero.dataset.state !== h.state){ hero.innerHTML = h.html; hero.dataset.state = h.state; } }
  });
}

function rInventory(){
  const items = S.inventory || [];
  const cards = items.map(it => {
    const col = PCOL[(it.platform||"").toLowerCase()] || ["#F5F3EE","#6B7280"];
    const hero = it.hero_photo
      ? `<img src="${it.hero_photo}" style="width:100%;height:100%;object-fit:contain">`
      : `<span style="color:#9CA3AF;font:500 10px ui-monospace,monospace">no photo</span>`;
    return `<div class="card fade">
      <div style="position:relative;aspect-ratio:4/5;border-radius:8px;overflow:hidden;background:#efece5;display:flex;align-items:center;justify-content:center;margin-bottom:11px">
        ${hero}
        <span style="position:absolute;top:9px;left:9px;background:#F0FDF4;color:#16A34A;font:500 11px Inter;padding:3px 8px;border-radius:9999px">✓ Listed</span>
        <span style="position:absolute;bottom:9px;right:9px;display:inline-flex;align-items:center;gap:4px;background:rgba(255,255,255,.92);color:#6B7280;font:500 11px Inter;padding:3px 8px;border-radius:9999px" title="try-ons">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><ellipse cx="12" cy="12" rx="10" ry="6.5" stroke="#6B7280" stroke-width="2.2"/><circle cx="12" cy="12" r="3" fill="#6B7280"/></svg>${it.tryon_count||0} tried on</span>
      </div>
      <div style="font-size:13px;font-weight:500;margin-bottom:6px">${esc(it.title)}</div>
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <span style="font-size:15px;font-weight:500">${typeof it.price==="number"?"$"+it.price:esc(it.price)}</span>
        <span style="background:${col[0]};color:${col[1]};font:500 11px Inter;padding:3px 9px;border-radius:9999px">${esc(it.platform)}</span></div>
      <button class="btn-ghost" style="width:100%" onclick="window.open('/tryon/${esc(it.garment_id)}','_blank')">Open try-on link</button>
    </div>`;
  }).join("");
  const empty = `<div style="text-align:center;padding:48px 20px;color:#6B7280"><p style="font-size:14px">No listings yet.</p>
    <button class="btn" onclick="go('upload')">+ New batch</button></div>`;
  return `<div class="fade">
    <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:20px">
      <h1>Inventory</h1><span style="font-size:13px;color:#6B7280">${items.length} listed</span></div>
    ${items.length ? `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px">${cards}</div>` : empty}</div>`;
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
    const baseCards = [["mannequin","Female mannequin"],["mannequin-male","Male mannequin"]].map(([b,label]) => `
      <div class="basecard ${S.base===b?"sel":""}" onclick="S.base='${b}';render()">
        <div style="aspect-ratio:1;border-radius:8px;overflow:hidden;margin-bottom:10px;background:#ece9e2">
          <img src="/api/base/${b}" style="width:100%;height:100%;object-fit:contain"></div>
        <div style="display:flex;align-items:center;justify-content:space-between">
          <span style="font-size:13px;font-weight:500">${label}</span>
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
          <div style="font-size:10px;color:#4B5563;margin-top:8px;max-width:360px;line-height:1.4">
            Mannequin base: photo by <a href="https://commons.wikimedia.org/wiki/User:Gerd_Eichmann" target="_blank" rel="noopener" style="color:#374151;text-decoration:underline">Gerd Eichmann</a>,
            <a href="https://creativecommons.org/licenses/by-sa/4.0" target="_blank" rel="noopener" style="color:#374151;text-decoration:underline">CC BY-SA 4.0</a>, cropped.</div>
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

/* Collapse the 4 backend steps into 3 product-facing workers:
   Identifying (identify) · Creating listing (vto+price+copy) · Ready (all done). */
function workerStages(g){
  const p = g.progress || {};
  const done = s => p[s] === "done";
  const failed = s => p[s] === "failed";
  const active = s => p[s] === "active";
  const anyStarted = ["identify","vto","price","copy"].some(s => p[s]);

  // Identifying: reflects the identify step
  let idState = "wait", idDetail = "";
  if(done("identify")){ idState = "done"; const i = g.identity || {};
    idDetail = [i.color, i.type, i.brand].filter(Boolean).map(esc).join(", "); }
  else if(active("identify") || (anyStarted && !p.identify)) idState = "active";
  else if(failed("identify")) idState = "failed";

  // Creating listing: covers vto/price/copy together
  const cl = ["vto","price","copy"];
  let clState = "wait";
  if(cl.every(done)) clState = "done";
  else if(cl.some(failed) && !g.vto?.best_url && !cl.some(active)) clState = "failed";
  else if(done("identify") && (cl.some(active) || cl.some(done))) clState = "active";

  // Ready: all four done (or done-with-failures)
  const allTerminal = ["identify","vto","price","copy"].every(s => p[s]==="done" || p[s]==="failed");
  const readyState = allTerminal ? (g.vto?.best_url ? "done" : "failed") : "wait";

  return [
    { label: idState === "done" && idDetail ? `Identified — ${idDetail}` : "Identifying", state: idState },
    { label: "Creating listing", state: clState },
    { label: "Ready", state: readyState },
  ];
}

function stepRows(g){
  return workerStages(g).map(s => {
    let dot, color = "#9CA3AF", weight = "400";
    if(s.state==="done"){ dot = `<span class="stepdot" style="background:#16A34A;color:#fff">✓</span>`; color="#6B7280"; }
    else if(s.state==="active"){ dot = `<span class="spin" style="width:14px;height:14px;border-width:2px;border-color:#D97706;border-top-color:transparent"></span>`; color="#1A1A1A"; weight="500"; }
    else if(s.state==="failed"){ dot = `<span class="stepdot" style="background:#DC2626;color:#fff">!</span>`; color="#DC2626"; weight="500"; }
    else dot = `<span class="stepdot" style="border:1.5px solid #E5E2DB"></span>`;
    return `<div style="display:flex;align-items:center;gap:8px">${dot}<span style="color:${color};font-weight:${weight};font-size:12px">${s.label}</span></div>`;
  }).join("");
}

/* returns {state, html} so patchProcessing only swaps the hero when its state changes */
function heroHtml(g){
  const prog = g.progress || {};
  const failed = prog.vto === "failed";
  const allDone = Object.values(prog).length && Object.values(prog).every(v => v==="done"||v==="failed");
  if(g.vto?.best_url) return { state:"render:"+g.vto.best_url, html:`<img src="${g.vto.best_url}" style="width:100%;height:100%;object-fit:contain">` };
  if(failed) return { state:"failed", html:`<span style="font-size:22px;color:#DC2626">⚠</span>` };
  if(allDone) return { state:"done", html:`<span class="stepdot" style="width:20px;height:20px;background:#16A34A;color:#fff;font-size:12px">✓</span>` };
  return { state:"spin", html:`<span class="spin" style="width:20px;height:20px"></span>` };
}

function rProcessing(){
  const gs = S.status?.garments || [];
  const doneCount = gs.filter(g => Object.values(g.progress||{}).length &&
    Object.values(g.progress||{}).every(v => v==="done"||v==="failed")).length;
  const cards = gs.map(g => {
    const failed = g.progress?.vto === "failed";
    const h = heroHtml(g);
    return `<div class="card" style="border-left:${failed?"3px solid #DC2626":"none"}">
      <div id="hero-${g.garment_number}" data-state="${h.state}" style="aspect-ratio:16/11;border-radius:8px;overflow:hidden;background:#efece5;display:flex;align-items:center;justify-content:center;margin-bottom:12px">${h.html}</div>
      <div style="font-size:13px;font-weight:500;margin-bottom:10px">${esc(g.identity?.type || g.type || "Garment")}</div>
      <div id="steps-${g.garment_number}" style="display:flex;flex-direction:column;gap:7px">${stepRows(g)}</div></div>`;
  }).join("");
  return `<div class="fade">
    <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:20px">
      <div><h1>Processing batch</h1><p class="sub" id="proc-sub">${doneCount} of ${gs.length||"…"} complete · rendering as ${baseLabel(S.base)}</p></div>
      <button class="btn-ghost" onclick="S.screen='review';render()">Skip to review →</button></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px">${cards}</div></div>`;
}

function rReview(){
  const gs = S.status?.garments || [];
  const approvedCount = Object.values(S.approved).filter(Boolean).length;
  const cards = gs.map(g => `<div id="rcard-${g.garment_number}">${rReviewCard(g)}</div>`).join("");
  return `<div class="fade">
    <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:20px">
      <div><h1>Review listings</h1><p class="sub">${approvedCount} of ${gs.length} approved · add sizes, then list</p></div>
      <button class="btn-ghost" onclick="go('inventory')">Done → inventory</button></div>
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
      <div style="position:relative;aspect-ratio:4/5;border-radius:8px;overflow:hidden;margin-bottom:12px;background:#efece5">
        <img src="${g.vto?.best_url || g.crop_url}" style="width:100%;height:100%;object-fit:contain">
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
  const flags = flagsOf(g).map(f => `<span class="flag">⚠ ${f}</span>`).join("");
  const sz = sizeOf(g);
  const size = sz.size;
  const fromProfile = sz.source === "profile_default";
  const spec = (key, val) => `<div class="specrow"><span class="speck">${key}</span>
    <input class="specv" value="${esc(val)}" onchange="factEdit(${n},'${{Brand:"brand",Color:"color",Material:"material_estimate",Condition:"condition_estimate"}[key]}',this.value)" style="${key==="Brand"?"border-bottom-color:#C4654A;font-weight:600":""}"></div>`;
  return `<div class="card" style="display:flex;flex-direction:column">
    <div style="position:relative;aspect-ratio:4/5;border-radius:8px;overflow:hidden;margin-bottom:6px;background:#efece5">
      <img src="${g.vto?.best_url || g.crop_url}${g._rev?`?r=${g._rev}`:""}" style="width:100%;height:100%;object-fit:contain">
      <span style="position:absolute;top:9px;left:9px;background:#F0FDF4;color:#16A34A;font:500 11px Inter;padding:3px 8px;border-radius:9999px">✓ Complete</span>
      <img src="${g.crop_url}" title="Original photo" style="position:absolute;bottom:8px;right:8px;width:40px;height:50px;border-radius:5px;border:2px solid #fff;object-fit:cover">
      <button onclick="regenImage(${n})" ${S.busyImg[n]?"disabled":""} style="position:absolute;bottom:8px;left:8px;background:rgba(255,255,255,.92);border:none;border-radius:8px;font:500 11px Inter;color:#C4654A;padding:6px 10px;cursor:pointer;display:inline-flex;align-items:center;gap:5px">${S.busyImg[n]?'<span class="spin" style="width:11px;height:11px;border-width:2px"></span> Rendering…':"↻ Regenerate"}</button>
    </div>
    <div style="font:400 10px Inter;color:#9CA3AF;margin-bottom:12px;display:flex;align-items:center;gap:4px">AI preview — verify logos/text against the original photo (inset)</div>
    <input class="field" style="font-weight:500;font-size:13px;margin-bottom:10px" value="${esc(displayTitle(g))}" onchange="edits(${n}).title=this.value;render()">
    <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:12px">
      <input value="${esc(priceOf(g))}" onchange="edits(${n}).price=this.value" style="width:58px;background:transparent;border:none;border-bottom:1px dashed #C4654A;font:500 16px Inter;padding:0 0 1px">
      <span style="font-size:11px;color:#9CA3AF">${g.pricing?.suggested_low?`$${g.pricing.suggested_low}–${g.pricing.suggested_high}`:"no comp data"}</span>
      <span style="font-size:11px;color:#C4654A;font-weight:500">${g.pricing?.comp_count||0} comps</span></div>
    <div style="display:flex;gap:8px;margin-bottom:12px">
      <div style="width:96px"><div class="microlabel">Size${fromProfile?` <span style="color:#C4654A;font-weight:500" title="From your sizing profile — not read from a tag">· profile</span>`:""}</div>
        <input class="field" style="border-color:${(size && !fromProfile)?"#E5E2DB":"#F0C89A"}" placeholder="e.g. M" value="${esc(size)}" onchange="factEdit(${n},'size',this.value)"></div>
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
        <span class="microlabel" style="margin:0">Format for</span>
        <span style="font:400 10px Inter;color:#9CA3AF">${PLAT_RULES[plat]||""}</span></div>
      <select onchange="setPlatform(${n}, this.value)" style="width:100%;background:#FAFAF7;border:1px solid #E5E2DB;border-radius:8px;font:500 13px Inter;color:#1A1A1A;padding:8px 10px;cursor:pointer">
        ${["poshmark","depop","ebay","vinted"].map(p => `<option value="${p}" ${p===plat?"selected":""}>${p[0].toUpperCase()+p.slice(1)}</option>`).join("")}
      </select>
      ${g.channel?.primary ? `<div style="font:400 10px Inter;color:#9CA3AF;margin-top:6px">Sellers often list items like this on ${esc(g.channel.primary[0].toUpperCase()+g.channel.primary.slice(1))}</div>` : ""}
    </div>
    <div style="position:relative;overflow:hidden;background:#fff;border:1px solid #E5E2DB;border-radius:8px;padding:11px;margin-bottom:10px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px">
        <span style="font:600 9px Inter;color:#9CA3AF;text-transform:uppercase;letter-spacing:.06em">Listing preview · ${esc(plat)} · plain text</span>
        <span style="cursor:pointer;font:500 10px Inter;color:${S.copied[n]?"#16A34A":"#6B7280"}" onclick="copyListing(${n})">${S.copied[n]?"✓ Copied":"Copy"}</span></div>
      <div style="font:600 12px Inter;line-height:1.35;margin-bottom:7px">${esc(displayTitle(g))}</div>
      <div style="font:400 11.5px Inter;color:#4B4B4B;line-height:1.5;white-space:pre-line">${esc(v.description||"")}\n\n👗 Try it on yourself: ${esc(tryonLink(g))}\n\n${esc((v.hashtags||[]).join(" "))}</div>
      ${S.busyCopy[n]?`<div style="position:absolute;inset:0;background:rgba(255,255,255,.74);display:flex;align-items:center;justify-content:center;gap:8px"><span class="spin" style="width:15px;height:15px;border-width:2px"></span><span style="font:500 11px Inter;color:#6B7280">updating copy…</span></div>`:""}
    </div>
    <div class="microlabel" style="margin:0 0 5px">Buyer try-on link</div>
    <div style="display:flex;align-items:center;gap:8px;background:#FAFAF7;border:1px solid #E5E2DB;border-radius:8px;padding:8px 10px;margin-bottom:12px">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" style="flex:none"><ellipse cx="12" cy="12" rx="10" ry="6.5" stroke="#6B7280" stroke-width="2.2"/><circle cx="12" cy="12" r="3" fill="#6B7280"/></svg>
      <span style="flex:1;min-width:0;font:11px ui-monospace,monospace;color:#6B7280;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${tryonLink(g)}</span>
      <span style="cursor:pointer;font:500 11px Inter;color:${S.copiedLink[n]?"#16A34A":"#C4654A"}" onclick="copyTryonLink(${n})">${S.copiedLink[n]?"Copied ✓":"Copy"}</span></div>
    ${flags?`<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">${flags}</div>`:""}
    <div style="display:flex;gap:8px;margin-top:auto">
      <button class="btn-ghost" onclick="downloadPhoto(${n})">Photo</button>
      <button class="btn" style="flex:1" onclick="approveListing(${n})">Approve & list</button>
    </div>
  </div>`;
}

loadBases();
loadInventory();
