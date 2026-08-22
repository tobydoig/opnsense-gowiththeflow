"""Diagnose scapy's link-layer dissection on this platform."""
from scapy.layers.inet import IP
from scapy.layers.l2 import Ether
from scapy.sendrecv import sniff

count = [0]


def cb(pkt):
    count[0] += 1
    raw = bytes(pkt)
    print("len:", len(raw), "first16:", raw[:16].hex())
    reparsed = Ether(raw)
    print("reparsed has IP:", IP in reparsed)
    if IP in reparsed:
        print("  src:", reparsed[IP].src, "dst:", reparsed[IP].dst)


print("starting capture on le0, tcp port 443, 8s...")
sniff(iface=["le0"], filter="tcp port 443", prn=cb, store=False, timeout=8)
print("packets seen:", count[0])
