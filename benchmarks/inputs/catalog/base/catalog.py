DEFAULT_CURRENCY = "EUR"


def display_name(record):
    """Use the SKU when the optional display name is absent."""
    return record.get("name") or record["sku"]


def list_price(record):
    """Return the price in cents, including a valid zero price."""
    return record["cents"]
