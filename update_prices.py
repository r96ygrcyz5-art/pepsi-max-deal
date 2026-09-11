import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DATA = Path("data/prices.json")

APIFY_TOKEN = os.environ.get("APIFY_TOKEN")

ACTOR_ID = "studio-amba~trolley-scraper"

PRODUCT_URL = "https://www.trolley.co.uk/product/pepsi-max/IAI691"

WANTED_RETAILERS = {
    "Morrisons",
    "Tesco",
    "Asda",
    "Sainsbury's",
    "Iceland",
}


def api_request(url, method="GET", payload=None):
    headers = {
        "Authorization": f"Bearer {APIFY_TOKEN}",
        "Accept": "application/json",
    }

    data = None

    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=headers,
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=120
        ) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)

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
            f"Apify returned HTTP {error.code}."
        ) from error


def load_existing():
    try:
        with DATA.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "product": {
                "name": "Pepsi Max",
                "pack": "24 × 330ml",
                "units": 24,
            },
            "generated_at": "",
            "retailers": [],
        }


def extract_promo_price(text):
    if not text:
        return None

    match = re.search(
        r"£\s*(\d+(?:\.\d{1,2})?)",
        str(text)
    )

    if not match:
        return None

    return float(match.group(1))


def start_actor():
    if not APIFY_TOKEN:
        raise RuntimeError(
            "APIFY_TOKEN is missing from GitHub Actions secrets."
        )

    query = urllib.parse.urlencode({
        "maxTotalChargeUsd": "0.10",
        "maxItems": "1",
    })

    url = (
        f"https://api.apify.com/v2/acts/"
        f"{ACTOR_ID}/runs?{query}"
    )

    payload = {
        "productUrls": [PRODUCT_URL],
        "maxResults": 1,
    }

    print("Starting Trolley Actor...")
    print("Maximum charge for this run: $0.10")

    response = api_request(
        url,
        method="POST",
        payload=payload,
    )

    run = response.get("data", {})

    run_id = run.get("id")

    if not run_id:
        raise RuntimeError(
            "Apify did not return a run ID."
        )

    print(f"Actor started. Run ID: {run_id}")

    return run_id


def wait_for_actor(run_id):
    url = (
        f"https://api.apify.com/v2/"
        f"actor-runs/{run_id}"
    )

    for attempt in range(36):
        response = api_request(url)

        run = response.get("data", {})

        status = run.get("status")

        print(
            f"Actor status: {status} "
            f"(check {attempt + 1})"
        )

        if status == "SUCCEEDED":
            dataset_id = run.get(
                "defaultDatasetId"
            )

            if not dataset_id:
                raise RuntimeError(
                    "Actor succeeded but no dataset ID was returned."
                )

            return dataset_id

        if status in {
            "FAILED",
            "ABORTED",
            "TIMED-OUT",
        }:
            raise RuntimeError(
                f"Actor finished with status: {status}"
            )

        time.sleep(5)

    raise RuntimeError(
        "Actor did not finish within 3 minutes."
    )


def get_dataset(dataset_id):
    url = (
        f"https://api.apify.com/v2/"
        f"datasets/{dataset_id}/items"
        "?clean=true&format=json"
    )

    items = api_request(url)

    if not isinstance(items, list):
        raise RuntimeError(
            "Dataset response was not a list."
        )

    return items


def build_retailers(item, old_data):
    today = datetime.now(
        timezone.utc
    ).date().isoformat()

    old_retailers = {
        retailer.get("name"): retailer
        for retailer in old_data.get(
            "retailers",
            []
        )
        if retailer.get("name")
    }

    results = []

    for retailer in item.get(
        "retailers",
        []
    ):
        name = retailer.get("name")

        if name not in WANTED_RETAILERS:
            continue

        shelf_price = retailer.get("price")
        offer_text = retailer.get("promoText")

        promo = extract_promo_price(
            offer_text
        )

        effective_price = (
            promo
            if promo is not None
            else shelf_price
        )

        if effective_price is None:
            continue

        results.append({
            "name": name,
            "price": float(effective_price),
            "regular_price": (
                float(shelf_price)
                if shelf_price is not None
                else None
            ),
            "offer": offer_text or "",
            "status": "verified",
            "checked": today,
            "url": (
                retailer.get("outboundUrl")
                or ""
            ),
            "error": "",
        })

    found = {
        retailer["name"]
        for retailer in results
    }

    for name in WANTED_RETAILERS:
        if name in found:
            continue

        old = old_retailers.get(name)

        if old:
            preserved = dict(old)
            preserved["status"] = "stale"
            preserved["error"] = (
                "Retailer not returned by "
                "today's Trolley check."
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
                    "Retailer not returned by "
                    "today's Trolley check."
                ),
            })

    results.sort(
        key=lambda retailer: (
            retailer.get("price") is None,
            retailer.get("price")
            if retailer.get("price") is not None
            else 999,
        )
    )

    return results


def main():
    old_data = load_existing()

    print(
        "Starting MaxDeal Pepsi Max "
        "price update..."
    )

    run_id = start_actor()

    dataset_id = wait_for_actor(run_id)

    print(
        f"Retrieving dataset: {dataset_id}"
    )

    items = get_dataset(dataset_id)

    if not items:
        raise RuntimeError(
            "Actor succeeded but returned no products."
        )

    item = items[0]

    product_name = str(
        item.get("productName", "")
    )

    if (
        "pepsi" not in product_name.lower()
        or "24" not in product_name.lower()
        or "330" not in product_name.lower()
    ):
        raise RuntimeError(
            "Unexpected product returned: "
            f"{product_name}"
        )

    retailers = build_retailers(
        item,
        old_data,
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
        "source": "Trolley.co.uk via Apify",
        "retailers": retailers,
    }

    DATA.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

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

    print("")
    print("SUCCESS — prices updated")
    print("")

    for retailer in retailers:
        print(
            retailer["name"],
            retailer.get("price"),
            retailer.get("offer", ""),
        )


if __name__ == "__main__":
    main()
