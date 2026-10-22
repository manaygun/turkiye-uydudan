"""Turkish Reels captions: hook question first, then keyword-rich (SEO) text, CTA, credit, hashtags.

Instagram search reads caption keywords, and shows only the first ~125 characters
before "devamı", so the hook goes first. Hashtags are capped at 5.
"""

CTAS = [
    "Sence 10 yıl sonra burası nasıl görünecek? Yorumlara yaz 👇",
    "Burayı gören ya da orada yaşayan var mı? Yorumda buluşalım 👇",
    "Sıradaki videoda hangi yeri görmek istersin? Yorumlara yaz 👇",
    "Bu değişim seni şaşırttı mı? 1'den 10'a puan ver 👇",
    "Sence bu değişimin en büyük sebebi ne? Yorumlara yaz 👇",
    "Bunu görmesi gereken birini etiketle 👇",
]

SAT_NAMES = {"landsat": "Landsat", "sentinel": "Sentinel-2"}
CREDITS = {
    "landsat": "Görüntüler: NASA / USGS Landsat.",
    "sentinel": "Contains modified Copernicus Sentinel data. Görüntüler: ESA Copernicus Sentinel-2.",
}
COMMON_TAGS = ["uydugörüntüsü", "dünya", "keşfet"]


def build_caption(place, hook, years, source, index):
    first, last = years[0], years[-1]
    sat = SAT_NAMES[source]
    tags = list(dict.fromkeys(place.get("tags", [])[:2] + COMMON_TAGS))[:5]
    cta = CTAS[index % len(CTAS)]
    return "\n".join([
        f"{hook} 🛰️",
        "",
        f"📍 {place['name']} ({place['il']}) | {first} → {last}",
        "",
        place["story"],
        "",
        f"Bu videoda {place['name']} bölgesinin uydudan yıllar içindeki değişimini, "
        f"{first} yılındaki en eski haritadan {last} yılındaki en yeni haritaya kadar "
        f"gerçek {sat} uydu görüntüleriyle izliyorsun. dünyanın değişimini uydudan "
        f"gösteren zaman atlamalı (timelapse) videolar için takip et.",
        "",
        cta,
        "",
        CREDITS[source],
        "",
        " ".join(f"#{t}" for t in tags),
    ])


# ---------------------------------------------------------------- English explainers

EXPLAINER_CTAS = [
    "Did you know this? Tell us in the comments 👇",
    "What should we explain next? Drop it in the comments 👇",
    "Send this to someone who uses it every day 👇",
    "Which part surprised you most? 👇",
    "Save this for later and share it with a curious friend 👇",
]
EXPLAINER_TAGS = ["howitworks", "learnsomethingnew", "science"]


def build_explainer_caption(topic, index):
    """Hook first (it's all people see before "more"), then a plain-English summary, CTA, 5 hashtags."""
    tags = list(dict.fromkeys(topic.get("tags", [])[:2] + EXPLAINER_TAGS))[:5]
    return "\n".join([
        f"{topic['hook']} 🤔",
        "",
        topic["caption"],
        "",
        EXPLAINER_CTAS[index % len(EXPLAINER_CTAS)],
        "",
        "Follow @gecmistenharita for a new \"how it works\" every day.",
        "",
        " ".join(f"#{t}" for t in tags),
    ])
