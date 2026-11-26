DEFAULT_LIMIT = 6


class Booking:
    def __init__(self, seats):
        self.seats = seats


def seats_available(booked, capacity):
    """Return the number of seats still open for booking."""
    return max(0, capacity - booked)


def booking_limit(override=None):
    """Use the system limit only when there is no override."""
    return DEFAULT_LIMIT if override is None else override
