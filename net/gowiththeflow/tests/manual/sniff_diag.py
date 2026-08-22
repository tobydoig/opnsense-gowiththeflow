"""Bare scapy sniff() diagnostic -- is anything captured at all?"""
from scapy.sendrecv import sniff

count = [0]


def cb(pkt):
    count[0] += 1
    print("PACKET:", pkt.summary())


print("starting capture on le0, tcp port 443, 8s...")
sniff(iface=["le0"], filter="tcp port 443", prn=cb, store=False, timeout=8)
print("packets seen:", count[0])
