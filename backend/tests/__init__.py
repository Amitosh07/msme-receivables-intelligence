import os

# Suites register many tenants and log in repeatedly from one client address;
# rate-limit behaviour is covered explicitly in test_phase4_production.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
