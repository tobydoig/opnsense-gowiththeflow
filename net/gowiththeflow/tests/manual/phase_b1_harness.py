"""Manual Phase B1 test harness -- NOT part of the automated test suite.
Run directly on the OPNsense test VM to drive gowiththeflowd for a short,
fixed window while real test traffic is generated from the client, then
inspect the resulting SQLite file. Deleted/ignored outside of manual VM
testing; not something CI or pytest ever collects.
"""

import sys

sys.path.insert(0, "/root/gowiththeflow_test")

import gowiththeflowd as g

config = g.Config(
    db_path="/tmp/test_flows.db",
    capture_interfaces=["le0"],
    local_subnets=["10.0.0.0/24"],
)
g.run(config)
