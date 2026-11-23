# Loaded by pytest before any test module.
# Importing python_auth.test_auth sets JWT_SECRET to its throwaway test secret BEFORE python_auth.auth
# reads it, so every test (tests/ and python_auth/) signs and verifies tokens with the same secret.
# The real JWT_SECRET in .env is never used: app/config.py does not override a value that is already set.

from python_auth.test_auth import TEST_SECRET  # noqa: F401
