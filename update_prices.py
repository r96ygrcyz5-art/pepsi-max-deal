import json
import os
import re
import urllib.error
import urllib.parse
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


def load_existing():
    try:
        with DATA.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "product": {
                "name": "Pepsi Max",
                "pack": "24 × 330ml",
                "units": 24
            },
            "generated_at": "",
            "retailers": []
        }


def promo_price(text):
    if not text:
        return None

    match = re.search(
        r"£\s*(\d+(?:\.\d{1,2})?)",
        str(text)
    )

    if not match:
        return None

    return float(match.group(1))


def run_apify():
    if not APIFY_TOKEN:
        raise RuntimeError(
            "APIFY_TOKEN is missing from GitHub Actions secrets."
        )

    query = urllib.parse.urlencode({
        "maxTotalChargeUsd": "0.10",
        "maxItems": "1"
    })

    url = f"{ACTOR_URL}?{query}"

    payload = {
        "productUrls": [PRODUCT_URL],
        "maxResults": 1
    }

    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {APIFY_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=300
        ) as response:

            raw = response.read().decode("utf-8")

    except urllib.error.HTTPError as error:
        error_body = error.read().decode(
            "utf-8",
            errors="replace"
        )

        print("")
        print("========== APIFY ERROR ==========")
        print(f"HTTP status: {error.code}")
        print(error_body)
        print("=================================")
        print("")

        raise RuntimeError(
            f"Apify returned HTTP {error.code}. "
            "See APIFY ERROR above."
        ) from error

    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Could not contact Apify: {error}"
        ) from error

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as error:
        print("Unexpected Apify response:")
        print(raw[:2000])
        raise RuntimeError(
            "Apify did not return valid JSON."
        ) from error

    return result


def build_retailers(item, old_data):
    today = datetime.now(timezone.utc).date().isoformat()

    old_retailers = {
        r.get("name"): r
        for r in old_data.get("retailers", [])
        if r.get("name")
    }

    results = []

    trolley_retailers = item.get("retailers", [])

    for retailer in trolley_retailers:
        name = retailer.get("name")

        if name not in WANTED_RETAILERS:
            continue

        shelf_price = retailer.get("price")
        offer_text = retailer.get("promoText")

        special_price = promo_price(offer_text)

        # If Trolley gives a member/promotion price such as
        # "£8.50 NECTAR", use it as the deal price.
        deal_price = (
            special_price
            if special_price is not None
            else shelf_price
        )

        if deal_price is None:
            continue

        results.append({
            "name": name,
            "price": float(deal_price),
            "regular_price": (
                float(shelf_price)
                if shelf_price is not None
                else None
            ),
            "offer": offer_text or "",
            "status": "verified",
            "checked": today,
            "url": retailer.get("outboundUrl") or "",
            "error": ""
        })

    found = {r["name"] for r in results}

    # Preserve an old value rather than deleting a supermarket
    # if Trolley temporarily doesn't return it.
    for name in WANTED_RETAILERS:
        if name in found:
            continue

        old = old_retailers.get(name)

        if old:
            preserved = dict(old)
            preserved["status"] = "stale"
            preserved["error"] = (
                "Retailer not returned by today's Trolley check."
            )
            results.append(preserved)
        else:
            results.append({
                "name": name,
                "price": None,
                "regular_price": None,
                "offer": "",
                "status": "stale",
                "checked": "",
                "url": "",
                "error": (
                    "Retailer not returned by today's Trolley check."
                )
            })

    results.sort(
        key=lambda r: (
            r.get("price") is None,
            r.get("price")
            if r.get("price") is not None
            else 999
        )
    )

    return results


def main():
    old_data = load_existing()

    print("Starting MaxDeal Pepsi Max price update...")
    print("Calling Trolley scraper through Apify...")
    print("Maximum charge for this run: $0.10")

    items = run_apify()

    if not isinstance(items, list) or not items:
        raise RuntimeError(
            "Apify completed but returned no product."
        )

    item = items[0]

    product_name = str(item.get("productName", ""))

    if (
        "pepsi" not in product_name.lower()
        or "24" not in product_name.lower()
        or "330" not in product_name.lower()
    ):
        raise RuntimeError(
            "Apify returned an unexpected product: "
            f"{product_name}"
        )

    retailers = build_retailers(item, old_data)

    output = {
        "product": {
            "name": "Pepsi Max",
            "pack": "24 × 330ml",
            "units": 24
        },
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": "Trolley.co.uk via Apify",
        "retailers": retailers
    }

    DATA.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with DATA.open(
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            output,
            f,
            indent=2,
            ensure_ascii=False
        )

    print("")
    print("Price update successful:")
    for retailer in retailers:
        print(
            retailer["name"],
            retailer.get("price"),
            retailer.get("offer", "")
        )


if __name__ == "__main__":
    main()
