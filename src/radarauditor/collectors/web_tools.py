"""Recognise chat, visitor analytics and Google Ads tags in a page's scripts and links.

Context: Shared by the website crawler, which records what a start page loads, and by the area n
checks, which judge it. Every tool is matched on stable markers: script hosts, script paths,
function names in inline scripts and link targets. Matching runs on folded (lower-case) text.
Input: Script addresses, inline script text and link addresses of a page.
Output: Tool names, Google tag ids and Google Ads conversion targets.
"""

import re
from collections.abc import Iterable

CHAT_TOOLS: dict[str, tuple[str, ...]] = {
    "tawk.to": ("embed.tawk.to", "tawk_api"),
    "Crisp": ("client.crisp.chat", "$crisp", "crisp_website_id"),
    "Tidio": ("code.tidio.co", "tidiochatapi"),
    "Userlike": ("userlike-cdn-widgets", "userlike.com", "userlikedata"),
    "Intercom": ("widget.intercom.io", "js.intercomcdn.com", "intercomsettings"),
    "Zendesk Chat": ("static.zdassets.com", "zopim", "zesettings", "zendesk.com/embeddable"),
    "HubSpot Conversations": ("js.usemessages.com", "hubspotconversations"),
    "LiveChat": ("cdn.livechatinc.com", "livechatinc.com", "__lc.license"),
    "Smartsupp": ("smartsuppchat.com", "_smartsupp"),
    "JivoChat": ("code.jivosite.com", "jivosite.com", "jivo_api"),
    "Chatwoot": ("chatwootsdk", "chatwoot"),
}
MESSENGER_LINKS: dict[str, tuple[str, ...]] = {
    "Facebook Messenger": ("//m.me/", "//www.m.me/"),
    "WhatsApp": ("//wa.me/", "//api.whatsapp.com/send", "//web.whatsapp.com/send"),
}
ANALYTICS_TOOLS: dict[str, tuple[str, ...]] = {
    # gtag() alone also loads Google Ads; Analytics ids G- and UA- are matched as tag ids.
    "Google Analytics": ("google-analytics.com", "ga('create'", 'ga("create"'),
    "Google Tag Manager": ("googletagmanager.com/gtm.js",),
    "Matomo": ("matomo.js", "matomo.php", "matomo.cloud", "_paq.push"),
    "Piwik": ("piwik.js", "piwik.php"),
    "Plausible": ("plausible.io/js", "plausible.js"),
    "etracker": ("etracker.com", "etracker.de", "et_pagename", "_etracker"),
    "Fathom": ("cdn.usefathom.com", "usefathom.com"),
    "Umami": ("umami.is", "umami.js", "umami.track"),
    "Microsoft Clarity": ("clarity.ms",),
    "Hotjar": ("static.hotjar.com", "hotjar.com", "_hjsettings"),
}
# Google Ads: the tag itself and the ways a conversion is measured.
ADS_MARKERS = ("googleadservices.com", "google_conversion_id", "googleads.g.doubleclick.net")
# Ids are case-sensitive and stand alone: a GA4 id is G- plus exactly ten capitals or digits, so
# class names and paths such as "g-recaptcha" or "/plugins/g-translate/" never count as a tag.
TAG_ID_PATTERN = re.compile(
    r"(?<![\w-])(G-[A-Z0-9]{10}|UA-\d{4,10}-\d{1,4}|GTM-[A-Z0-9]{4,9}|AW-\d{6,12})(?![\w-])"
)
SEND_TO_PATTERN = re.compile(r"""send_to['"]?\s*:\s*['"](AW-\d{6,12}/[A-Za-z0-9_-]+)['"]""")
CONVERSION_ID_PATTERN = re.compile(r"google_conversion_id\s*[=:]\s*['\"]?(\d{6,12})")
CONVERSION_URL_PATTERN = re.compile(
    r"googleadservices\.com/pagead/conversion(?:_async\.js|/(\d{6,12}))", re.IGNORECASE
)
INLINE_MARKERS = tuple(
    dict.fromkeys(
        marker
        for markers in (*CHAT_TOOLS.values(), *ANALYTICS_TOOLS.values(), ADS_MARKERS)
        for marker in markers
    )
)


def inline_signatures(scripts: Iterable[str]) -> list[str]:
    """Return the known tool markers that occur in inline script text, in marker order.

    Args:
        scripts: Text of the inline scripts of a page.

    Returns:
        Every marker of INLINE_MARKERS found, each once.
    """
    text = "\n".join(scripts).casefold()
    return [marker for marker in INLINE_MARKERS if marker in text]


def google_tag_ids(texts: Iterable[str]) -> list[str]:
    """Return the Google tag ids (G-, UA-, GTM-, AW-) in script text and addresses, each once.

    Args:
        texts: Inline script text and script addresses of a page.

    Returns:
        Tag ids in the order they first appear.
    """
    found: list[str] = []
    for text in texts:
        for match in TAG_ID_PATTERN.finditer(text):
            if match.group(1) not in found:
                found.append(match.group(1))
    return found


def ads_conversions(texts: Iterable[str]) -> list[str]:
    """Return the Google Ads conversion measurements in script text and addresses.

    Args:
        texts: Inline script text and script addresses of a page.

    Returns:
        send_to targets as "AW-id/label", google_conversion_id values as "AW-id", and
        googleadservices conversion calls as "pagead/conversion" (with "/id" when it names one),
        each once.
    """
    found: list[str] = []
    for text in texts:
        candidates = [match.group(1) for match in SEND_TO_PATTERN.finditer(text)]
        candidates.extend(f"AW-{match.group(1)}" for match in CONVERSION_ID_PATTERN.finditer(text))
        for match in CONVERSION_URL_PATTERN.finditer(text):
            candidates.append(
                f"pagead/conversion/{match.group(1)}" if match.group(1) else "pagead/conversion"
            )
        found.extend(item for item in candidates if item not in found)
    return found


def tools_in(markers: dict[str, tuple[str, ...]], texts: Iterable[str]) -> list[str]:
    """Return the tools whose markers occur in any of the texts, in table order.

    Args:
        markers: Tool name to lower-case markers, e.g. CHAT_TOOLS.
        texts: Script addresses, inline signatures or link addresses.

    Returns:
        Names of the tools found.
    """
    folded = [text.casefold() for text in texts]
    return [
        tool
        for tool, needles in markers.items()
        if any(needle in text for text in folded for needle in needles)
    ]
