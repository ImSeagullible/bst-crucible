"""Build data/crucible.js (Crucible of the Unbroken boards) from game data via XIVAPI.

Usage:  python tools/build_crucible.py
Needs:  Pillow (for enemy portraits)

Most XBM* columns are still unnamed in EXDSchema, so this script reads them by
position. The column meanings below were decoded by comparing against in-game
screenshots and the game's UI text (Addon sheet); see the notes per table.
"""
import concurrent.futures as cf
import json
import os
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "tools", ".cache")
UA = {"User-Agent": "bst-fansite-assets/1.0"}
API = "https://v2.xivapi.com/api"

NODE_TYPES = {1: "start", 2: "enemy", 3: "elite", 4: "boss", 5: "shop", 6: "camp", 7: "treasure", 8: "random"}

# BNpcResist.Unknown0 slot order, inferred from each beast's InflictsStatus flags
# vs. its Tempered Release text (e.g. Lamb/Lullaby -> slot 7 = Sleep).
STATUS_SLOTS = ["Slow", "Petrification", "Paralysis", "Interrupt", "Blind", "Poison",
                "Stun", "Sleep", "Bind", "Heavy", "Doom"]

# The AttackType sheet only carries Japanese names.
ATTACK_TYPES = {"斬": "Slashing", "突": "Piercing", "打": "Blunt", "射": "Shot", "魔法": "Magic",
                "ブレス": "Breath", "音波": "Sound", "限界技": "Limit Break", "無": "Unaspected"}


def get(path, **params):
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60))


def sheet(name, fields, **extra):
    """All rows of a sheet (handles paging), as a list of (row_id, subrow_id, fields)."""
    rows, after = [], None
    while True:
        params = {"fields": ",".join(fields), "limit": 500, **extra}
        if after:
            params["after"] = after
        batch = get(f"sheet/{name}", **params)["rows"]
        rows += [(r["row_id"], r.get("subrow_id"), r["fields"]) for r in batch]
        if len(batch) < 500:
            return rows
        last = batch[-1]
        after = f'{last["row_id"]}' + (f':{last["subrow_id"]}' if "subrow_id" in last else "")


def text(link, key="Name"):
    """Pull a display string out of a linked-row field."""
    if isinstance(link, dict):
        return (link.get("fields") or {}).get(key) or ""
    return ""


def u(f, i):
    return f.get(f"Unknown{i}")


def main():
    os.makedirs(CACHE, exist_ok=True)

    # XBMContent: Unknown0-32 = points per XBMScoreBonus (0 = unavailable),
    # Unknown34 = team size, Unknown35 - 2 = recommended rank, Unknown36 = sync rank.
    content = {r: f for r, _, f in sheet("XBMContent", [f"Unknown{i}" for i in range(37)] + [
        "ContentFinderCondition.Name", "ContentFinderCondition.ClassJobLevelRequired",
        "ContentFinderCondition.ItemLevelRequired", "ContentFinderCondition.ItemLevelSync"]) if r}

    bonuses = [{"name": u(f, 0), "desc": u(f, 1)} for _, _, f in sheet("XBMScoreBonus", ["Unknown0", "Unknown1"])]

    # XBMContentStageEvent (row = board): Unknown0 type, Unknown1 move, Unknown2 index within type.
    events = {}
    for r, _, f in sheet("XBMContentStageEvent", ["Unknown0", "Unknown1", "Unknown2"]):
        if r:
            events.setdefault(r, []).append({"type": NODE_TYPES[u(f, 0)], "move": u(f, 1), "idx": u(f, 2)})

    # XBMContentStageEventMap: Unknown0 x, Unknown1 y, Unknown2 1 = node cell / else line cell,
    # Unknown3 node, Unknown4 linked node (the next node upward).
    positions, edges = {}, {}
    for r, _, f in sheet("XBMContentStageEventMap", ["Unknown0", "Unknown1", "Unknown2", "Unknown3", "Unknown4"]):
        x, y, kind, a, b = (u(f, i) for i in range(5))
        if not r or not (x or y or kind):
            continue
        if kind == 1:
            positions.setdefault(r, {})[a] = (x, y)
        else:
            edges.setdefault(r, set()).add((a, b))

    battles = {}
    for r, _, f in sheet("XBMContentBattle", ["BattleDetail"]):
        battles.setdefault(r, []).append(f["BattleDetail"]["value"])

    camps = {}
    for r, _, f in sheet("XBMContentCamp", ["Unknown0"]):
        camps.setdefault(r, []).append(u(f, 0))

    random_sets = {}
    for r, _, f in sheet("XBMContentRandomStageEvent", ["RandomStageEvent"]):
        random_sets.setdefault(r, []).append(f["RandomStageEvent"]["value"])
    random_opts = {}
    for r, _, f in sheet("XBMRandomStageEvent", ["Unknown0", "Unknown1"]):
        random_opts.setdefault(r, []).append({"type": NODE_TYPES.get(u(f, 0), "?"), "idx": u(f, 1)})

    actions = {}
    for r, _, f in sheet("XBMBattleDetailAction", ["Action.Name", "Action.AttackType.Name", "Status.Name",
                                                    "ActionTarget.Unknown0", "ActionEffectType.Unknown0"]):
        actions[r] = {
            "name": text(f.get("Action")),
            "damage": ATTACK_TYPES.get(text((f.get("Action") or {}).get("fields", {}).get("AttackType")), ""),
            "status": text(f.get("Status")),
            "target": text(f.get("ActionTarget"), "Unknown0"),
            "area": text(f.get("ActionEffectType"), "Unknown0"),
        }

    # XBMBattleDetail (row = encounter, subrow = enemy). Unknown5-9 are the 1-5 star ratings;
    # Element is the enemy's weakness; Resist flags statuses it is immune to.
    encounters, icons = {}, set()
    for r, _, f in sheet("XBMBattleDetail", ["Name.Singular", "Unknown1", "Unknown2", "Unknown3",
                                              "Resist.Unknown0", "Unknown5", "Unknown6", "Unknown7",
                                              "Unknown8", "Unknown9", "Element.Name"]):
        if not r:
            continue
        icon = (f.get("Unknown1") or {}).get("id", 0)
        icons.add(icon)
        immune = (f.get("Resist") or {}).get("fields", {}).get("Unknown0") or [False] * 11
        name = text(f.get("Name"), "Singular")
        encounters.setdefault(r, []).append({
            "name": name[:1].upper() + name[1:],
            "icon": icon,
            "weakness": text(f.get("Element")).strip(),
            "stars": [u(f, i) for i in range(5, 10)],
            "vulnerable": [s for s, imm in zip(STATUS_SLOTS, immune) if not imm],
            "actions": [actions[a] for a in ((f.get("Unknown2") or {}).get("value"), (f.get("Unknown3") or {}).get("value"))
                        if a and a in actions],
        })

    boards = []
    for b in sorted(content):
        c = content[b]
        cfc = (c.get("ContentFinderCondition") or {}).get("fields", {})
        nodes = []
        for i, ev in enumerate(events[b]):
            node = {"id": i, **ev, "x": positions[b][i][0], "y": positions[b][i][1]}
            if ev["type"] in ("enemy", "elite", "boss"):
                node["encounter"] = battles[b][ev["idx"]]
            elif ev["type"] == "camp":
                node["familiars"] = camps[b][ev["idx"]]
            elif ev["type"] == "random":
                opts = random_opts[random_sets[b][ev["idx"]]]
                node["options"] = [
                    {**o, **({"encounter": battles[b][o["idx"]]} if o["type"] in ("enemy", "elite") else {}),
                     **({"familiars": camps[b][o["idx"]] if o["idx"] < len(camps[b]) else None} if o["type"] == "camp" else {})}
                    for o in opts]
            nodes.append(node)
        boards.append({
            "id": b,
            "name": cfc.get("Name", f"Board {b}"),
            "level": cfc.get("ClassJobLevelRequired"),
            "itemLevel": cfc.get("ItemLevelRequired"),
            "itemLevelSync": cfc.get("ItemLevelSync"),
            "teamSize": u(c, 34),
            "recommendedRank": u(c, 35) - 2,
            "syncRank": u(c, 36),
            "bonuses": [{**bonuses[i], "points": u(c, i)} for i in range(33) if u(c, i)],
            "nodes": nodes,
            "edges": sorted(edges[b]),
        })

    used = {n.get("encounter") for bd in boards for n in bd["nodes"]} | {
        o.get("encounter") for bd in boards for n in bd["nodes"] for o in n.get("options", [])}
    data = {
        "boards": boards,
        "encounters": {k: v for k, v in encounters.items() if k in used},
        "statusSlots": STATUS_SLOTS,
    }
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", "crucible.js"), "w", encoding="utf-8") as fh:
        fh.write("// Generated by tools/build_crucible.py from FFXIV game data (via XIVAPI). (c) SQUARE ENIX\n")
        fh.write("window.CRUCIBLE = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n")

    # enemy portraits
    out_dir = os.path.join(ROOT, "assets", "icons", "enemies")
    os.makedirs(out_dir, exist_ok=True)

    def fetch_icon(i):
        dest = os.path.join(out_dir, f"{i}.png")
        if i and not os.path.exists(dest):
            p = f"ui/icon/{i // 1000 * 1000:06d}/{i:06d}_hr1.tex"
            req = urllib.request.Request(f"{API}/asset?path={p}&format=png", headers=UA)
            with open(dest, "wb") as fh:
                fh.write(urllib.request.urlopen(req, timeout=30).read())
    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(fetch_icon, sorted(icons)))

    print(f"{len(boards)} boards, {len(data['encounters'])} encounters, {len(icons - {0})} enemy portraits")


if __name__ == "__main__":
    main()
