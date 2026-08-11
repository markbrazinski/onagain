/* Verified-replay shim. Activated by ?replay=1 (or #replay).
 *
 * Intercepts window.fetch and answers the seller UI from the pre-baked golden bundle
 * (/api/replay/*) instead of the live pipeline — zero API spend, deterministic, and the
 * generate/poll timing respects timings.json so the processing animation feels real.
 *
 * ponytail: a shim over the real UI, not a second UI. The app runs unchanged; only the
 * network layer is swapped. Translates golden listings into the shapes app.js expects.
 */
(function () {
  const params = new URLSearchParams(location.search);
  if (params.get("replay") !== "1" && location.hash !== "#replay") return;

  const realFetch = window.fetch.bind(window);
  let LISTINGS = {}, TIMINGS = {}, GIDS = [], STARTER = null;
  const APPROVED = new Set();        // gids that have hit the save/approve action this session
  const ready = (async () => {
    LISTINGS = await (await realFetch("/api/replay/listings")).json().then(d => keyById(d.listings));
    TIMINGS = await (await realFetch("/api/replay/timings")).json();
    STARTER = await (await realFetch("/api/replay/starter")).json();
    GIDS = Object.keys(LISTINGS);
  })();

  function keyById(list) { const o = {}; (list || []).forEach(v => o[v.garment_id] = v); return o; }
  const json = (obj) => new Response(JSON.stringify(obj), { headers: { "Content-Type": "application/json" } });
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  // golden listing -> the garment shape app.js's review screen renders.
  // platform (optional) selects the baked per-platform copy so the swap shows real copy.
  function garmentShape(gid, i, platform) {
    const v = LISTINGS[gid];
    const plat = platform || v.platform;
    const c = (v.copy_by_platform && v.copy_by_platform[plat]) ||
              { title: v.title, description: v.description, hashtags: v.hashtags };
    return {
      garment_number: i + 1,
      garment_id: gid,
      type: v.type,
      crop_url: `/api/replay/crop/${gid}`,
      identity: { type: v.type, brand: v.brand, color: null, visible_size: v.size, size: v.size },
      vto: { best_url: `/api/replay/render/${gid}`, ranking_reason: "golden render" },
      pricing: {
        suggested_low: v.price_low, suggested_mid: v.price, suggested_high: v.price_high,
        comp_count: v.comp_count, reasoning: `${v.comp_count} comps (demo)`,
        comps: v.comps || [],
      },
      channel: { primary: plat, primary_reasoning: "Demo channel" },
      copy: {
        variants: [
          { style: "keyword", title: c.title, description: c.description, hashtags: c.hashtags },
          { style: "lifestyle", title: c.title, description: c.description, hashtags: c.hashtags },
        ],
        flags: {},
      },
    };
  }

  // pace generate/poll to the recorded per-garment durations, but compressed for demo sanity
  const SPEED = 18;                       // replay compression: ~10s of processing animation
  let genStartedAt = 0;

  window.fetch = async function (url, opts) {
    await ready;
    const u = (typeof url === "string" ? url : url.url) || "";
    const path = u.replace(location.origin, "");

    // parse -> the golden split. Hold ~2.5s so the "isolating garments" beat reads
    // (the real parse takes a few seconds; the compressed number would be too fast).
    if (path === "/api/parse" && opts?.method === "POST") {
      await sleep(2500);
      return json({
        batch_id: "golden", gate: { pass: true, reason: "Demo replay — clean split" },
        source_url: "/api/replay/source",
        garments: GIDS.map((gid, i) => ({
          garment_number: i + 1, garment_id: gid, type: LISTINGS[gid].type,
          box_pct: LISTINGS[gid].box_pct,          // orange bounding-box overlay
          crop_url: `/api/replay/crop/${gid}`,
        })),
      });
    }

    // inventory -> the one starter listing, PLUS any golden garments already approved
    // this session. New garments populate only on their save/approve action.
    if (path === "/api/inventory") {
      const out = [];
      if (STARTER && STARTER.garment_id) {
        out.push({ ...STARTER, hero_photo: "/api/replay/starter-hero", status: "listed" });
      }
      GIDS.filter(gid => APPROVED.has(gid)).forEach(gid => {
        const v = LISTINGS[gid];
        out.push({ ...v, hero_photo: `/api/replay/render/${gid}`, status: "listed",
                   tryon_count: 0, created_at: "2026-08-10",
                   buy_url: "https://" + v.platform + ".com" });
      });
      return json({ listings: out });
    }

    // generate -> just acknowledge; poll drives the timed reveal
    if (/\/api\/batch\/golden\/generate$/.test(path) && opts?.method === "POST") {
      genStartedAt = Date.now();
      return json({ status: "started", base: "mannequin" });
    }

    // batch poll -> replay the REAL per-step progression. Garments run 2-wide (like the
    // real ThreadPoolExecutor), each stepping identify->vto->price->copy on recorded times.
    if (/\/api\/batch\/golden$/.test(path)) {
      const elapsed = (Date.now() - genStartedAt) / 1000 * SPEED;   // real-equivalent seconds
      const per = TIMINGS.per_garment || [];
      const STEP_ORDER = ["identify", "vto", "price", "copy"];
      // per-garment duration = SUM of its clean step times (NOT t.seconds, which can be
      // inflated by a mid-bake stall). Lane model: 2 lanes, next garment starts on a free lane.
      const dur = t => STEP_ORDER.reduce((s, k) => s + ((t.steps || {})[k] || 0), 0);
      const laneFree = [0, 0];
      const startAt = per.map(t => {
        const lane = laneFree[0] <= laneFree[1] ? 0 : 1;
        const s = laneFree[lane];
        laneFree[lane] = s + dur(t);
        return s;
      });
      const garments = GIDS.map((gid, i) => {
        const g = garmentShape(gid, i);
        const steps = (per[i] || {}).steps || {};
        const local = elapsed - (startAt[i] || 0);                 // seconds into THIS garment
        const progress = {};
        let cursor = 0, allDone = true;
        for (const k of STEP_ORDER) {
          const d = steps[k] || 0;
          if (local <= cursor) { progress[k] = "wait"; allDone = false; }
          else if (local < cursor + d) { progress[k] = "active"; allDone = false; }
          else { progress[k] = "done"; }
          cursor += d;
        }
        if (local <= 0) STEP_ORDER.forEach(k => progress[k] = "wait");  // not started yet
        g.progress = progress;
        if (!allDone) { g.vto = null; g.pricing = null; g.copy = null; }  // reveal data only when done
        return g;
      });
      const total = Math.max(...startAt.map((s, i) => s + dur(per[i])), 0);
      return json({ batch_id: "golden", status: elapsed >= total ? "done" : "processing", garments });
    }

    // approve/regen -> success in demo (nothing to persist; links already stable).
    // regen_copy carries the chosen platform -> return that platform's baked copy.
    // On approve ONLY, mark the garment so it appears in inventory (populate-on-save).
    const gm = path.match(/\/api\/batch\/golden\/garment\/(\d+)\/(approve|regen_copy|regen_image)$/);
    if (gm) {
      let platform;
      try { platform = opts?.body && JSON.parse(opts.body).platform; } catch (e) {}
      const g = garmentShape(GIDS[Number(gm[1]) - 1], Number(gm[1]) - 1, platform);
      if (gm[2] === "approve") APPROVED.add(g.garment_id);
      return json({ status: "listed", garment_id: g.garment_id, tryon_url: `/tryon/${g.garment_id}`,
                    copy: g.copy, identity: g.identity, channel: g.channel,
                    vto: { best_url: g.vto.best_url, ranking_reason: g.vto.ranking_reason } });
    }

    // bases (for the picker)
    if (path === "/api/bases") {
      return json({ bases: [{ name: "mannequin", url: "/api/base/mannequin" },
                            { name: "mannequin-male", url: "/api/base/mannequin-male" }] });
    }

    // everything else (images: /api/replay/*, /api/base/*, /tryon page) -> real fetch
    return realFetch(url, opts);
  };

  // banner so it's obvious we're in replay mode
  window.addEventListener("DOMContentLoaded", () => {
    const b = document.createElement("div");
    b.textContent = "VERIFIED REPLAY — replaying golden demo (no API calls)";
    b.style.cssText = "position:fixed;bottom:0;left:0;right:0;background:#1A1A1A;color:#F5C518;" +
      "font:600 12px Inter;text-align:center;padding:6px;z-index:9999;letter-spacing:.02em";
    document.body.appendChild(b);
  });
})();
