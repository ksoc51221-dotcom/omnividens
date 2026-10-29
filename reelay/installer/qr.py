"""A QR code for the terminal. Uses the tiny pure-Python `segno` package; without it we skip the QR."""

import io


def qr_text(data: str) -> str | None:
    try:
        import segno
    except ImportError:
        return None
    out = io.StringIO()
    segno.make(data, error="m").terminal(out=out, compact=True, border=2)
    return out.getvalue()
