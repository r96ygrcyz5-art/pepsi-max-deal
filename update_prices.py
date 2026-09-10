import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

DATA = Path("data/prices.json")

SOURCES = {
    "Morrisons": {
        "url": "https://groceries.morrisons.com/products/pepsi-max-no-sugar-cola-cans-24-x-330ml",
        "mode": "morrisons",
    },
    "Tesco": {
        "url": "https://www.tesco.com/groceries/en-GB/products/282774907",
        "mode": "tesco",
    },
    "Asda": {
        "url": "https://www.asda.com/groceries/product/sugar-free-diet-cola/pepsi-max-no-sugar-cola-cans",
        "mode": "asda",
    },
    "Ocado": {
        "url": "https://www.ocado.com/products/pepsi-max/376033011",
        "mode": "ocado",
    },
    "Sainsbury's": {
        "url": "https://www.sainsburys.co.uk/gol-ui/SearchResults/pepsi%20max%2024%20x%20330ml",
        "mode": "sainsburys",
    },
    "Iceland": {
        "url": "https://www.iceland.co.uk/p/pepsi-max-no-sugar-cola-cans-24-x-330ml/",
        "mode": "iceland",
    },
}


def plausible_price(value):
    return 5.0 <= value <= 25.0


def money_values(text):
    values = []

    for match in re.findall(
        r"£\s*(\d{1,2}(?:\.\d{1,2})?)",
        text,
        flags=re.I,
    ):
        try:
            value = float(match)

            if plausible_price(value):
                values.append(value)

        except ValueError:
            pass

    seen = set()
    unique = []

    for value in values:
        value = round(value, 2)

        if value not in seen:
            seen.add(value)
            unique.append(value)

    return unique


def looks_like_24_pack(text):
    lower = text.lower()

    has_pepsi = "pepsi" in lower and "max" in lower

    pack_patterns = [
        r"24\s*[x×]\s*330\s*ml",
        r"24x330ml",
        r"24\s*x\s*330",
        r"24\s*cans",
        r"24\s*pack",
    ]

    has_pack = any(
        re.search(pattern, lower, flags=re.I)
        for pattern in pack_patterns
    )

    return has_pepsi and has_pack


def extract_json_prices(text):
    patterns = [
        r'"price"\s*:\s*"?(\d{1,2}(?:\.\d{1,2})?)"?',
        r'"currentPrice"\s*:\s*"?(\d{1,2}(?:\.\d{1,2})?)"?',
        r'"actualPrice"\s*:\s*"?(\d{1,2}(?:\.\d{1,2})?)"?',
        r'"sellingPrice"\s*:\s*"?(\d{1,2}(?:\.\d{1,2})?)"?',
    ]

    values = []

    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.I):
            try:
                value = float(match)

                if plausible_price(value):
                    values.append(round(value, 2))

            except ValueError:
                pass

    seen = set()
    unique = []

    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)

    return unique


def first_likely_pack_price(values):
    preferred = [
        value
        for value in values
        if 7.0 <= value <= 20.0
    ]

    if preferred:
        return preferred[0]

    if values:
        return values[0]

    return None


async def page_text_and_html(page):
    body = await page.locator("body").inner_text()
    html = await page.content()

    return body, html


async def extract_tesco(page):
    body, html = await page_text_and_html(page)

    combined = body + "\n" + html

    prices = []

    prices.extend(extract_json_prices(combined))
    prices.extend(money_values(combined))

    seen = set()
    unique = []

    for price in prices:
        if price not in seen:
            seen.add(price)
            unique.append(price)

    likely = [
        price
        for price in unique
        if 9.0 <= price <= 15.0
    ]

    if likely:
        return likely[0], None, None, ""

    price = first_likely_pack_price(unique)

    if price is not None:
        return price, None, None, ""

    return None, None, None, ""


async def extract_morrisons(page):
    body, html = await page_text_and_html(page)

    combined = body + "\n" + html

    if not looks_like_24_pack(combined):
        return None, None, None, ""

    prices = money_values(body)

    if not prices:
        prices = extract_json_prices(html)

    price = first_likely_pack_price(prices)

    return price, None, None, ""


async def extract_asda(page):
    body, html = await page_text_and_html(page)

    combined = body + "\n" + html

    if not looks_like_24_pack(combined):
        return None, None, None, ""

    prices = money_values(body)

    if not prices:
        prices = extract_json_prices(html)

    price = first_likely_pack_price(prices)

    return price, None, None, ""


async def extract_ocado(page):
    body, html = await page_text_and_html(page)

    combined = body + "\n" + html

    if not looks_like_24_pack(combined):
        return None, None, None, ""

    prices = money_values(body)

    if not prices:
        prices = extract_json_prices(html)

    price = first_likely_pack_price(prices)

    offer = ""

    offer_match = re.search(
        r"(?:2\s*for|buy\s*2\s*for)\s*£\s*(\d{1,2}(?:\.\d{1,2})?)",
        combined,
        flags=re.I,
    )

    if offer_match:
        offer = "2 for £" + str(
            round(float(offer_match.group(1)), 2)
        )

    return price, None, None, offer


async def extract_sainsburys(page):
    body, html = await page_text_and_html(page)

    combined = body + "\n" + html

    if not looks_like_24_pack(combined):
        return None, None, None, ""

    prices = money_values(body)

    if not prices:
        prices = extract_json_prices(html)

    nectar_price = None

    nectar_patterns = [
        r"nectar[^£]{0,120}£\s*(\d{1,2}(?:\.\d{1,2})?)",
        r"£\s*(\d{1,2}(?:\.\d{1,2})?)[^£]{0,120}nectar",
    ]

    for pattern in nectar_patterns:
        match = re.search(
            pattern,
            combined,
            flags=re.I,
        )

        if match:
            try:
                value = float(match.group(1))

                if plausible_price(value):
                    nectar_price = round(value, 2)
                    break

            except ValueError:
                pass

    standard_candidates = [
        value
        for value in prices
        if nectar_price is None
        or abs(value - nectar_price) > 0.01
    ]

    standard_price = first_likely_pack_price(
        standard_candidates
    )

    if standard_price is None and prices:
        standard_price = first_likely_pack_price(prices)

    return (
        standard_price,
        nectar_price,
        "Nectar" if nectar_price is not None else None,
        "",
    )


async def extract_iceland(page):
    body, html = await page_text_and_html(page)

    combined = body + "\n" + html

    if not looks_like_24_pack(combined):
        return None, None, None, ""

    prices = money_values(body)

    if not prices:
        prices = extract_json_prices(html)

    price = first_likely_pack_price(prices)

    return price, None, None, ""


async def check_retailer(page, mode):
    if mode == "tesco":
        return await extract_tesco(page)

    if mode == "morrisons":
        return await extract_morrisons(page)

    if mode == "asda":
        return await extract_asda(page)

    if mode == "ocado":
        return await extract_ocado(page)

    if mode == "sainsburys":
        return await extract_sainsburys(page)

    if mode == "iceland":
        return await extract_iceland(page)

    return None, None, None, ""


async def main():
    with DATA.open("r", encoding="utf-8") as f:
        old_data = json.load(f)

    previous = {
        item["name"]: item
        for item in old_data.get("retailers", [])
    }

    today = datetime.now(
        timezone.utc
    ).date().isoformat()

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True
        )

        context = await browser.new_context(
            locale="en-GB",
            user_agent=(
                "Mozilla/5.0 "
                "(Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
        )

        for name, source in SOURCES.items():
            old = previous.get(name, {})

            item = {
                "name": name,
                "price": old.get("price"),
                "currency": "GBP",
                "status": "stale",
                "offer": old.get("offer", ""),
                "checked": old.get("checked", ""),
                "url": source["url"],
            }

            if "loyalty_price" in old:
                item["loyalty_price"] = old["loyalty_price"]

            if "loyalty_scheme" in old:
                item["loyalty_scheme"] = old["loyalty_scheme"]

            page = await context.new_page()

            try:
                await page.goto(
                    source["url"],
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                await page.wait_for_timeout(7000)

                (
                    price,
                    loyalty_price,
                    loyalty_scheme,
                    offer,
                ) = await check_retailer(
                    page,
                    source["mode"],
                )

                if price is not None:
                    item["price"] = round(
                        price,
                        2,
                    )

                    item["status"] = "verified"
                    item["checked"] = today

                    if offer:
                        item["offer"] = offer

                    item.pop("error", None)

                    if loyalty_price is not None:
                        item["loyalty_price"] = round(
                            loyalty_price,
                            2,
                        )

                        item["loyalty_scheme"] = (
                            loyalty_scheme
                        )

                    else:
                        item.pop(
                            "loyalty_price",
                            None,
                        )

                        item.pop(
                            "loyalty_scheme",
                            None,
                        )

                    print(
                        name,
                        "verified",
                        item["price"],
                    )

                else:
                    item["status"] = "stale"

                    item["error"] = (
                        "Automated check failed: "
                        "Could not confidently identify "
                        "Pepsi Max 24-pack price"
                    )

                    print(
                        name,
                        "stale",
                    )

            except Exception as exc:
                item["status"] = "stale"

                item["error"] = (
                    "Automated check failed: "
                    + str(exc)[:180]
                )

                print(
                    name,
                    "error",
                    exc,
                )

            finally:
                await page.close()

            results.append(item)

        await browser.close()

    output = {
        "product": {
            "name": "Pepsi Max",
            "pack": "24 × 330ml",
            "units": 24,
        },
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "retailers": results,
    }

    with DATA.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            output,
            f,
            indent=2,
            ensure_ascii=False,
        )


if __name__ == "__main__":
    asyncio.run(main())
