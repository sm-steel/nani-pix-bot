"""The bound on ids read back from callback data and deep links."""

MAX_ID = 2**63 - 1  # BigInteger ceiling: anything larger would overflow the DB
