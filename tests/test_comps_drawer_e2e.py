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

    # 2. decision block: suggested $22, count, NO conventional median in primary hierarchy
    body = page.inner_text("[role=dialog]")
    low = body.lower()
    ok("suggested price $22 shown", "$22" in body)
    ok("'Based on 6 comparable sold listings'", "6 comparable sold listings" in low)
    ok("conventional median $19.49 NOT in drawer", "19.49" not in body)
    ok("'Use $22' primary action present", page.query_selector("#use-suggested") is not None)
    aria = page.get_attribute("[role=dialog]", "aria-label") or ""
    ok("drawer names the cardigan", "cardigan" in low or "cardigan" in aria.lower())

    # 3. calculation block: ascending strip w/ $22.99 selected + honest explanation
    ok("selected candidate $22.99 highlighted", page.query_selector("[role=dialog] [aria-current='true']") is not None)
    sel_txt = page.inner_text("[role=dialog] [aria-current='true']")
    ok("highlighted value is $22.99", "22.99" in sel_txt)
    ok("explanation connects 22.99 -> $22", "22.99" in body and "$22 listing price" in body)

    # 4. six source rows w/ honest 'View search' links
    ok("6 source listings label", "6 source listings" in low)
    links = page.query_selector_all("[role=dialog] a")
    ok("source links say 'View search' (not sold-link)", any("view search" in (a.inner_text() or '').lower() for a in links))

    # 5. 'Use $22' applies to ONLY the cardigan, via canonical state path, flips to Applied
    dress_before = page.inner_text("#rcard-3")
    page.click("#use-suggested"); page.wait_for_timeout(300)
    ok("button flips to Applied ✓", "Applied" in page.inner_text("#use-suggested"))
    card2 = page.eval_on_selector("#rcard-2 input[aria-label='Suggested price']", "el => el.value")
    ok("cardigan price applied ($22)", "22" in card2)
    ok("adjacent dress card unchanged", dress_before.strip() == page.inner_text("#rcard-3").strip())
    page.click("[role=dialog] button[aria-label^='Close']"); page.wait_for_timeout(300)

    # 6. custom price path still works (secondary 'Apply')
    page.click("#rcard-2 button:has-text('Review')"); page.wait_for_timeout(300)
    page.fill("#drawer-price", "33"); page.click("[role=dialog] button:has-text('Apply')"); page.wait_for_timeout(400)
    ok("custom price applied ($33)", "33" in page.eval_on_selector("#rcard-2 input[aria-label='Suggested price']", "el=>el.value"))

    # 7. Escape closes + focus restore
    page.click("#rcard-2 button:has-text('Review')"); page.wait_for_timeout(300)
    page.keyboard.press("Escape"); page.wait_for_timeout(300)
    ok("Escape closes drawer", page.query_selector("[role=dialog]") is None)
    focused = page.evaluate("() => document.activeElement && document.activeElement.textContent")
    ok("focus returns to Review button", focused and "Review" in focused)

    # 8. cancel preserves price
    page.click("#rcard-2 button:has-text('Review')"); page.wait_for_timeout(300)
    page.fill("#drawer-price", "999")
    page.click("[role=dialog] button[aria-label^='Close']"); page.wait_for_timeout(300)
    final_price = page.eval_on_selector("#rcard-2 input[aria-label='Suggested price']", "el => el.value")
    ok("cancel does NOT change price (still 33)", "33" in final_price and "999" not in final_price)

    b.close()

print(f"\n{P} passed, {F} failed")
import sys; sys.exit(1 if F else 0)
