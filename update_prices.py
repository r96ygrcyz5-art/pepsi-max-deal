import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PRICES_FILE = Path("data/prices.json")
HISTORY_FILE = Path("data/history.json")

BASKETSCOUT_URL = (
    "https://api.basketscout.co.uk/"
    "api/v1/products/23023"
)

WANTED_STORES = {
    "Morrisons",
    "Tesco",
    "Asda",
    "Sainsbury's",
    "Iceland",
    "Ocado",
}


def get_basketscout_data():
    request = urllib.request.Request(
        BASKETSCOUT_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "MaxDeal/0.1",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def pounds(pence):
    if pence is None:
        return None
    return round(pence / 100, 2)


def load_history():
    if not HISTORY_FILE.exists():
        return {
            "product_id": 23023,
            "snapshots": []
        }

    try:
        with HISTORY_FILE.open(
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except (json.JSONDecodeError, OSError):
        return {
            "product_id": 23023,
            "snapshots": []
        }


def main():

    print("Starting MaxDeal price update...")
    print("Source: BasketScout product 23023")

    product = get_basketscout_data()

    if product.get("id") != 23023:
        raise RuntimeError(
            "Unexpected BasketScout product returned."
        )

    if product.get("gtin") != "4060800130754":
        raise RuntimeError(
            "Product GTIN does not match Pepsi Max 24 x 330ml."
        )

    retailers = []

    for store in product.get("stores", []):

        name = store.get("store_name")

        if name not in WANTED_STORES:
            continue

        normal_price = pounds(
            store.get("price_pence")
        )

        loyalty_price = pounds(
            store.get("loyalty_price_pence")
        )

        promo_price = pounds(
            store.get("promo_price_pence")
        )

        if loyalty_price is not None:
            effective_price = loyalty_price
            deal_type = "loyalty"
        else:
            effective_price = normal_price
            deal_type = "standard"

        multibuy_price = None

        if (
            promo_price is not None
            and
            store.get("promo_type") == "n_for_x"
        ):
            multibuy_price = promo_price

        retailers.append({
            "name": name,
            "price": effective_price,
            "regular_price": normal_price,
            "was_price": pounds(
                store.get("was_price_pence")
            ),
            "loyalty_price": loyalty_price,
            "loyalty_scheme":
                store.get("loyalty_scheme"),
            "loyalty_ends":
                store.get("loyalty_ends_at"),
            "multibuy_price_each":
                multibuy_price,
            "offer":
                store.get("promo_description")
                or "",
            "deal_type": deal_type,
            "checked":
                store.get("checked_at"),
            "url":
                store.get("store_url")
                or "",
            "match_type":
                store.get("match_type"),
            "verified":
                bool(store.get("is_verified")),
            "status": "verified",
            "error": "",
        })

    retailers.sort(
        key=lambda r: (
            r["price"] is None,
            r["price"]
            if r["price"] is not None
            else 999
        )
    )

    if not retailers:
        raise RuntimeError(
            "BasketScout returned no matching supermarkets."
        )

    now = datetime.now(timezone.utc)

    output = {
        "product": {
            "id": 23023,
            "name": "Pepsi Max",
            "pack": "24 × 330ml",
            "units": 24,
            "gtin": "4060800130754",
            "image_url":
                product.get("image_url"),
        },
        "generated_at": now.isoformat(),
        "source": "BasketScout",
        "retailers": retailers,
    }

    PRICES_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with PRICES_FILE.open(
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            output,
            f,
            indent=2,
            ensure_ascii=False
        )

    # -------------------------
    # SAVE PRICE HISTORY
    # -------------------------

    history = load_history()

    today = now.date().isoformat()

    snapshot = {
        "date": today,
        "prices": {}
    }

    for retailer in retailers:
        snapshot["prices"][
            retailer["name"]
        ] = {
            "price":
                retailer["price"],
            "regular_price":
                retailer["regular_price"],
            "loyalty_price":
                retailer["loyalty_price"],
            "multibuy_price_each":
                retailer[
                    "multibuy_price_each"
                ],
        }

    # Only keep one snapshot per day.
    history["snapshots"] = [
        item
        for item in history.get(
            "snapshots", []
        )
        if item.get("date") != today
    ]

    history["snapshots"].append(
        snapshot
    )

    history["snapshots"].sort(
        key=lambda x: x.get(
            "date", ""
        )
    )

    HISTORY_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with HISTORY_FILE.open(
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            history,
            f,
            indent=2,
            ensure_ascii=False
        )

    print("")
    print(
        "SUCCESS — prices and history updated"
    )
    print(
        "History now contains",
        len(history["snapshots"]),
        "daily snapshot(s)"
    )
    print("")

    for retailer in retailers:
        print(
            retailer["name"],
            retailer["price"],
            retailer["offer"]
        )


if __name__ == "__main__":
    main()
