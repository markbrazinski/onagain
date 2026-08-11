/* Comps-drawer stat invariants. The summary MUST derive from the same rows shown, so it
 * can never contradict the evidence. Run: node tests/test_comps_drawer.mjs */

// mirror of the app.js helpers (kept in sync — same formulas)
const fmtUSD = v => (v == null || isNaN(+v)) ? "—"
  : "$" + (Number.isInteger(+v) ? String(+v) : (+v).toFixed(2));
function compStats(comps){
  const prices = (comps || []).map(c => +c.price).filter(p => !isNaN(p)).sort((a,b)=>a-b);
  if(!prices.length) return null;
  const n = prices.length;
  const median = n % 2 ? prices[(n-1)/2] : (prices[n/2-1] + prices[n/2]) / 2;
  return { count: n, min: prices[0], max: prices[n-1], median };
}

let pass = 0, fail = 0;
const ok = (name, cond) => { if(cond){ pass++; console.log("OK", name); } else { fail++; console.error("FAIL", name); } };

// 1. canonical six -> count 6, median 19.49, range 11–42
const canon = [22.99, 42.00, 15.99, 37.95, 14.50, 11.00].map(price => ({ price }));
const s = compStats(canon);
ok("count is 6", s.count === 6);
ok("min is 11", s.min === 11);
ok("max is 42", s.max === 42);
ok("median is 19.49 (even n = mean of two middles)", s.median === 19.49);
ok("range label matches rows ($11–$42)", `${fmtUSD(s.min)}–${fmtUSD(s.max)}` === "$11–$42");

// 2. odd sample size -> exact middle
const odd = [10, 20, 30].map(price => ({ price }));
ok("odd median is middle (20)", compStats(odd).median === 20);

// 3. currency formatting: whole vs cents
ok("whole dollars format without cents", fmtUSD(30) === "$30");
ok("cents format with two places", fmtUSD(19.49) === "$19.49");
ok("null/NaN formats as em dash", fmtUSD(null) === "—" && fmtUSD("x") === "—");

// 4. empty comps -> null (no fake stats)
ok("no comps -> null stats", compStats([]) === null && compStats(undefined) === null);

// 5. non-numeric prices are ignored, not counted
ok("junk prices dropped from stats", compStats([{price:"n/a"},{price:12},{price:8}]).count === 2);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
