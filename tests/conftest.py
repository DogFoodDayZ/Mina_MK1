import os


# API route modules are imported during pytest collection. They must not construct
# Mina's live memory engine before individual tests inject their fake cores.
os.environ["MK1_API_TEST_MODE"] = "1"