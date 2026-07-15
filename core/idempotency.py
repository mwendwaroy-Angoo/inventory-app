from django.core.cache import cache


def claim_checkout_token(business_id, token, ttl=120):
    """Atomically claim a client-supplied idempotency token for a checkout POST.

    Client-side double-submit guards (disabling a button, a JS "already submitted"
    flag) only protect against a second click on the same live page — they do
    nothing against a real duplicate request reaching the server (slow-network
    retry, browser back-button resubmission of a real <form>, a stray double tap
    that both landed before the button could disable). This is the server-side
    backstop: the same token, from the same business, can only win the race once.

    Returns True the first time a token is seen (caller should proceed and
    write the sale). Returns False if it was already claimed within the TTL
    window (caller must treat this as a duplicate and skip re-processing).
    A blank token always returns True — callers without a token get no
    protection rather than being blocked outright.
    """
    if not token:
        return True
    key = f'checkout_idem:{business_id}:{token}'
    return cache.add(key, True, timeout=ttl)
