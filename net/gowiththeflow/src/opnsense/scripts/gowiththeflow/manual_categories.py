"""Hand-curated hostname -> category overrides for domains that either
have no real coverage in v2fly/domain-list-community, or where its
per-company file groups things too coarsely for our purposes (see
categories.py's own comment on the amazon/aws case for an example of
the latter, fixed there directly since a clean upstream fix existed).

This is the other half of the workflow: not every real hostname a real
network sees is going to have a matching upstream file, so this module
exists to be grown incrementally from what a real box actually
observes -- see the Top Talkers "Uncategorized Hosts" tab, which lists
real, currently-uncategorized hostnames ordered by traffic volume, so
entries can be added here for the ones that matter rather than guessed
at ahead of time.

Checked *before* the v2fly-based CategoryMatcher (see
gowiththeflowd.py's _CategoryMatcherHolder.categorize()) -- an explicit
human judgment call always wins over the automated lookup, the same
precedence static_overrides gets over the automated hostname resolvers
in correlator.py.
"""

from __future__ import annotations

# domain suffix (matches itself and any subdomain, same rule as
# categories.CategoryMatcher's suffix entries) -> category label.
#
# First real pass, seeded from a live nostromo export of the Top Talkers
# "Uncategorized Hosts" tab (2282 hostnames). Grouped into buckets that
# are safe to categorize *by domain suffix alone* -- single-purpose
# infrastructure/device-cloud domains where the suffix itself is a
# reliable signal regardless of which specific subdomain shows up.
# Deliberately NOT attempted here (left uncategorized, on purpose):
# - Reverse-DNS PTR hostnames under a generic VPS/hosting provider (e.g.
#   *.ip.linodeusercontent.com, *.datapacket.com, *hosted-by-worldstream.
#   net) -- the provider tells you nothing about what's actually running
#   on that IP, so a suffix rule there would be a real guess, not a fact.
# - General retail/media/finance company domains (Shopping, News,
#   Banking, ...) -- skipped as a deliberate first-pass scope decision,
#   not because they're unsafe the way PTR hosts are; a future pass
#   could reasonably add them. Some (e.g. bbc.co.uk) also genuinely
#   span more than one category (News, Streaming/iPlayer, Music/Sounds)
#   and would need real per-subdomain judgment, not a single bulk rule.
OVERRIDES: dict[str, str] = {
    # DNS/NTP/registry infrastructure -- root/TLD/registry nameservers,
    # third-party DNS providers, and time-sync servers. Single-purpose
    # network plumbing, essentially never anything a user would
    # recognize as "a site they visited" regardless of which specific
    # service triggered the lookup.
    "root-servers.net": "Network Infrastructure",
    "gtld-servers.net": "Network Infrastructure",
    "edu-servers.net": "Network Infrastructure",
    "gtld.biz": "Network Infrastructure",
    "nic.uk": "Network Infrastructure",
    "arin.net": "Network Infrastructure",
    "ripe.net": "Network Infrastructure",
    "apnic.net": "Network Infrastructure",
    "afrinic.net": "Network Infrastructure",
    "iana-servers.net": "Network Infrastructure",
    "nsone.net": "Network Infrastructure",
    "ultradns.net": "Network Infrastructure",
    "ultradns.com": "Network Infrastructure",
    "ultradns.org": "Network Infrastructure",
    "ultradns.info": "Network Infrastructure",
    "ultradns.biz": "Network Infrastructure",
    "ultradns.co.uk": "Network Infrastructure",
    "ultradns2.org": "Network Infrastructure",
    "ultradns2.com": "Network Infrastructure",
    "digicertdns.net": "Network Infrastructure",
    "digicertdns.com": "Network Infrastructure",
    "azuregov-dns.us": "Network Infrastructure",
    "dnsmadeeasy.com": "Network Infrastructure",
    "bitnames.com": "Network Infrastructure",
    "bunnydns.com": "Network Infrastructure",
    "ncuk.net": "Network Infrastructure",
    "ncuk.net.uk": "Network Infrastructure",
    "demysdns.co.uk": "Network Infrastructure",
    "demysdns.com": "Network Infrastructure",
    "everett.org": "Network Infrastructure",
    "dns.cn": "Network Infrastructure",
    "dns.it": "Network Infrastructure",
    "afilias-nst.org": "Network Infrastructure",
    "afilias-nst.info": "Network Infrastructure",
    "pir-ns.org": "Network Infrastructure",
    "domaincontrol.com": "Network Infrastructure",
    "sectigoweb.com": "Network Infrastructure",
    "constellix.net": "Network Infrastructure",
    "adobe.net": "Network Infrastructure",
    "ntpns.org": "Network Infrastructure",
    "pool.ntp.org": "Network Infrastructure",
    "panq.nl": "Network Infrastructure",
    "ntp0.sotaconnect.net": "Network Infrastructure",
    "ntp1.karneval.cz": "Network Infrastructure",
    "ntp1.exa-networks.co.uk": "Network Infrastructure",
    "ntp2.as200552.net": "Network Infrastructure",
    "ntp2.glypnod.com": "Network Infrastructure",
    "ntp5.leontp.com": "Network Infrastructure",
    "01.ntp.sarik.tech": "Network Infrastructure",
    "pool.ntp0.cam.ac.uk": "Network Infrastructure",
    "pool.ntp1.cam.ac.uk": "Network Infrastructure",
    "pool.ntp3.cam.ac.uk": "Network Infrastructure",
    # Microsoft's Azure Traffic Manager nameserver domains -- every
    # instance observed was an "ns1-4.<region>-msedge.net" delegation
    # record, not actual content, regardless of which Microsoft-hosted
    # service was being resolved.
    "ax-msedge.net": "Network Infrastructure",
    "arc-msedge.net": "Network Infrastructure",
    "arm-msedge.net": "Network Infrastructure",
    "bx-msedge.net": "Network Infrastructure",
    "mcr-msedge.net": "Network Infrastructure",
    "o-msedge.net": "Network Infrastructure",
    "ln-msedge.net": "Network Infrastructure",
    "wac-msedge.net": "Network Infrastructure",
    "b-dc-msedge.net": "Network Infrastructure",
    # Ookla's speedtest server network and speedtest.net itself --
    # bandwidth-testing infrastructure, not content.
    "ooklaserver.net": "Network Infrastructure",
    "speedtest.net": "Network Infrastructure",

    # Smart home / IoT device cloud backends -- single-purpose (a Ring
    # doorbell's API isn't also going to serve up news or shopping), so
    # the suffix alone is a reliable signal here in a way it usually
    # isn't for general company domains.
    "tplinkcloud.com": "Smart Home/IoT",
    "tplinknbu.com": "Smart Home/IoT",
    "tp-link.com": "Smart Home/IoT",
    "tplinkra.com": "Smart Home/IoT",
    "tapo.com": "Smart Home/IoT",
    "wyzecam.com": "Smart Home/IoT",
    "wyze.com": "Smart Home/IoT",
    "ring.com": "Smart Home/IoT",
    "tuya.com": "Smart Home/IoT",
    "meethue.com": "Smart Home/IoT",
    "ecobee.com": "Smart Home/IoT",
    "firewalla.com": "Smart Home/IoT",
    "encipher.io": "Smart Home/IoT",
    "garmin.com": "Smart Home/IoT",
    "synology.com": "Smart Home/IoT",
    "samsunghealth.com": "Smart Home/IoT",
    "samsungcloud.com": "Smart Home/IoT",
    "samsungapps.com": "Smart Home/IoT",

    # Gaming -- game-networking SDKs and game-company domains that
    # v2fly/domain-list-community's curated "category-games" file (our
    # only automated Gaming source) doesn't happen to include.
    "robertsspaceindustries.com": "Gaming",
    "exitgames.com": "Gaming",
    "mobilityware.com": "Gaming",
    "tocaboca.com": "Gaming",
    "animaljam.com": "Gaming",
    "peoplefungames.com": "Gaming",
    "cubecraft.net": "Gaming",
    "hardlightgames.com": "Gaming",
    "galaxite.net": "Gaming",
    "devvit.net": "Gaming",
    "inpvp.net": "Gaming",
    "megasmp.gg": "Gaming",
    "enchanted.gg": "Gaming",
    "gxcorner.games": "Gaming",
    "playpiknik.com": "Gaming",
    # 4netplayers.com's own reverse-PTR pattern for its rented game
    # servers ("gs-<ip>.server.4netplayers.com") is an explicit "this is
    # a game server" signal, unlike a generic VPS host's PTR.
    "4netplayers.com": "Gaming",

    # WebRTC/VoIP signalling infrastructure -- used by many different
    # apps for the actual call, so it's a purpose-based bucket rather
    # than any one app's own category.
    "ekiga.net": "Communication",
    "stun.1und1.de": "Communication",
    "stun.kaptcha.com": "Communication",
    "vivox.com": "Communication",

    # Sky's own ISP-embedded CDN cache nodes ("Google Global Cache"
    # style -- note the literal "ggc"/"aanp" markers in the hostnames) --
    # infrastructure carrying someone else's content, not a site of its
    # own, same idea as Cloudflare/Akamai/Fastly already being Cloud
    # Infrastructure rather than "whatever site happens to be behind it."
    "isp.sky.com": "Cloud Infrastructure",

    # Well-known UK residential-broadband reverse-DNS domains -- the
    # traffic itself is almost certainly P2P/gaming/WebRTC to someone's
    # home connection, not a site being browsed. Deliberately narrow:
    # only suffixes where every observed hostname under them was this
    # exact PTR shape, unlike e.g. toob.co.uk (also runs its own
    # speedtest server under the same domain) or as13285.net-style ones
    # mixed with other uses, which were left alone rather than guessed.
    "btcentralplus.com": "Peer-to-Peer",
    "gigaclear.net": "Peer-to-Peer",
}


def categorize(hostname: str | None) -> str | None:
    if not hostname:
        return None
    hostname = hostname.lower()
    for suffix, category in OVERRIDES.items():
        if hostname == suffix or hostname.endswith("." + suffix):
            return category
    return None
