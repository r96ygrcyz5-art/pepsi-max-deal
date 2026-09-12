import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("data/prices.json")

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

        # Single-pack price is what somebody pays
        # if they only want one case.
        single_price = normal_price

        # Loyalty price can apply to one case,
        # so use it for the main comparison.
        if loyalty_price is not None:
            effective_price = loyalty_price
            deal_type = "loyalty"

        else:
            effective_price = single_price
            deal_type = "standard"

        # Multi-buy prices are kept separately.
        # They do NOT replace the single-pack price.
        multibuy_price = None

        if (
            promo_price is not None
            and store.get("promo_type") == "n_for_x"
        ):
            multibuy_price = promo_price

        retailer = {
            "name": name,
            "price": effective_price,
            "regular_price": normal_price,
            "was_price": pounds(
                store.get("was_price_pence")
            ),
            "loyalty_price": loyalty_price,
            "loyalty_scheme": (
                store.get("loyalty_scheme")
            ),
            "loyalty_ends": (
                store.get("loyalty_ends_at")
            ),
            "multibuy_price_each": (
                multibuy_price
            ),
            "offer": (
                store.get("promo_description")
                or ""
            ),
            "deal_type": deal_type,
            "checked": (
                store.get("checked_at")
            ),
            "url": (
                store.get("store_url")
                or ""
            ),
            "match_type": (
                store.get("match_type")
            ),
            "verified": bool(
                store.get("is_verified")
            ),
            "status": "verified",
            "error": "",
        }

        retailers.append(retailer)

    retailers.sort(
        key=lambda r: (
            r["price"] is None,
            r["price"]
            if r["price"] is not None
            else 999,
        )
    )

    if not retailers:
        raise RuntimeError(
            "BasketScout returned no matching supermarkets."
        )

    output = {
        "product": {
            "id": 23023,
            "name": "Pepsi Max",
            "pack": "24 × 330ml",
            "units": 24,
            "gtin": "4060800130754",
            "image_url": product.get(
                "image_url"
            ),
        },
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": "BasketScout",
        "retailers": retailers,
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
    print("SUCCESS — BasketScout prices updated")
    print("")

    for retailer in retailers:
        print(
            retailer["name"],
            retailer["price"],
            retailer["offer"],
        )


if __name__ == "__main__":
    main()
