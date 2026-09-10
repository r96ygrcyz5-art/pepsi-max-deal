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

    for match in re.findall(r"£\s*(\d{1,2}(?:\.\d{1,2})?)", text):
        try:
            value = float(match)
            if plausible_price(value):
                values.append(value)
        except ValueError:
            pass

    return values


def looks_like_24_pack(text):
    text = text.lower()

    has_pepsi = "pepsi" in text and "max" in text

    pack_patterns = [
        r"24\s*[x×]\s*330\s*ml",
        r"24\s*x\s*330",
        r"24x330",
        r"24\s*pack",
        r"24\s*cans",
    ]

    has_pack = any(re.search(pattern, text) for pattern in pack_patterns)

    return has_pepsi and has_pack


async def extract_tesco(page):
    # Tesco often exposes useful product information in rendered text
    # even when the underlying page structure changes.
    body = await page.locator("body").inner_text()

    if not looks_like_24_pack(body):
        return None, ""

    lines = [line.strip() for line in body.splitlines() if line.strip()]

    product_indexes = []

    for i, line in enumerate(lines):
        lower = line.lower()

        if (
            "pepsi" in lower
            and "max" in lower
            and (
                "24" in lower
                or "330ml" in lower
                or "330 ml" in lower
            )
        ):
            product_indexes.append(i)

    candidates = []

    for index in product_indexes:
        start = max(0, index - 3)
        end = min(len(lines), index + 12)

        section = "\n".join(lines[start:end])

        for price in money_values(section):
            candidates.append(price)

    if candidates:
        # For a single 24-pack, the first sensible product-area price
        # is normally the standard/current selling price.
        return candidates[0], ""

    # Tesco sometimes embeds JSON price data in the page source.
    html = await page.content()

    patterns = [
        r'"price"\s*:\s*"?(\d{1,2}\.\d{2})"?',
        r'"currentPrice"\s*:\s*"?(\d{1,2}\.\d{2})"?',
        r'"actualPrice"\s*:\s*"?(\d{1,2}\.\d{2})"?',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, html, re.I)

        for match in matches:
            try:
                value = float(match)

                if plausible_price(value):
                    return value, ""
            except ValueError:
                pass

    return None, ""


async def extract_generic(page):
    body = await page.locator("body").inner_text()

    if not looks_like_24_pack(body):
        return None, ""

    prices = money_values(body)

    if not prices:
        return None, ""

    return prices[0], ""


async def extract_price(page, mode):
    if mode == "tesco":
        return await extract_tesco(page)

    return await extract_generic(page)


async def main():
    if DATA.exists():
        with DATA.open("r", encoding="utf-8") as f:
            old_data = json.load(f)
    else:
        old_data = {
            "product": {
                "name": "Pepsi Max",
                "pack": "24 × 330ml",
                "units": 24,
            },
            "retailers": [],
        }

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
            print(f"Checking {name}...")

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

            page = await context.new_page()

            try:
                await page.goto(
                    source["url"],
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                await page.wait_for_timeout(6000)

                price, offer = await extract_price(
                    page,
                    source["mode"],
                )

                if price is not None:
                    item["price"] = round(price, 2)
                    item["status"] = "verified"
                    item["offer"] = offer
                    item["checked"] = datetime.now(
                        timezone.utc
                    ).date().isoformat()

                    print(f"{name}: £{price:.2f}")

                else:
                    item["error"] = (
                        "Automated check failed: "
                        "Could not confidently identify "
                        "Pepsi Max 24-pack price"
                    )

                    print(f"{name}: no verified price")

            except Exception as exc:
                item["error"] = (
                    "Automated check failed: "
                    + str(exc)[:180]
                )

                print(f"{name}: {exc}")

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

    DATA.parent.mkdir(parents=True, exist_ok=True)

    with DATA.open("w", encoding="utf-8") as f:
        json.dump(
            output,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print("Finished updating prices.")


if __name__ == "__main__":
    asyncio.run(main())
