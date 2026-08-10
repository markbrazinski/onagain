/* Test-mode replay shim. Activated by ?test=1 (or #test).
 *
 * Intercepts window.fetch and answers the seller UI from the pre-baked golden bundle
 * (/api/test/*) instead of the live pipeline — zero API spend, deterministic, and the
 * generate/poll timing respects timings.json so the processing animation feels real.
 *
 * ponytail: a shim over the real UI, not a second UI. The app runs unchanged; only the
 * network layer is swapped. Translates golden listings into the shapes app.js expects.
 */
(function () {
  const params = new URLSearchParams(location.search);
  if (params.get("test") !== "1" && location.hash !== "#test") return;

  const realFetch = window.fetch.bind(window);
  let LISTINGS = {}, TIMINGS = {}, GIDS = [];
  const ready = (async () => {
    LISTINGS = await (await realFetch("/api/test/listings")).json().then(d => keyById(d.listings));
    TIMINGS = await (await realFetch("/api/test/timings")).json();
    GIDS = Object.keys(LISTINGS);
  })();

  function keyById(list) { const o = {}; (list || []).forEach(v => o[v.garment_id] = v); return o; }
  const json = (obj) => new Response(JSON.stringify(obj), { headers: { "Content-Type": "application/json" } });
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  // golden listing -> the garment shape app.js's review screen renders
  function garmentShape(gid, i) {
    const v = LISTINGS[gid];
    return {
      garment_number: i + 1,
      garment_id: gid,
      type: v.type,
      crop_url: `/api/test/crop/${gid}`,
      identity: { type: v.type, brand: v.brand, color: null, visible_size: v.size, size: v.size },
      vto: { best_url: `/api/test/render/${gid}`, ranking_reason: "golden render" },
      pricing: {
        suggested_low: v.price_low, suggested_mid: v.price, suggested_high: v.price_high,
        comp_count: v.comp_count, reasoning: `${v.comp_count} comps (demo)`,
      },
      channel: { primary: v.platform, primary_reasoning: "Demo channel" },
      copy: {
        variants: [
          { style: "keyword", title: v.title, description: v.description, hashtags: v.hashtags },
          { style: "lifestyle", title: v.title, description: v.description, hashtags: v.hashtags },
        ],
        flags: {},
      },
    };
  }

  // pace generate/poll to the recorded per-garment durations, but compressed for demo sanity
  const SPEED = 12;                       // replay 12x faster than real bake (7min -> ~35s)
  let genStartedAt = 0;

  window.fetch = async function (url, opts) {
    await ready;
    const u = (typeof url === "string" ? url : url.url) || "";
    const path = u.replace(location.origin, "");

    // parse -> the golden split
    if (path === "/api/parse" && opts?.method === "POST") {
      await sleep((TIMINGS.parse_s || 3) * 1000 / SPEED);
      return json({
        batch_id: "golden", gate: { pass: true, reason: "Demo replay — clean split" },
        source_url: "/api/test/source",
        garments: GIDS.map((gid, i) => ({
          garment_number: i + 1, garment_id: gid, type: LISTINGS[gid].type,
          crop_url: `/api/test/crop/${gid}`,
        })),
      });
    }

    // inventory -> baked listings (adds buy_url + hero url the inventory cards want)
    if (path === "/api/inventory") {
      return json({
        listings: GIDS.map(gid => {
          const v = LISTINGS[gid];
          return { ...v, hero_photo: `/api/test/render/${gid}`, status: "listed",
                   tryon_count: v.comp_count, created_at: "2026-08-10",
                   buy_url: "https://" + v.platform + ".com" };
        }),
      });
    }

    // generate -> just acknowledge; poll drives the timed reveal
    if (/\/api\/batch\/golden\/generate$/.test(path) && opts?.method === "POST") {
      genStartedAt = Date.now();
      return json({ status: "started", base: "mannequin" });
    }

    // batch poll -> reveal garments progressively per recorded timings, then mark done
    if (/\/api\/batch\/golden$/.test(path)) {
      const elapsed = (Date.now() - genStartedAt) / 1000 * SPEED;   // real-equivalent seconds
      let acc = 0, doneCount = 0;
      (TIMINGS.per_garment || []).forEach(t => { acc += t.seconds; if (elapsed >= acc) doneCount++; });
      const garments = GIDS.map((gid, i) => {
        const g = garmentShape(gid, i);
        if (i >= doneCount) { g.vto = null; g.pricing = null; g.copy = null; }  // not "done" yet
        return g;
      });
      const total = (TIMINGS.per_garment || []).reduce((s, t) => s + t.seconds, 0);
      return json({ batch_id: "golden", status: elapsed >= total ? "done" : "processing", garments });
    }

    // approve/regen -> no-op success in demo (nothing to persist; links already stable)
    if (/\/api\/batch\/golden\/garment\/\d+\/(approve|regen_copy|regen_image)$/.test(path)) {
      const m = path.match(/garment\/(\d+)\//);
      const g = garmentShape(GIDS[Number(m[1]) - 1], Number(m[1]) - 1);
      return json({ status: "listed", garment_id: g.garment_id, tryon_url: `/tryon/${g.garment_id}`,
                    copy: g.copy, identity: g.identity, channel: g.channel,
                    vto: { best_url: g.vto.best_url, ranking_reason: g.vto.ranking_reason } });
    }

    // bases (for the picker)
    if (path === "/api/bases") {
      return json({ bases: [{ name: "mannequin", url: "/api/base/mannequin" },
                            { name: "mannequin-male", url: "/api/base/mannequin-male" }] });
    }

    // everything else (images: /api/test/*, /api/base/*, /tryon page) -> real fetch
    return realFetch(url, opts);
  };

  // banner so it's obvious we're in replay mode
  window.addEventListener("DOMContentLoaded", () => {
    const b = document.createElement("div");
    b.textContent = "TEST MODE — replaying golden demo (no API calls)";
    b.style.cssText = "position:fixed;bottom:0;left:0;right:0;background:#1A1A1A;color:#F5C518;" +
      "font:600 12px Inter;text-align:center;padding:6px;z-index:9999;letter-spacing:.02em";
    document.body.appendChild(b);
  });
})();
