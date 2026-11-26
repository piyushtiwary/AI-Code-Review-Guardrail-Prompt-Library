DEFAULT_DEPOT = "central"


def depot_for(shipment):
    """Use the central depot when the shipment has no assigned depot."""
    return shipment.get("depot") or DEFAULT_DEPOT


def notify(send, shipment):
    """Deliver a notice through the supplied transport."""
    send(shipment)
