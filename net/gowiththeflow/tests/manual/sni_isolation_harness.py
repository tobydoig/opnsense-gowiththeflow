"""Manual isolated SNI-capture test -- NOT part of the automated suite.
Calls sni_sniffer.sniff_loop() directly (the real production function,
not a reimplementation) so this test actually exercises the fixed code
path, and appends every hint observed to a file.
"""

import sys

sys.path.insert(0, "/root/gowiththeflow_test")

import sni_sniffer

with open("/tmp/sni_hints.log", "a") as f:
    f.write("starting sniff_loop on le0...\n")
    f.flush()

    def on_hint(local_ip, local_port, remote_ip, remote_port, hostname, ts):
        f.write(f"HINT {local_ip}:{local_port} -> {remote_ip}:{remote_port} SNI={hostname}\n")
        f.flush()

    sni_sniffer.sniff_loop(["le0"], on_hint)
