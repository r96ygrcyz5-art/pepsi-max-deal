import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright

DATA = Path("data/prices.json")

SOURCES = {
    "Morrisons": {
        "url": "https://groceries.morrisons.com/products/pepsi-max-no-sugar-cola-cans-24-x-330ml/111904213",
        "mode": "generic",
    },
    "Tesco": {
        "url": "https://www.tesco.com/shop/en-GB/products/282774907",
        "mode": "tesco",
    },
    "Asda": {
        "url": "https://www.asda.com/groceries/product/sugar-free-diet-cola/pepsi-max-no-sugar-cola-cans-24-x-330ml/5934876",
        "mode": "generic",
    },
    "Ocado": {
        "url": "https://www.ocado.com/products/pepsi-max/376033011",
        "mode": "generic",
    },
    "Sainsbury's": {
        "url": "https://www.sainsburys.co.uk/gol-ui/SearchResults/pepsi%20max%2024%20x%20330ml",
        "mode": "sainsburys",
    },
    "Iceland": {
        "url": "https://www.iceland.co.uk/p/pepsi-max-no-sugar-cola-cans-24-x-330ml/47908.html",
        "mode": "iceland",
    },
}


def plausible_price(value):
    return 5.0 <= value <= 25.0


def money_values(text):
    values = []
    for match in re.findall(r"£\s*(\d{1,2}(?:\.\d{1,2})?)", text):
        try:
            value = float(match)
            if plausible_price(value):
                values.append(value)
        except ValueError:
            pass
    return values


def extract_generic(text):
    patterns = [
        r'"price"\s*:\s*"?(\d{1,2}\.\d{2})"?',
        r'(?:actual price|now|price)[^£]{0,40}£\s*(\d{1,2}(?:\.\d{1,2})?)',
        r'£\s*(\d{1,2}(?:\.\d{1,2})?)',
    ]

    for pattern in patterns:
        values = []
        for match in re.findall(pattern, text, flags=re.I):
            try:
                value = float(match)
                if plausible_price(value):
                    values.append(value)
            except ValueError:
                pass

        if values:
            return min(values)

    return None


def extract_tesco(text):
    lower = text.lower()

    product_pos = lower.find("pepsi max no sugar cola cans 24x330ml")
    if product_pos == -1:
        product_pos = lower.find("pepsi max no sugar cola cans 24 x 330ml")

    if product_pos == -1:
        return None, None

    block = text[product_pos:product_pos + 1800]

    clubcard = re.search(
        r"£\s*(\d{1,2}(?:\.\d{1,2})?)\s*clubcard price",
        block,
        flags=re.I,
    )

    prices = money_values(block)

    loyalty = float(clubcard.group(1)) if clubcard else None

    standard = None
    for price in prices:
        if loyalty is None or abs(price - loyalty) > 0.01:
            if price >= 7:
                standard = price
                break

    return standard, loyalty


def extract_sainsburys(text):
    lower = text.lower()

    product_pos = lower.find("pepsi max")
    if product_pos == -1:
        return None, None

    block = text[product_pos:product_pos + 2200]

    nectar = re.search(
        r"(?:nectar price|with nectar)[^£]{0,80}£\s*(\d{1,2}(?:\.\d{1,2})?)",
        block,
        flags=re.I,
    )

    prices = money_values(block)

    loyalty = float(nectar.group(1)) if nectar else None

    standard = None
    for price in prices:
        if loyalty is None or abs(price - loyalty) > 0.01:
            if price >= 7:
                standard = price
                break

    return standard, loyalty


def extract_iceland(text):
    lower = text.lower()

    product_pos = lower.find("pepsi max no sugar cola cans 24")
    if product_pos == -1:
        product_pos = lower.find("pepsi max 24 x 330ml")

    if product_pos == -1:
        return None

    block = text[product_pos:product_pos + 1200]

    prices = money_values(block)

    if not prices:
        return None

    return prices[0]


def product_is_correct(text):
    lower = text.lower()

    has_pepsi = "pepsi max" in lower
    has_24 = "24x330ml" in lower or "24 x 330ml" in lower or "7920ml" in lower

    return has_pepsi and has_24


async def check_retailer(page, name, source):
    await page.goto(
        source["url"],
        wait_until="domcontentloaded",
        timeout=45000,
    )

    await page.wait_for_timeout(3500)

    body = await page.locator("body").inner_text()
    html = await page.content()
    combined = html + "\n" + body

    if not product_is_correct(combined):
        raise ValueError("Could not confidently identify Pepsi Max 24-pack")

    mode = source["mode"]

    if mode == "tesco":
        price, loyalty = extract_tesco(body)

    elif mode == "sainsburys":
        price, loyalty = extract_sainsburys(body)

    elif mode == "iceland":
        price = extract_iceland(body)
        loyalty = None

    else:
        price = extract_generic(combined)
        loyalty = None

    if price is None:
        raise ValueError("Could not confidently identify product price")

    return price, loyalty


async def main():
    data = json.loads(DATA.read_text())

    existing = {
        item["name"]: item
        for item in data.get("retailers", [])
    }

    today = datetime.now(timezone.utc).date().isoformat()

    async with async_playwright() as p:
        browser = await p.chromium.launch()

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                "AppleWebKit/605.1.15 Version/18.0 "
                "Mobile/15E148 Safari/604.1"
            )
        )

        results = []

        for name, source in SOURCES.items():
            item = existing.get(
                name,
                {
                    "name": name,
                    "currency": "GBP",
                    "offer": "",
                },
            )

            item["url"] = source["url"]

            page = await context.new_page()

            try:
                price, loyalty = await check_retailer(
                    page,
                    name,
                    source,
                )

                item["price"] = round(price, 2)
                item["status"] = "verified"
                item["checked"] = today

                item.pop("error", None)

                if loyalty is not None:
                    item["loyalty_price"] = round(loyalty, 2)

                    if name == "Tesco":
                        item["loyalty_scheme"] = "Clubcard"
                    elif name == "Sainsbury's":
                        item["loyalty_scheme"] = "Nectar"

                else:
                    item.pop("loyalty_price", None)
                    item.pop("loyalty_scheme", None)

            except Exception as exc:
                item["status"] = "stale"
                item["error"] = (
                    "Automated check failed: "
                    + str(exc)[:160]
                )

            finally:
                await page.close()

            results.append(item)

        await browser.close()

    data["retailers"] = results
    data["generated_at"] = datetime.now(timezone.utc).isoformat()

    DATA.write_text(
        json.dumps(data, indent=2) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
