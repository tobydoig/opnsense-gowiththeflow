"""Manual isolated SNI-capture test -- NOT part of the automated suite.
Runs only sni_sniffer's live capture (no pf polling, no DNS sniffing, no
db writes) and appends every hint observed to a file, so the live TLS
ClientHello capture path can be confirmed working independently of
whether the resulting flow would ever be tracked as a pf state.

This version also logs every raw ClientHello reassembly attempt (not just
successful hostname extractions), to diagnose exactly where a real-world
packet diverges from the synthetic test fixtures if extraction is still
failing.
"""

import sys

sys.path.insert(0, "/root/gowiththeflow_test")

import sni_sniffer
from scapy.layers.inet import IP, TCP
from scapy.layers.l2 import Ether
from scapy.sendrecv import sniff

reassembler = sni_sniffer.ClientHelloReassembler()

with open("/tmp/sni_hints.log", "a") as f:

    def _handle(pkt):
        eth = Ether(bytes(pkt))
        if IP not in eth or TCP not in eth:
            return
        payload = bytes(eth[TCP].payload)
        if not payload:
            return
        key = (eth[IP].src, eth[TCP].sport, eth[IP].dst, eth[TCP].dport)
        f.write(f"RAW {key} len={len(payload)} first16={payload[:16].hex()}\n")
        if len(payload) >= 5 and payload[0] == 0x16 and len(payload) < 300:
            f.write(f"FULL_CLIENTHELLO_CANDIDATE {key} hex={payload.hex()}\n")
        f.flush()
        hostname = reassembler.feed(key, payload)
        if hostname is not None:
            f.write(f"HINT {key} SNI={hostname}\n")
            f.flush()

    f.write("starting sniff on le0...\n")
    f.flush()
    sniff(iface=["le0"], filter="tcp and port 443", prn=_handle, store=False, timeout=30)
    f.write("sniff loop ended\n")
    f.flush()
