import json

from dpi_classifier import DpiClassification, parse_ndpi_output

LOCAL_SUBNETS = ["192.168.1.0/24"]

# Real ndpiReader -K json output, captured against actual traffic on the
# test VM during this feature's own feasibility research.
REAL_DNS_LINE = (
    '{"src_ip":"10.0.0.9","dest_ip":"10.0.0.1","src_port":53223,"dst_port":53,'
    '"ip":4,"proto":"UDP","ndpi":{"flow_risk":{"43":{"risk":"Error Code"}},'
    '"confidence":{"6":"DPI"},"proto":"DNS","proto_id":"5","proto_by_ip":"Unknown",'
    '"proto_by_ip_id":0,"encrypted":0,"breed":"Acceptable","category_id":14,'
    '"category":"Network","hostname":"nostromo.internal","domainame":"nostromo.internal",'
    '"dns":{"num_queries":1,"num_answers":0,"reply_code":3,"query_type":1,"rsp_type":0,'
    '"rsp_addr":[]}},"detection_completed":0,"flow_id":0,"first_seen":1787702478.318,'
    '"last_seen":1787702478.319,"duration":0.001}'
)


def _line(**overrides):
    rec = {
        "src_ip": "192.168.1.50", "dest_ip": "93.184.216.34",
        "src_port": 52341, "dst_port": 443, "proto": "TCP",
        "ndpi": {"proto": "TLS"},
    }
    rec.update(overrides)
    return json.dumps(rec)


def test_parses_the_real_captured_ndpi_line():
    results = parse_ndpi_output(REAL_DNS_LINE.encode(), ["10.0.0.0/24"])

    assert results == [
        DpiClassification(
            proto="udp", local_ip="10.0.0.9", local_port=53223,
            peer_ip="10.0.0.1", peer_port=53, dpi_protocol="DNS",
        )
    ]


def test_reorients_when_local_side_is_dest_not_src():
    line = _line(src_ip="93.184.216.34", dest_ip="192.168.1.50", src_port=443, dst_port=52341)

    results = parse_ndpi_output(line.encode(), LOCAL_SUBNETS)

    assert results == [
        DpiClassification(
            proto="tcp", local_ip="192.168.1.50", local_port=52341,
            peer_ip="93.184.216.34", peer_port=443, dpi_protocol="TLS",
        )
    ]


def test_flow_with_neither_side_local_is_skipped():
    line = _line(src_ip="93.184.216.34", dest_ip="1.2.3.4")

    results = parse_ndpi_output(line.encode(), LOCAL_SUBNETS)

    assert results == []


def test_line_with_no_ndpi_key_is_skipped_not_a_crash():
    line = json.dumps({"src_ip": "192.168.1.50", "dest_ip": "93.184.216.34",
                        "src_port": 1, "dst_port": 2, "proto": "TCP"})

    results = parse_ndpi_output(line.encode(), LOCAL_SUBNETS)

    assert results == []


def test_multiple_lines_all_parsed():
    raw = (_line(dst_port=443) + "\n" + _line(dst_port=8443)).encode()

    results = parse_ndpi_output(raw, LOCAL_SUBNETS)

    assert len(results) == 2
    assert {r.peer_port for r in results} == {443, 8443}


def test_blank_lines_and_malformed_json_are_tolerated():
    raw = ("\n" + _line() + "\nnot valid json\n").encode()

    results = parse_ndpi_output(raw, LOCAL_SUBNETS)

    assert len(results) == 1
