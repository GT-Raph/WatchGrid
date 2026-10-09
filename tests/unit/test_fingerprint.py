"""Error fingerprinting helpers (Section 16.2): pure functions, no database."""

from apps.errors.fingerprint import normalize_endpoint, normalize_message, top_frame

PY_TRACE = """Traceback (most recent call last):
  File "/app/payments/views.py", line 41, in create_payment
    charge = gateway.charge(order)
  File "/app/payments/gateway.py", line 88, in charge
    conn = pool.acquire()
DatabaseError: Unable to acquire database connection
"""


def test_top_frame_variants():
    assert top_frame(PY_TRACE) == "gateway.py:charge"
    js = "TypeError: x is undefined\n    at renderCart (https://cdn.example.com/app.js:10:5)\n    at main (app.js:1:1)"
    assert top_frame(js) == "app.js:renderCart"
    java = "java.lang.NullPointerException\n\tat com.acme.Cart.total(Cart.java:42)"
    assert top_frame(java) == "Cart.java:com.acme.Cart.total"
    assert top_frame("") == ""


def test_normalizers():
    assert (
        normalize_message("User 8f14e45f-ceea-467a-9575-1f8c1a2b3c4d not found (id=42)")
        == "User <uuid> not found (id=<n>)"
    )
    assert (
        normalize_endpoint("/api/orders/123/items/8f14e45f-ceea-467a-9575-1f8c1a2b3c4d?x=1")
        == "/api/orders/:id/items/:uuid"
    )
