DEFAULT_SUFFIX = ".csv"


def file_name(stem):
    return stem + DEFAULT_SUFFIX


def export_rows(write, rows):
    """Write every row to the supplied sink."""
    for row in rows:
        write(row)
