"""Check the RevenueCat connection (billing.py) against the real API.

    uv run python scripts/check_revenuecat.py [learner-id]

Reads REVENUECAT_SECRET_KEY / REVENUECAT_PROJECT_ID from .env (never prints
the key), lists the project's entitlements and products as Versa sees them,
and, given a learner id, that customer's active entitlements and purchases.
Read-only: it changes nothing in RevenueCat or in Versa.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

from versa.billing import (
    EXAM_PASS_PRODUCTS,
    PLUS_ENTITLEMENT,
    SPARK_PACKS,
    RevenueCatClient,
)


async def main() -> int:
    load_dotenv()
    key, project = os.getenv("REVENUECAT_SECRET_KEY", ""), os.getenv("REVENUECAT_PROJECT_ID", "")
    if not key or key.startswith("sk_your") or not project:
        print("REVENUECAT_SECRET_KEY / REVENUECAT_PROJECT_ID are not set in .env")
        return 1
    client = RevenueCatClient(key, project)
    try:
        entitlements = await client._list(f"/projects/{project}/entitlements")
        products = await client._list(f"/projects/{project}/products")
        keys = sorted(e.get("lookup_key") for e in entitlements)
        stores = sorted(p.get("store_identifier") for p in products)
        print("entitlements:", keys)
        print("products:    ", stores)
        missing = [PLUS_ENTITLEMENT] * (PLUS_ENTITLEMENT not in keys)
        missing += [p for p in [*SPARK_PACKS, *EXAM_PASS_PRODUCTS] if p not in stores]
        print("OK: everything Versa needs exists" if not missing else f"MISSING: {missing}")
        if len(sys.argv) > 1:
            customer = sys.argv[1]
            print("active entitlements:", await client.active_entitlements(customer))
            print("purchases:", await client.purchases(customer))
        return 1 if missing else 0
    finally:
        await client.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
