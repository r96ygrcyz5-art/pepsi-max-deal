"""
Daily Pepsi Max price updater.
Designed for GitHub Actions. It renders retailer pages with Playwright, extracts
JSON-LD / visible price text, validates the result, and writes data/prices.json.

Retailer pages change often. A failed extraction NEVER overwrites the previous
price; it marks that retailer stale instead.
"""
import asyncio, json, re
from datetime import datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright

DATA = Path("data/prices.json")

SOURCES = {
 "Morrisons": "https://groceries.morrisons.com/products/pepsi-max-no-sugar-cola-cans-24-x-330ml/111904213",
 "Tesco": "https://www.tesco.com/shop/en-GB/products/302624226",
 "Asda": "https://www.asda.com/groceries/product/sugar-free-diet-cola/pepsi-max-no-sugar-cola-cans-24-x-330ml/5934876",
 "Ocado": "https://www.ocado.com/products/pepsi-max/376033011",
}

PRICE_PATTERNS = [
 re.compile(r'"price"\s*:\s*"?(\d{1,2}\.\d{2})"?', re.I),
 re.compile(r'(?:actual price|now|price)[^£]{0,35}£\s*(\d{1,2}(?:\.\d{1,2})?)', re.I),
 re.compile(r'£\s*(\d{1,2}(?:\.\d{1,2})?)'),
]

def plausible(v):
    return 4.0 <= v <= 30.0

def extract_price(text):
    vals=[]
    for pat in PRICE_PATTERNS:
        for m in pat.findall(text):
            try:
                v=float(m)
                if plausible(v): vals.append(v)
            except: pass
        if vals:
            # Prefer a normal full-pack price; sale prices are usually the lowest
            # plausible headline price in the product page content.
            return min(vals)
    return None

async def main():
    data=json.loads(DATA.read_text())
    byname={x["name"]:x for x in data["retailers"]}
    today=datetime.now(timezone.utc).date().isoformat()
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        context=await browser.new_context(
          user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
        )
        for name,url in SOURCES.items():
            item=byname.get(name, {"name":name,"url":url})
            try:
                page=await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3500)
                text=await page.locator("body").inner_text()
                html=await page.content()
                price=extract_price(html+"\n"+text)
                title=(await page.title()).lower()
                product_ok=("pepsi" in title or "pepsi max" in text.lower()) and ("24" in text or "7920" in text)
                if not product_ok or price is None:
                    raise ValueError("Could not confidently identify product/price")
                item.update(price=price,status="verified",checked=today,url=url)
                item["offer"] = item.get("offer","")
                await page.close()
            except Exception as e:
                item["status"]="stale"
                item["error"]="Automated check failed: "+str(e)[:120]
            byname[name]=item
        await browser.close()
    data["retailers"]=list(byname.values())
    data["generated_at"]=datetime.now(timezone.utc).isoformat()
    DATA.write_text(json.dumps(data,indent=2)+"\n")

if __name__=="__main__":
    asyncio.run(main())
