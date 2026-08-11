"""Playwright interaction tests for the comps drawer: click-open, isolation, apply, focus."""
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
P, F = 0, 0
def ok(name, cond):
    global P, F
    if cond: P += 1; print("OK", name)
    else: F += 1; print("FAIL", name)

def to_review(page):
    page.goto(f"{BASE}/?replay=1"); page.wait_for_timeout(1500)
    page.evaluate("go('upload')"); page.wait_for_timeout(400)
    page.evaluate("""()=>{const f=new File([new Uint8Array([255,216,255])],'d.jpg',{type:'image/jpeg'});doParse(f);}""")
    page.wait_for_timeout(3000)
    page.evaluate("generate()"); page.wait_for_timeout(14000)
    page.wait_for_selector("text=Review listings", timeout=6000)

with sync_playwright() as pw:
    b = pw.chromium.launch(); page = b.new_page(viewport={"width":1440,"height":900})
    to_review(page)

    # 1. no hover needed — click "Review N comps" on the cardigan (rcard-2) opens the drawer
    page.click("#rcard-2 button:has-text('Review')")
    page.wait_for_timeout(400)
    ok("click opens drawer (no hover)", page.query_selector("[role=dialog]") is not None)

    # 2. drawer shows the CARDIGAN's evidence: count 6, median 19.49, range 11–42
    body = page.inner_text("[role=dialog]").lower()
    ok("summary shows 6 sold listings", "6" in body and "sold listings" in body)
    ok("median 19.49 shown", "$19.49" in body)
    ok("range $11–$42 shown", "$11–$42" in body or ("$11" in body and "$42" in body))
    aria = page.get_attribute("[role=dialog]", "aria-label") or ""
    ok("drawer names the cardigan", "cardigan" in body or "cardigan" in aria.lower())

    # 3. six rows present
    rows = page.query_selector_all("[role=dialog] >> text=/eBay|Mercari|Poshmark/")
    ok("marketplace badges present (>=6)", len(rows) >= 6)

    # 4. apply a new price -> updates ONLY the cardigan
    page.fill("#drawer-price", "33")
    dress_before = page.inner_text("#rcard-3")     # olive dress card (unchanged expected)
    page.click("[role=dialog] button:has-text('Apply price')")
    page.wait_for_timeout(500)
    ok("drawer closes after apply", page.query_selector("[role=dialog]") is None)
    # price lives in the card's <input> value (not text content)
    card2_price = page.eval_on_selector("#rcard-2 input[aria-label='Suggested price']", "el => el.value")
    ok("cardigan price applied ($33)", "33" in card2_price)
    dress_after = page.inner_text("#rcard-3")
    ok("adjacent dress card unchanged", dress_before.strip() == dress_after.strip())

    # 5. Escape closes; focus returns to the trigger
    page.click("#rcard-2 button:has-text('Review')"); page.wait_for_timeout(300)
    ok("reopened drawer reflects saved price ($33)", "33" in page.inner_text("[role=dialog]"))
    page.keyboard.press("Escape"); page.wait_for_timeout(300)
    ok("Escape closes drawer", page.query_selector("[role=dialog]") is None)
    focused = page.evaluate("() => document.activeElement && document.activeElement.textContent")
    ok("focus returns to Review button", focused and "Review" in focused)

    # 6. cancel (close without apply) preserves price
    page.click("#rcard-2 button:has-text('Review')"); page.wait_for_timeout(300)
    page.fill("#drawer-price", "999")
    page.click("[role=dialog] button[aria-label=Close]"); page.wait_for_timeout(300)
    final_price = page.eval_on_selector("#rcard-2 input[aria-label='Suggested price']", "el => el.value")
    ok("cancel does NOT change price (still 33)", "33" in final_price and "999" not in final_price)

    b.close()

print(f"\n{P} passed, {F} failed")
import sys; sys.exit(1 if F else 0)
