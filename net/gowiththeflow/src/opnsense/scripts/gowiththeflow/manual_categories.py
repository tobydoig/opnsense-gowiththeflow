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

    # --- Second pass, seeded from a later nostromo "Uncategorized
    # Hosts" export. Extends into buckets the first pass deliberately
    # deferred (Shopping/News/Banking company domains, per its own
    # docstring) now that there's a real, traffic-ordered list to work
    # from rather than guessing at coverage ahead of time. Same
    # discipline as pass one throughout: a suffix only goes in if the
    # *entire* domain is safe to bucket regardless of which subdomain
    # shows up (a retailer's storefront, a bank's own domain, a single-
    # purpose SDK/CDN) -- generic hosting-provider PTR hosts
    # (*.ip.linodeusercontent.com, *.datapacket.com, ...) and one-off/
    # low-confidence names are left alone rather than guessed at.

    # Retail -- single-brand storefronts/CDNs, plus Shopify's own
    # platform domains (any store built on Shopify, not one company).
    "vinted.com": "Shopping", "vinted.co.uk": "Shopping", "vinted.net": "Shopping",
    "vinted.lt": "Shopping",
    "asos.com": "Shopping", "asos-media.com": "Shopping", "asosservices.com": "Shopping",
    "next.co.uk": "Shopping",
    "nordstrom.com": "Shopping",
    "marksandspencer.com": "Shopping",
    "debenhams.com": "Shopping",
    "zalando.co.uk": "Shopping", "ztat.net": "Shopping",  # ztat.net is Zalando's own CDN
    "temu.com": "Shopping", "kwcdn.com": "Shopping",  # kwcdn.com is Temu's CDN
    "shein.com": "Shopping", "ltwebstatic.com": "Shopping",  # Shein's CDN
    "etsystatic.com": "Shopping",  # etsy.com itself already covered upstream (v2fly)
    "ebaydesc.com": "Shopping",
    "johnlewis.com": "Shopping",
    "dunelm.com": "Shopping",
    "mainlinemenswear.co.uk": "Shopping",
    "cottontraders.com": "Shopping",
    "mountainwarehouse.com": "Shopping",
    "flannels.com": "Shopping",
    "roman.co.uk": "Shopping",
    "bonprix.co.uk": "Shopping",
    "woolovers.com": "Shopping",
    "jdwilliams.co.uk": "Shopping",
    "hsn.com": "Shopping",
    "worldofbooks.com": "Shopping",
    "zara.com": "Shopping",
    "uniqlo.com": "Shopping",
    "argos.co.uk": "Shopping",
    "bouxavenue.com": "Shopping",
    "matalan.co.uk": "Shopping",
    "damart.co.uk": "Shopping",
    "myshopify.com": "Shopping", "shopifycdn.com": "Shopping", "shopify.com": "Shopping",
    # Airsoft/tactical-gear retailers -- same single-purpose logic.
    "mirtactical.com": "Shopping", "highpressureairsoft.co.uk": "Shopping",
    "usedairsoft.co.uk": "Shopping", "outdoorandtactical.co.uk": "Shopping",
    "gearofwar.co.uk": "Shopping", "redwolfairsoft.com": "Shopping",
    "airsoftzone.co.uk": "Shopping", "zenitco.ru": "Shopping",
    # 3D-printer manufacturers' own sites (not the model-sharing
    # communities like Thingiverse/Printables/Cults3D, which aren't
    # storefronts and were left alone).
    "ultimaker.com": "Shopping", "bambulab.com": "Shopping", "elegoo.com": "Shopping",

    # News publishers -- their own domains and CDNs. bbc.co.uk itself
    # deliberately excluded (see pass one's docstring: it genuinely spans
    # News/iPlayer/Sounds and needs real per-subdomain judgment, not a
    # bulk rule) -- "news.ycombinator.com" is scoped to that one
    # subdomain for the same reason (ycombinator.com itself is much more
    # than just Hacker News).
    "nytimes.com": "News", "nyt.com": "News",
    "washingtonpost.com": "News",
    "theguardian.com": "News", "guim.co.uk": "News", "guardianapis.com": "News",
    "apnews.com": "News",
    "telegraph.co.uk": "News",
    "nypost.com": "News",
    "cbsnews.com": "News",
    "thedailybeast.com": "News",
    "gizmodo.com": "News",
    "businessinsider.com": "News",
    "aljazeera.com": "News",
    "reachgeneric.co.uk": "News",  # Reach plc's shared infra (Mirror, Express, local papers)
    "lincolnshirelive.co.uk": "News", "nottinghampost.com": "News",  # Reach plc local papers
    "slashdot.org": "News",
    "futurism.com": "News",
    "news.ycombinator.com": "News",

    # Banks, payment processors, and card/fraud infrastructure -- these
    # only ever carry the bank's/processor's own traffic.
    "coinbase.com": "Banking",
    "paypal.com": "Banking", "paypalobjects.com": "Banking",
    "chase.com": "Banking",
    "capitalone.com": "Banking",
    "hsbc.co.uk": "Banking", "hsbc.com": "Banking", "hsbc.net": "Banking", "hsbc.uk": "Banking",
    "lloydsbank.co.uk": "Banking", "lloydsbanking.com": "Banking",
    "lloydsbankinggroup.com": "Banking",
    "nsandi.com": "Banking",
    "firstdirect.com": "Banking",
    "virginmoney.com": "Banking",
    "klarna.com": "Banking", "klarnaservices.com": "Banking", "klarnacdn.net": "Banking",
    "klarnaevt.com": "Banking",
    "stripe.com": "Banking", "stripecdn.com": "Banking", "stripe.network": "Banking",
    "visa.com": "Banking",
    "worldpay.com": "Banking",
    "sardine.ai": "Banking",  # fraud-detection SDK used by financial services
    "cardinalcommerce.com": "Banking",  # Mastercard's 3-D Secure card-auth service
    "zetapay.tech": "Banking",

    # Marketing/analytics/consent-management SaaS -- the same "tracks or
    # profiles you for someone else's benefit" purpose as Ads/Tracking's
    # existing v2fly-sourced entries, just not covered by that upstream
    # list.
    "scorecardresearch.com": "Ads/Tracking",
    "taboola.com": "Ads/Tracking",
    "onetrust.com": "Ads/Tracking", "onetrust.io": "Ads/Tracking",
    "cookielaw.org": "Ads/Tracking",
    "cookieyes.com": "Ads/Tracking",
    "cookiebot.com": "Ads/Tracking",
    "consentmanager.net": "Ads/Tracking",
    "usercentrics.eu": "Ads/Tracking",
    "trustarc.com": "Ads/Tracking",
    "klaviyo.com": "Ads/Tracking",
    "mailchimp.com": "Ads/Tracking", "list-manage.com": "Ads/Tracking",
    "chimpstatic.com": "Ads/Tracking",
    "bazaarvoice.com": "Ads/Tracking",
    "yotpo.com": "Ads/Tracking",
    "powerreviews.com": "Ads/Tracking",
    "trustpilot.com": "Ads/Tracking",
    "branch.io": "Ads/Tracking",
    "appsflyer.com": "Ads/Tracking",
    "singular.net": "Ads/Tracking",
    "rudderstack.com": "Ads/Tracking",
    "optimizely.com": "Ads/Tracking",
    "qualtrics.com": "Ads/Tracking",
    "medallia.com": "Ads/Tracking",
    "rubiconproject.com": "Ads/Tracking",
    "inmobi.com": "Ads/Tracking",
    "omtrdc.net": "Ads/Tracking",  # Adobe Experience Cloud (Analytics/Target)
    "pendo.io": "Ads/Tracking",

    # Customer-support chat/helpdesk widgets -- a live-support channel,
    # same purpose bucket as WhatsApp/Discord/Zoom above.
    "zendesk.com": "Communication", "zdassets.com": "Communication",
    "freshworksapi.com": "Communication", "freshchat.com": "Communication",
    "gorgias.chat": "Communication",
    "intercom.io": "Communication", "intercomcdn.com": "Communication",
    "intercomcdn.eu": "Communication",
    "liveperson.net": "Communication",
    "getzowie.com": "Communication",
    "salesiq.zoho.eu": "Communication",  # scoped to the SalesIQ chat widget, not all of Zoho

    # Gaming -- same company as robertsspaceindustries.com (already
    # above), and NVIDIA's GeForce/cloud-gaming-specific subdomains
    # (scoped narrowly, not bare nvidia.com, which is a much broader
    # hardware/AI company domain).
    "cloudimperiumgames.com": "Gaming",
    "geforce.com": "Gaming", "gfe.nvidia.com": "Gaming", "ops-gx.nvidia.com": "Gaming",
    "nvidiagrid.net": "Gaming",

    # firewalla.org is the same company as firewalla.com (already
    # above), just a second TLD. securecomwireless.com is a home
    # alarm/security-system cloud backend, same single-purpose logic as
    # the rest of this bucket.
    "firewalla.org": "Smart Home/IoT",
    "securecomwireless.com": "Smart Home/IoT",

    # CDN/WAF infrastructure carrying someone else's content, same idea
    # as Cloudflare/Akamai/Fastly already being Cloud Infrastructure
    # rather than "whatever site happens to be behind it."
    "bunnyinfra.net": "Cloud Infrastructure",
    "sucuri.net": "Cloud Infrastructure",

    # zenarmor.net is OPNsense's own Zenarmor/Sensei security plugin's
    # update server -- appliance plumbing, not a site. The speedtest
    # entries are scoped to the *exact* hostname observed, not their
    # whole parent domain (unlike ooklaserver.net/speedtest.net above,
    # an ISP's own root domain usually carries other, non-infra content
    # too -- see pass one's own note on toob.co.uk for exactly this
    # reasoning).
    "updates.zenarmor.net": "Network Infrastructure",
    "speedtest.trooli.com": "Network Infrastructure",
    "speedtest.ths.connectfibre.co.uk": "Network Infrastructure",
    "speedtest.skybloxsystems.co.uk": "Network Infrastructure",
    "speedtest.toob.co.uk": "Network Infrastructure",

    # Amazon Prime Video's own CDN domain -- distinct from primevideo.com
    # itself (already covered upstream via v2fly's "primevideo" keyword)
    # and not caught by that keyword since it's a separate domain.
    "pv-cdn.net": "Streaming/Video",

    # School/education portals -- a real, recurring cluster in family
    # network traffic that neither v2fly nor pass one's buckets cover.
    "edulinkone.com": "Education",
    "mychildatschool.com": "Education",
    "classmanager.com": "Education",
    "parentpay.com": "Education",
    "mathletics.com": "Education",
    "schoolgrid.co.uk": "Education",
    "e4education.co.uk": "Education",
    "pearson.com": "Education",
    "coursera.org": "Education",

    # UK/US government services -- gov.uk is effectively a public-sector
    # namespace (every UK government department/agency lives under it),
    # safe to bucket wholesale the same way a company's own domain is.
    "gov.uk": "Government",
    "digitalgov.gov": "Government",
    "met.police.uk": "Government",

    "worldlabs.ai": "AI",
}


def categorize(hostname: str | None) -> str | None:
    if not hostname:
        return None
    hostname = hostname.lower()
    for suffix, category in OVERRIDES.items():
        if hostname == suffix or hostname.endswith("." + suffix):
            return category
    return None
