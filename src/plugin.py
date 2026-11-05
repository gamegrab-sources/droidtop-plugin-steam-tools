"""Steam tools for droidtop: an unofficial gamegrab-sources plugin, not part of droidtop and not
affiliated with or endorsed by Valve or ArchiSteamFarm.

What people use ArchiSteamFarm and its plugins for, on the Steam account droidtop is already
signed in to (droidtop docs/plugin-api.md 3 G8, `steam.*`): this plugin never signs in, and never
sees a password, token or cookie.

- Free games (after the FreePackages plugin's idea): a background service (`jobs.service`) reads
  Steam's product changes, keeps the apps and packages that are free to own, and claims them.
  droidtop keeps Steam's hourly limit for the account; what does not fit waits for the next hour.
- Keys: paste keys, and a job redeems them one by one with each result.
- Shown as played (optional, off): the games the person names are shown as played while the
  service runs, renewed before droidtop's 12-hour bound runs out.

It runs contained (5.3): every call goes through droidtop's broker, every byte it keeps through
droidtop's data API, and it draws nothing itself.
"""
import re
import time

import gamegrab as gg  # embedded by build.sh

SETTINGS = "settings.json"
STATE = "state.json"
DEFAULTS = {
    "claim": True,
    "demos": False,
    "playtests": False,
    "freeWeekends": False,
    "play": False,
    "playApps": "",
}

# How often the service looks at Steam's changes, and how much one look reads.
CYCLE_S = 30 * 60
MAX_CHANGED = 300
BATCH = 100
KEEP_CLAIMED = 200
KEEP_KEYS = 100

# Steam's billing types (EBillingType): free on demand is what a "free to own" package is.
FREE_ON_DEMAND = "12"

plugin = gg.Plugin("Steam tools")


def settings():
    kept = gg.read_json(SETTINGS, {}) or {}
    out = dict(DEFAULTS)
    for key, value in DEFAULTS.items():
        if key in kept:
            out[key] = kept[key] if isinstance(value, str) else bool(kept[key])
    return out


def state():
    kept = gg.read_json(STATE, {}) or {}
    return {
        "since": int(kept.get("since") or 0),
        "queue": [q for q in kept.get("queue") or [] if isinstance(q, dict)],
        "claimed": [c for c in kept.get("claimed") or [] if isinstance(c, dict)],
        "refused": [int(x) for x in kept.get("refused") or [] if str(x).isdigit()],
        "keys": [k for k in kept.get("keys") or [] if isinstance(k, dict)],
        "playUntil": int(kept.get("playUntil") or 0),
    }


def save_state(s):
    s["claimed"] = s["claimed"][-KEEP_CLAIMED:]
    s["keys"] = s["keys"][-KEEP_KEYS:]
    s["refused"] = s["refused"][-2000:]
    gg.write_json(STATE, s)


# ------------------------------------------------------------------ what is free


def is_free_package(facts, opts):
    """A package that is free to own: billed as free on demand, on sale (status 0), and not time-limited
    unless free weekends are wanted."""
    if str(facts.get("billingtype", "")) != FREE_ON_DEMAND:
        return False
    if str(facts.get("status", "0")) != "0":
        return False
    limited = facts.get("extended/expirytime") or facts.get("extended/freeweekend") == "1"
    if limited and not opts["freeWeekends"]:
        return False
    return True


def is_free_app(facts, opts):
    """An app Steam marks as a free app to own: a game or DLC, a demo or playtest only when wanted."""
    if str(facts.get("extended/isfreeapp", facts.get("common/isfreeapp", ""))) != "1":
        return False
    kind = str(facts.get("common/type", "")).lower()
    name = str(facts.get("common/name", ""))
    if kind == "demo":
        return opts["demos"]
    if "playtest" in name.lower():
        return opts["playtests"]
    return kind in ("game", "dlc")


def name_of(facts):
    return gg.cut(str(facts.get("common/name") or facts.get("name") or ""), 120)


# ------------------------------------------------------------------ one look at Steam


def look(now=None):
    """One pass: read the changes since the last one, queue what became free, claim what fits. Returns
    the line the service reports."""
    opts = settings()
    s = state()
    changes = gg.call("steam", "list_changes", {"since": s["since"]})
    current = int(changes.get("current") or 0)
    if s["since"] == 0 or changes.get("fullUpdateNeeded"):
        # The first look only learns where Steam is: nothing older is read.
        s["since"] = current
        save_state(s)
        return "Watching Steam for free games"
    seen = {(q["kind"], q["id"]) for q in s["queue"]} | {(c["kind"], c["id"]) for c in s["claimed"]}
    refused = set(s["refused"])
    apps = [a for a in changes.get("apps") or [] if ("app", a) not in seen and a not in refused][:MAX_CHANGED]
    packages = [p for p in changes.get("packages") or [] if ("package", p) not in seen and p not in refused][:MAX_CHANGED]
    for i in range(0, max(len(apps), len(packages)), BATCH):
        info = gg.call("steam", "get_product_info", {"apps": apps[i : i + BATCH], "packages": packages[i : i + BATCH]})
        for key, facts in (info.get("apps") or {}).items():
            if is_free_app(facts, opts):
                s["queue"].append({"kind": "app", "id": int(key), "name": name_of(facts)})
        for key, facts in (info.get("packages") or {}).items():
            if is_free_package(facts, opts):
                s["queue"].append({"kind": "package", "id": int(key), "name": name_of(facts)})
    s["since"] = current
    claimed = claim(s, now) if opts["claim"] else 0
    save_state(s)
    if claimed:
        gg.notify("Free Steam games claimed", "%d added to your Steam library" % claimed)
    return "Claimed %d free games; %d waiting" % (claimed, len(s["queue"])) if (claimed or s["queue"]) else "No new free games"


def claim(s, now=None):
    """Claims what is queued, as much as droidtop lets go now; the rest stays queued. Returns how many."""
    if not s["queue"]:
        return 0
    batch = s["queue"][:30]
    answer = gg.call(
        "steam",
        "claim_free",
        {"apps": [q["id"] for q in batch if q["kind"] == "app"], "packages": [q["id"] for q in batch if q["kind"] == "package"]},
    )
    names = {(q["kind"], q["id"]): q.get("name", "") for q in batch}
    done = set()
    count = 0
    stamp = int(now if now is not None else time.time())
    for r in answer.get("results") or []:
        key = (r.get("kind"), r.get("id"))
        if key not in names:
            continue
        if r.get("claimed"):
            s["claimed"].append({"kind": key[0], "id": key[1], "name": names[key], "at": stamp})
            done.add(key)
            count += 1
        elif r.get("reason") not in ("later", "rate_limited"):
            # Steam would not give it: not asked again.
            s["refused"].append(key[1])
            done.add(key)
    s["queue"] = [q for q in s["queue"] if (q["kind"], q["id"]) not in done]
    return count


# ------------------------------------------------------------------ shown as played


def play_apps(text):
    """The app ids the person typed (any separators), each once, at most 32."""
    out = []
    for token in re.findall(r"\d+", text or ""):
        value = int(token)
        if 0 < value < 2**31 and value not in out:
            out.append(value)
    return out[:32]


def renew_play(now=None):
    """Keeps the chosen games shown as played while the setting is on, renewed before droidtop's bound."""
    opts = settings()
    s = state()
    now = int(now if now is not None else time.time())
    apps = play_apps(opts["playApps"])
    if not opts["play"] or not apps:
        if s["playUntil"]:
            gg.call("steam", "stop", {})
            s["playUntil"] = 0
            save_state(s)
        return
    if s["playUntil"] - now > CYCLE_S * 2:
        return
    answer = gg.call("steam", "play", {"apps": apps, "minutes": 12 * 60})
    s["playUntil"] = now + 12 * 3600 if answer.get("playing") else 0
    save_state(s)


# ------------------------------------------------------------------ the service


@plugin.job("jobs.service", "run")
def _service(args, report):
    """droidtop's background service: a look every half hour while it runs."""
    while True:
        try:
            line = look()
            renew_play()
        except gg.HostError as e:
            if e.code == "SIGN_IN_REQUIRED":
                line = "Sign in to Steam under Stores to claim free games"
            elif e.code == "PERMISSION_DENIED":
                return gg.failed("Allow Steam tools to use your Steam account on its page")
            else:
                line = "Steam could not be asked: " + gg.cut(e.message, 120)
        report(-1, line)
        time.sleep(CYCLE_S)


# ------------------------------------------------------------------ keys


def split_keys(text):
    """Steam keys in pasted text (AAAAA-BBBBB-CCCCC and longer forms), each once, at most 50."""
    out = []
    for key in re.findall(r"[A-Za-z0-9]{4,6}(?:-[A-Za-z0-9]{4,6}){2,4}", text or ""):
        key = key.upper()
        if key not in out:
            out.append(key)
    return out[:50]


RESULT_WORDS = {
    "ok": "Added",
    "already_owned": "Already owned",
    "invalid": "Not a valid key",
    "already_redeemed": "Already redeemed",
    "region_locked": "Not for your region",
    "needs_base_game": "Needs the base game",
    "rate_limited": "Steam asked to slow down",
    "no_session": "Steam's store could not be reached",
    "failed": "Steam refused it",
}


def mask(key):
    return key[:5] + "-…" if len(key) > 5 else key


@plugin.job("ui.facets", "redeem")
def _redeem(args, report):
    # From the page's form, droidtop sends what was entered as args.values (plugin-api 1.10, a form group).
    pasted = args.get("keys") or (args.get("values") or {}).get("keys") or ""
    keys = split_keys(str(pasted))
    if not keys:
        return gg.failed("No Steam keys in what was pasted")
    s = state()
    added = 0
    for i, key in enumerate(keys):
        report(int(i * 100 / len(keys)), "Redeeming %d of %d" % (i + 1, len(keys)))
        while True:
            try:
                answer = gg.call("steam", "redeem_key", {"key": key})
                break
            except gg.HostError as e:
                if e.code == "RATE_LIMITED" and "every" in e.message:
                    time.sleep(10)
                    continue
                answer = {"result": "rate_limited" if e.code == "RATE_LIMITED" else "failed", "items": []}
                break
        result = str(answer.get("result") or "failed")
        items = [gg.cut(str(x), 120) for x in answer.get("items") or []]
        s["keys"].append({"key": mask(key), "result": result, "items": items, "at": int(time.time())})
        if result == "ok":
            added += 1
        if result == "rate_limited":
            break
    save_state(s)
    return gg.done("Redeemed %d of %d keys" % (added, len(keys)))


# ------------------------------------------------------------------ settings (C)


SETTINGS_TARGET = "settings.page"
SWITCHES = [
    ("claim", "Claim free games", "Watches Steam and adds games that are free to own to your library"),
    ("demos", "Include demos", None),
    ("playtests", "Include playtests", None),
    ("freeWeekends", "Include free weekends", "Licences that end when the free weekend does"),
    ("play", "Show games as played", "The games below show as played while the service runs (play time, card drops)"),
]


def page_facets():
    s = settings()
    st = state()
    facets = []
    for key, label, sub in SWITCHES:
        facet = {"id": key, "kind": "toggle", "v": 1, "targets": [SETTINGS_TARGET], "label": label, "value": s[key], "call": {"op": "set", "args": {"key": key}}}
        if sub:
            facet["sub"] = sub
        facets.append(facet)
    facets.append({
        "id": "playApps", "kind": "input", "v": 1, "targets": [SETTINGS_TARGET], "label": "Games to show as played",
        "value": s["playApps"], "hint": "Steam app ids, e.g. 440 570", "inputKind": "text", "call": {"op": "set", "args": {"key": "playApps"}},
    })
    facets.append({
        "id": "keys", "kind": "group", "v": 1, "targets": [SETTINGS_TARGET], "title": "Redeem keys", "form": True,
        "facets": [{"id": "keys", "kind": "input", "v": 1, "label": "Keys", "value": "", "hint": "Paste one or more keys", "inputKind": "text"}],
        "submit": {"op": "redeem", "kind": "job"},
    })
    claimed = list(reversed(st["claimed"]))[:50]
    facets.append({
        "id": "claimed", "kind": "list", "v": 1, "targets": [SETTINGS_TARGET],
        "title": "Claimed (%d)" % len(st["claimed"]) if st["claimed"] else "Nothing claimed yet",
        "entries": [{"id": "%s-%d" % (c["kind"], c["id"]), "title": c.get("name") or "%s %d" % (c["kind"].capitalize(), c["id"]), "sub": time.strftime("%Y-%m-%d", time.gmtime(c.get("at") or 0))} for c in claimed],
    })
    if st["queue"]:
        facets.append({"id": "waiting", "kind": "info", "v": 1, "targets": [SETTINGS_TARGET], "label": "Waiting for Steam's hourly limit", "value": str(len(st["queue"]))})
    keys = list(reversed(st["keys"]))[:20]
    if keys:
        facets.append({
            "id": "redeemed", "kind": "list", "v": 1, "targets": [SETTINGS_TARGET], "title": "Keys redeemed",
            "entries": [{"id": "k%d" % i, "title": k["key"], "sub": ", ".join(k.get("items") or []) or None, "value": RESULT_WORDS.get(k["result"], k["result"])} for i, k in enumerate(keys)],
        })
    return {"facets": facets}


@plugin.on("ui.facets", "get")
def _facets(args):
    if args.get("target") == SETTINGS_TARGET:
        return gg.ok(page_facets())
    return gg.ok({"facets": []})


@plugin.on("ui.facets", "set")
def _set(args):
    key = args.get("key")
    if key not in DEFAULTS:
        return gg.error("INVALID_ARGS", "Unknown setting")
    s = settings()
    value = args.get("value")
    if isinstance(DEFAULTS[key], str):
        s[key] = gg.cut(str(value or ""), 400)
    else:
        s[key] = value is True or str(value).lower() == "true"
    gg.write_json(SETTINGS, s)
    if key in ("play", "playApps"):
        try:
            renew_play()
        except gg.HostError:
            pass
    return gg.ok(page_facets())


handle, start_job, cancel_job = plugin.entry_points()
