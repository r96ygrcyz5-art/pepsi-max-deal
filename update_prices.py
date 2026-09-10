import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("data/prices.json")

APIFY_TOKEN = os.environ.get("APIFY_TOKEN")

ACTOR_URL = (
    "https://api.apify.com/v2/acts/"
    "studio-amba~trolley-scraper/"
    "run-sync-get-dataset-items"
)

PRODUCT_URL = "https://www.trolley.co.uk/product/pepsi-max/IAI691"

WANTED_RETAILERS = {
    "Morrisons",
    "Tesco",
    "Asda",
    "Sainsbury's",
    "Iceland",
}


def extract_promo_price(text):
    if not text:
        return None

    match = re.search(
        r"£\s*(\d+(?:\.\d{1,2})?)",
        text,
    )

    if not match:
        return None

    try:
        return round(float(match.group(1)), 2)
    except ValueError:
        return None


def call_apify():
    if not APIFY_TOKEN:
        raise RuntimeError(
            "APIFY_TOKEN environment variable is missing"
        )

    payload = {
        "productUrls": [PRODUCT_URL],
        "maxResults": 1,
    }

    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        ACTOR_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {APIFY_TOKEN}",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=180,
    ) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def build_retailer(item, today):
    name = item.get("name")

    shelf_price = item.get("price")
    promo_text = item.get("promoText")

    promo_price = extract_promo_price(promo_text)

    price = shelf_price
    regular_price = None
    offer = ""

    if promo_price is not None:
        if shelf_price is not None:
            regular_price = round(
                float(shelf_price),
                2,
            )

        price = promo_price
        offer = promo_text or ""

    if price is not None:
        price = round(float(price), 2)

    result = {
        "name": name,
        "price": price,
        "currency": "GBP",
        "status": "verified",
        "offer": offer,
        "checked": today,
        "url": item.get("outboundUrl", ""),
    }

    if regular_price is not None:
        result["regular_price"] = regular_price

    price_per_unit = item.get("pricePerUnit")

    if price_per_unit:
        result["price_per_unit"] = price_per_unit

    return result


def main():
    today = datetime.now(
        timezone.utc
    ).date().isoformat()

    data = call_apify()

    if not isinstance(data, list) or not data:
        raise RuntimeError(
            "Apify returned no Pepsi Max data"
        )

    product = data[0]

    retailers = product.get("retailers", [])

    results = []

    for retailer in retailers:
        if retailer.get("name") not in WANTED_RETAILERS:
            continue

        results.append(
            build_retailer(
                retailer,
                today,
            )
        )

    if not results:
        raise RuntimeError(
            "No expected supermarket prices were returned"
        )

    results.sort(
        key=lambda x: (
            x["price"]
            if x["price"] is not None
            else 999
        )
    )

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

    DATA.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with DATA.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            output,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print("Updated Pepsi Max prices:")

    for retailer in results:
        print(
            retailer["name"],
            retailer["price"],
            retailer.get("offer", ""),
        )


if __name__ == "__main__":
    main()
