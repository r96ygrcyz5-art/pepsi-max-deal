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
        "mode": "generic",
    },
    "Tesco": {
        "url": "https://www.tesco.com/groceries/en-GB/products/282774907",
        "mode": "tesco",
    },
    "Asda": {
        "url": "https://www.asda.com/groceries/product/sugar-free-diet-cola/pepsi-max-no-sugar-cola-cans",
        "mode": "generic",
    },
    "Ocado": {
        "url": "https://www.ocado.com/products/pepsi-max/376033011",
        "mode": "generic",
    },
    "Sainsbury's": {
        "url": "https://www.sainsburys.co.uk/groceries/browse/hot-drinks-soft-drinks-and-water/fizzy-drinks/cola/c%3A1019301",
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

    for match in re.findall(r"£\s*(\d{1,2}(?:\.\d{1,2})?)", text):
        try:
            value = float(match)

            if plausible_price(value):
                values.append(value)

        except ValueError:
            pass

    return values


def looks_like_24_pack(text):
    lower = text.lower()

    return (
        "pepsi max" in lower
        and (
            "24x330ml" in lower
            or "24 x 330ml" in lower
            or "24×330ml" in lower
            or "24 x 330 ml" in lower
        )
    )


async def extract_tesco(page):
    body = await page.locator("body").inner_text()

    if not looks_like_24_pack(body):
        return None, None, None

    lines = [x.strip() for x in body.splitlines() if x.strip()]

    for i, line in enumerate(lines):
        lower = line.lower()

        if (
            "pepsi max" in lower
            and "24" in lower
            and "330" in lower
        ):
            section = "\n".join(
                lines[max(0, i - 3):min(len(lines), i + 15)]
            )

            prices = money_values(section)

            if prices:
                return prices[0], None, None

    return None, None, None


async def extract_sainsburys(page):
    body = await page.locator("body").inner_text()

    lines = [x.strip() for x in body.splitlines() if x.strip()]

    for i, line in enumerate(lines):
        lower = line.lower()

        if (
            "pepsi max no sugar cola cans 24x330ml" in lower
            or "pepsi max no sugar cola cans 24 x 330ml" in lower
        ):
            section = "\n".join(
                lines[max(0, i - 4):min(len(lines), i + 12)]
            )

            prices = money_values(section)

            if len(prices) >= 2:
                nectar_price = min(prices)
                standard_price = max(prices)

                return (
                    standard_price,
                    nectar_price,
                    "Nectar",
                )

            if len(prices) == 1:
                return prices[0], None, None

    return None, None, None


async def extract_generic(page):
    body = await page.locator("body").inner_text()

    if not looks_like_24_pack(body):
        return None, None, None

    prices = money_values(body)

    if not prices:
        return None, None, None

    return prices[0], None, None


async def extract_price(page, mode):
    if mode == "tesco":
        return await extract_tesco(page)

    if mode == "sainsburys":
        return await extract_sainsburys(page)

    return await extract_generic(page)


async def main():
    with DATA.open("r", encoding="utf-8") as f:
        old_data = json.load(f)

    previous = {
        item["name"]: item
        for item in old_data.get("retailers", [])
    }

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        context = await browser.new_context(
            locale="en-GB",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
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

                await page.wait_for_timeout(6000)

                price, loyalty_price, loyalty_scheme = (
                    await extract_price(
                        page,
                        source["mode"],
                    )
                )

                if price is not None:
                    item["price"] = round(price, 2)
                    item["status"] = "verified"
                    item["checked"] = datetime.now(
                        timezone.utc
                    ).date().isoformat()

                    item.pop("error", None)

                    if loyalty_price is not None:
                        item["loyalty_price"] = round(
                            loyalty_price,
                            2,
                        )
                        item["loyalty_scheme"] = loyalty_scheme
                    else:
                        item.pop("loyalty_price", None)
                        item.pop("loyalty_scheme", None)

                else:
                    item["error"] = (
                        "Automated check failed: "
                        "Could not confidently identify "
                        "Pepsi Max 24-pack price"
                    )

            except Exception as exc:
                item["error"] = (
                    "Automated check failed: "
                    + str(exc)[:180]
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

    with DATA.open("w", encoding="utf-8") as f:
        json.dump(
            output,
            f,
            indent=2,
            ensure_ascii=False,
        )


if __name__ == "__main__":
    asyncio.run(main())
