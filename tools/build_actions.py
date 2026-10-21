"""Build data/actions.js (job actions, familiar skills, traits) from game data via XIVAPI.

Usage:  python tools/build_actions.py

Tooltip text comes from ActionTransient / Trait with the game's own colour markup
(requested as HTML), so the site can render the same tooltips as the game.
"""
import concurrent.futures as cf
import json
import os
import re
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "tools", ".cache")
UA = {"User-Agent": "bst-fansite-assets/1.0"}
API = "https://v2.xivapi.com/api"

# Player action rows (Action sheet, ClassJob 43 plus the upgrades/Kinship actions that
# carry no ClassJob). Tempered Release also exists as row 47092 with ClassJob 0; 44890
# is the row assigned to the job.
GROUPS = [
    ("Weaponskills", [44879, 44883, 44885]),
    ("Instinctual Skills", [44884, 44887, 44888, 44889]),
    ("Instinctual Mastery (Lv. 50, 250 TP)", [44930, 44931, 44932, 44933]),
    ("Familiar Commands", [44881, 44892, 44894, 44891, 47093, 44890, 44895, 44886]),
    ("Resources & Mobility", [44905, 44904, 44893]),
    ("Capture", [44880, 44882]),
    ("Kinship Actions", [44896, 44897, 44898, 44899, 44900, 44901, 44902, 44903]),
]
KINSHIP_CLASS = {44896: "beastkin", 44897: "vilekin", 44898: "cloudkin", 44899: "seedkin",
                 44900: "wavekin", 44901: "scalekin", 44902: "soulkin", 44903: "ashkin"}

FAMILIAR_FIRST = 44935  # Trick / Tempered Release pairs, one per beast in bestiary order
CLASSES = ["", "beastkin", "vilekin", "cloudkin", "seedkin", "wavekin", "scalekin", "soulkin", "ashkin"]
ASPECTS = {1: "Fire", 2: "Ice", 3: "Wind", 4: "Earth", 5: "Lightning", 6: "Water"}
ATTACK_TYPES = {1: "Slashing", 2: "Piercing", 3: "Blunt"}
STATUS_WORDS = ["Slow", "Petrification", "Paralysis", "Interrupt", "Blind", "Poison",
                "Stun", "Sleep", "Bind", "Heavy", "Doom"]
COST = {111: "TP", 112: "Familiar TP"}

# In-game tooltip colours -> CSS classes (the page recolours them for parchment)
COLOURS = {"0,204,34": "g", "255,123,26": "o", "255,255,102": "y"}

ACTION_FIELDS = ["Name", "Icon", "ClassJobLevel", "ActionCategory.Name", "Range", "EffectRange",
                 "Cast100ms", "Recast100ms", "PrimaryCostType", "PrimaryCostValue", "MaxCharges",
                 "Aspect", "AttackType"]


def get(path, **params):
    url = f"{API}/{path}?" + urllib.parse.urlencode(params, safe="@(),")
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60))


def rows(sheet, ids, fields, transient=None):
    params = {"rows": ",".join(map(str, ids)), "fields": ",".join(fields)}
    if transient:
        params["transient"] = transient
    return {r["row_id"]: r for r in get(f"sheet/{sheet}", **params)["rows"]}


def tooltip(html):
    """Swap the game's inline colour styles for classes and drop the dynamic-name gap."""
    def span(m):
        cls = COLOURS.get(m.group(1))
        return f'<span class="tt-{cls}">' if cls else "<span>"
    html = re.sub(r'<span style="color:rgba\((\d+,\d+,\d+),1\);">', span, html or "")
    # Battlehorn tooltips name the slotted familiar at runtime; the sheet leaves it blank
    return re.sub(r'<span class="tt-\w"></span>', '<span class="tt-y">familiar</span>', html)


def plain(html):
    return re.sub(r"<[^>]+>", " ", html)


def action(r, desc):
    f = r["fields"]
    cost_type = COST.get(f["PrimaryCostType"])
    return {
        "id": r["row_id"],
        "name": f["Name"],
        "icon": f["Icon"]["id"],
        "level": f["ClassJobLevel"],
        "category": f["ActionCategory"]["fields"].get("Name", ""),
        "range": 3 if f["Range"] == -1 else f["Range"],
        "radius": f["EffectRange"],
        "cast": f["Cast100ms"] / 10,
        "recast": f["Recast100ms"] / 10,
        "cost": {"type": cost_type, "value": f["PrimaryCostValue"]} if cost_type else None,
        "charges": f["MaxCharges"] or None,
        "desc": tooltip(desc),
    }


def damage_kind(f):
    if f["Aspect"] in ASPECTS:
        return ASPECTS[f["Aspect"]]
    return ATTACK_TYPES.get((f.get("AttackType") or {}).get("value"), "Unaspected")


def fetch_icon(folder, icon):
    dest = os.path.join(ROOT, "assets", "icons", folder, f"{icon}.png")
    if not os.path.exists(dest):
        p = f"ui/icon/{icon // 1000 * 1000:06d}/{icon:06d}_hr1.tex"
        req = urllib.request.Request(f"{API}/asset?path={p}&format=png", headers=UA)
        with open(dest, "wb") as fh:
            fh.write(urllib.request.urlopen(req, timeout=30).read())


def main():
    ids = [i for _, g in GROUPS for i in g]
    acts = rows("Action", ids, ACTION_FIELDS, "Description@as(html)")
    groups = []
    for title, members in GROUPS:
        items = []
        for i in members:
            a = action(acts[i], acts[i]["transient"]["Description@as(html)"])
            if i in KINSHIP_CLASS:
                a["kinship"] = KINSHIP_CLASS[i]
            items.append(a)
        groups.append({"title": title, "actions": items})

    # Familiars: XBMPet (Unknown7 = classification, Unknown1/2 = short Trick / Tempered
    # Release blurbs, Unknown0 = lore) plus each beast's two Action rows.
    pets = get("sheet/XBMPet", limit=60, fields="Unknown0,Unknown1,Unknown2,Unknown7")["rows"]
    pets = {r["row_id"]: r["fields"] for r in pets if r["row_id"]}
    fam_ids = list(range(FAMILIAR_FIRST, FAMILIAR_FIRST + 2 * len(pets)))
    fam = {}
    for chunk in (fam_ids[:50], fam_ids[50:]):
        fam |= rows("Action", chunk, ACTION_FIELDS, "Description@as(html)")
    beasts = []
    for n in sorted(pets):
        trick, release = fam[FAMILIAR_FIRST + 2 * (n - 1)], fam[FAMILIAR_FIRST + 2 * (n - 1) + 1]
        t = action(trick, trick["transient"]["Description@as(html)"])
        rel = action(release, release["transient"]["Description@as(html)"])
        affinity = re.search(r"Instinctual Affinity:\s*(\w+)", plain(t["desc"])).group(1).lower()
        text = plain(rel["desc"]) + " " + plain(t["desc"])
        beasts.append({
            "n": n,
            "class": CLASSES[pets[n]["Unknown7"]],
            "affinity": affinity,
            "damage": damage_kind(trick["fields"]),
            "inflicts": [s for s in STATUS_WORDS if re.search(rf"\b{s}", text, re.I)],
            "lore": pets[n]["Unknown0"],
            "trick": t,
            "release": rel,
        })

    traits = get("search", sheets="Trait", query="ClassJob=43", limit=50,
                 fields="Name,Level,Icon", transient="Description@as(html)")["results"]
    traits = sorted(({"name": r["fields"]["Name"], "level": r["fields"]["Level"], "icon": r["fields"]["Icon"]["id"],
                      "desc": tooltip(r["transient"]["Description@as(html)"])} for r in traits),
                    key=lambda t: t["level"])

    data = {"groups": groups, "beasts": beasts, "traits": traits}
    with open(os.path.join(ROOT, "data", "actions.js"), "w", encoding="utf-8") as fh:
        fh.write("// Generated by tools/build_actions.py from FFXIV game data (via XIVAPI). (c) SQUARE ENIX\n")
        fh.write("window.BST_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n")

    icons = {a["icon"] for g in groups for a in g["actions"]} | {
        b[k]["icon"] for b in beasts for k in ("trick", "release")}
    jobs = [("actions", i) for i in sorted(icons)] + [("traits", t["icon"]) for t in traits]
    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda j: fetch_icon(*j), jobs))
    print(f"{len(ids)} actions, {len(beasts)} familiars, {len(traits)} traits, {len(icons)} icons")


if __name__ == "__main__":
    main()
