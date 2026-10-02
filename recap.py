#!/usr/bin/env python3
"""Weekly fantasy recap: Sleeper stats -> facts -> Claude-written jokes -> static site in docs/.

  python recap.py              recap the latest scored week, then rebuild docs/
  WEEK=3 python recap.py       recap a specific week
  FRESH=true python recap.py   rewrite this week's jokes even if they already exist
  python recap.py render       rebuild docs/ from weeks/*.json only (no network)
"""
import html
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

LEAGUE = os.environ.get("LEAGUE_ID", "1393873211271188480")
SITE = os.environ.get("SITE_URL", "https://double-dip.alexeldeib.xyz/")
MODEL = "claude-opus-5-5"
ROOT = Path(__file__).resolve().parent
SLEEPER = "https://api.sleeper.app/v1"
PROJ = ("https://api.sleeper.com/projections/nfl/{}/{}?season_type=regular"
        "&position%5B%5D=QB&position%5B%5D=RB&position%5B%5D=WR&position%5B%5D=TE&position%5B%5D=K&position%5B%5D=DEF")
FLEX = {"FLEX": {"RB", "WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
        "REC_FLEX": {"WR", "TE"}, "WRRB_FLEX": {"WR", "RB"}}
HURT = {"Out", "IR", "Doubtful", "Sus", "PUP", "NA"}
STATS = [("pass_yd", "pass yds"), ("pass_td", "pass TD"), ("pass_int", "INT"), ("rush_yd", "rush yds"),
         ("rush_td", "rush TD"), ("rec", "rec"), ("rec_yd", "rec yds"), ("rec_td", "rec TD"),
         ("fum_lost", "fumbles lost")]


def get(url, fallback=None):
    """Fetch JSON. Required endpoints raise; optional ones pass a fallback."""
    try:  # api.sleeper.com 403s the default Python user agent
        with urlopen(Request(url, headers={"User-Agent": "double-dipper-recap"}), timeout=90) as r:
            return json.load(r)
    except Exception:
        if fallback is None:
            raise
        print(f"::warning::{url.split('?')[0]} failed; carrying on without it.")
        return fallback


def pairs(matchups):
    games = defaultdict(list)
    for m in matchups:
        if m.get("matchup_id"):
            games[m["matchup_id"]].append(m)
    return [g for _, g in sorted(games.items()) if len(g) == 2]


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def build_facts(week=None):
    api = f"{SLEEPER}/league/{LEAGUE}"
    lg, users, rosters = get(api), get(api + "/users"), get(api + "/rosters")
    P = get(f"{SLEEPER}/players/nfl")
    season, cfg = lg["season"], lg["settings"]
    week = int(week or cfg.get("last_scored_leg") or 0)
    if week < 1:
        sys.exit("No scored weeks yet.")
    slots = [s for s in lg["roster_positions"] if s not in ("BN", "IR", "TAXI")]
    pts_key = {1: "pts_ppr", 0.5: "pts_half_ppr"}.get(lg["scoring_settings"].get("rec", 0), "pts_std")

    user = {u["user_id"]: u for u in users}
    teams, team = [], {}
    for r in sorted(rosters, key=lambda r: r["roster_id"]):
        u = user.get(r["owner_id"]) or {}
        manager = u.get("display_name") or f"team{r['roster_id']}"
        team[r["roster_id"]] = (u.get("metadata") or {}).get("team_name") or manager
        avatar = (u.get("metadata") or {}).get("avatar") or (
            u.get("avatar") and f"https://sleepercdn.com/avatars/thumbs/{u['avatar']}")
        teams.append(dict(team=team[r["roster_id"]], manager=manager, avatar=avatar, commish=bool(u.get("is_owner"))))

    def info(pid):
        return P.get(pid) or {}

    def fits(pid, slot):
        p = info(pid)
        return bool(set(p.get("fantasy_positions") or [p.get("position") or "DEF"]) & FLEX.get(slot, {slot}))

    def name(pid):
        p = info(pid)
        if pid == "0":
            return "an empty slot"
        if p.get("position") == "DEF" or not p.get("last_name"):
            return f"{pid} D/ST"
        return f"{p['first_name'][:1]}. {p['last_name']}"

    def optimal(pp):
        # ponytail: fill the pickiest slots first. Exact when slots nest (QB < FLEX < SUPER_FLEX);
        # overlapping flexes (REC_FLEX vs WRRB_FLEX) can come up a hair short.
        left, total = dict(pp), 0.0
        for slot in sorted(slots, key=lambda s: len(FLEX.get(s, {s}))):
            best = max((p for p in left if fits(p, slot)), key=left.get, default=None)
            if best is not None:
                total += left.pop(best)
        return round(total, 2)

    # Season to date: record, all-play, bench points left.
    weekly = {w: get(f"{api}/matchups/{w}") for w in range(1, week + 1)}
    last_regular = min(week, cfg.get("playoff_week_start", 99) - 1)
    rec = {rid: dict(w=0, l=0, t=0, hw=0, h2h=0, pf=0.0, pa=0.0, ap_w=0, ap_l=0, bench=0.0, weeks=[]) for rid in team}
    snaps = {}  # week -> {rid: (all-play win rate, points for)} through that week
    for w in range(1, last_regular + 1):
        ms = [m for m in weekly[w] if m.get("matchup_id")]
        score = {m["roster_id"]: m["points"] for m in ms}
        for m in ms:
            r, others = rec[m["roster_id"]], [v for k, v in score.items() if k != m["roster_id"]]
            r["pf"] += m["points"]
            r["weeks"].append(m["points"])
            r["bench"] += optimal(m["players_points"]) - m["points"]
            r["ap_w"] += sum(m["points"] > v for v in others)
            r["ap_l"] += sum(m["points"] < v for v in others)
        for a, b in pairs(ms):
            for x, y in ((a, b), (b, a)):
                r = rec[x["roster_id"]]
                r["pa"] += y["points"]
                r["h2h"] += 1
                r["hw"] += x["points"] > y["points"]
                r["w" if x["points"] > y["points"] else "l" if x["points"] < y["points"] else "t"] += 1
        if cfg.get("league_average_match"):
            v = sorted(score.values())
            median = (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2
            for rid, p in score.items():
                rec[rid]["w" if p > median else "l"] += 1
        snaps[w] = {rid: (r["ap_w"] / max(1, r["ap_w"] + r["ap_l"]), r["pf"]) for rid, r in rec.items()}
    standings = []
    for rid, r in rec.items():
        ap = r["ap_w"] + r["ap_l"]
        standings.append(dict(team=team[rid], w=r["w"], l=r["l"], t=r["t"], pf=round(r["pf"], 2), pa=round(r["pa"], 2),
                              all_play=f"{r['ap_w']}-{r['ap_l']}", bench_left=round(r["bench"], 1),
                              pa_per_game=round(r["pa"] / r["h2h"], 1) if r["h2h"] else 0,
                              luck=round(r["hw"] - (r["ap_w"] / ap * r["h2h"] if ap else 0), 2), weekly_scores=r["weeks"]))
    standings.sort(key=lambda s: (s["w"] + s["t"] / 2, s["pf"]), reverse=True)

    # This week, per team.
    stats = get(f"{SLEEPER}/stats/nfl/regular/{season}/{week}", {})
    proj = {x["player_id"]: (x.get("stats") or {}).get(pts_key) or 0 for x in get(PROJ.format(season, week), [])}

    def line(pid):
        s = stats.get(pid) or {}
        return ", ".join(f"{s[k]:g} {label}" for k, label in STATS if s.get(k))

    ms = [m for m in weekly[week] if m.get("matchup_id")]
    card, lineups = {}, {}
    for m in ms:
        rid, pp, st = m["roster_id"], m["players_points"], m["starters"]
        bench = [p for p in m["players"] if p not in st]
        opt = optimal(pp)
        gain, b, s = max(((pp.get(b, 0) - pp.get(s, 0), b, s)
                          for s, slot in zip(st, slots) for b in bench if fits(b, slot)), default=(0, None, None))
        top = max((p for p in st if p != "0"), key=lambda p: pp.get(p, 0))
        card[rid] = dict(
            team=team[rid], pts=m["points"], opt=opt, eff=round(100 * m["points"] / opt, 1) if opt else 100.0,
            proj=round(sum(proj.get(p, 0) for p in st), 1) if proj else None,
            bench_pts=round(sum(pp.get(p, 0) for p in bench), 2),
            top=dict(player=name(top), pts=pp.get(top, 0)),
            swap=dict(bench=name(b), bench_pts=pp.get(b, 0), start=name(s), start_pts=pp.get(s, 0),
                      gain=round(gain, 2)) if gain > 0 else None)
        lineups[team[rid]] = dict(
            started=[dict(slot=slot, player=info(p).get("full_name") or name(p), pts=pp.get(p, 0), proj=proj.get(p), line=line(p))
                     for p, slot in zip(st, slots) if p != "0"],
            bench=[dict(player=info(p).get("full_name") or name(p), pos=info(p).get("position"), pts=pp.get(p, 0), line=line(p)) for p in bench])
    games = []
    for a, b in pairs(ms):
        if a["points"] < b["points"]:
            a, b = b, a
        games.append(dict(win=card[a["roster_id"]], lose=card[b["roster_id"]], margin=round(a["points"] - b["points"], 2)))

    # Trophies: the numbers are picked here; Claude only writes the jokes.
    awards = []

    def award(key, emoji, label, who, stat, vs=None):
        awards.append(dict(key=key, emoji=emoji, label=label, team=who, stat=stat, vs=vs))

    def swap_vs(sw):
        return sw and dict(a=sw["bench"], a_pts=sw["bench_pts"], a_tag="bench",
                           b=sw["start"], b_pts=sw["start_pts"], b_tag="started")

    def vs_proj(t):
        return f" ({t['pts'] - t['proj']:+.1f} vs proj)" if t.get("proj") else ""

    ranked = sorted(card.values(), key=lambda t: -t["pts"])
    blow, close = max(games, key=lambda g: g["margin"]), min(games, key=lambda g: g["margin"])
    award("blowout", "💣", "Biggest domination", blow["win"]["team"],
          f"by {blow['margin']:.2f} over {blow['lose']['team']}")
    award("high", "🥇", "Top score", ranked[0]["team"], f"{ranked[0]['pts']:.2f}{vs_proj(ranked[0])}")
    award("low", "💩", "Biggest loser", ranked[-1]["team"], f"{ranked[-1]['pts']:.2f}{vs_proj(ranked[-1])}")
    award("close", "🤏", "Closest game", close["win"]["team"], f"by {close['margin']:.2f} over {close['lose']['team']}")
    flips = [(g["lose"]["swap"]["gain"] - g["margin"], g) for g in games
             if g["lose"]["swap"] and g["lose"]["swap"]["gain"] > g["margin"]]
    if flips:
        by, g = min(flips, key=lambda x: x[0])
        award("heartbreaker", "💔", "Heartbreaker", g["lose"]["team"],
              f"lost by {g['margin']:.2f}, one swap from winning by {by:.2f}", swap_vs(g["lose"]["swap"]))
    best = max(ranked, key=lambda t: (t["eff"], t["pts"]))
    worst = min(ranked, key=lambda t: (t["eff"], t["pts"]))
    award("best_mgr", "🔥", "Best manager", best["team"], f"{best['eff']:g}% of max ({best['opt']:.2f})")
    award("worst_mgr", "🤡", "Worst manager", worst["team"], f"{worst['eff']:g}% of max ({worst['opt']:.2f})",
          swap_vs(worst["swap"]))
    deep = max(ranked, key=lambda t: t["bench_pts"])
    award("best_bench", "🪑", "Best bench", deep["team"], f"{deep['bench_pts']:.2f} on the bench")
    starts = [(m["players_points"].get(p, 0), p, m["roster_id"]) for m in ms for p in m["starters"] if p != "0"]
    pts, pid, rid = max(starts)
    award("mvp", "💪", "Best player", team[rid], f"{name(pid)} {pts:.2f}")
    pts, pid, m = max(((m["players_points"].get(p, 0), p, m) for m in ms for p in m["players"]
                       if p not in m["starters"]), key=lambda x: x[:2])
    sat = min(((m["players_points"].get(s, 0), s) for s, slot in zip(m["starters"], slots) if fits(pid, slot)),
              default=None)
    award("bench_mvp", "🛋️", "Bench MVP", team[m["roster_id"]], f"{name(pid)} {pts:.2f}",
          sat and dict(a=name(pid), a_pts=pts, a_tag="bench", b=name(sat[1]), b_pts=sat[0], b_tag="started"))
    diffs = [(p - proj[pid], p, pid, rid) for p, pid, rid in starts if proj.get(pid)]
    if diffs:
        d, p, pid, rid = max(diffs)
        award("over", "📈", "Overachiever", team[rid], f"{name(pid)} {p:.2f}, {d:+.1f} vs proj")
        d, p, pid, rid = min(diffs)
        award("under", "👎", "Underachiever", team[rid], f"{name(pid)} {p:.2f}, {d:+.1f} vs proj")
    lucky = min((g["win"] for g in games), key=lambda t: t["pts"])
    unlucky = max((g["lose"] for g in games), key=lambda t: t["pts"])
    award("lucky", "🍀", "Lucky", lucky["team"], f"won with the {ordinal(ranked.index(lucky) + 1)}-best score")
    award("unlucky", "😡", "Unlucky", unlucky["team"], f"lost with the {ordinal(ranked.index(unlucky) + 1)}-best score")

    # Gems: cross-roster comparisons a model won't reliably compute on its own.
    gems = []
    scores = sorted((t["pts"], t["team"]) for t in card.values())
    low_pts, low_team = scores[0]
    for t in card.values():
        beaten = [n for p, n in scores if p < t["bench_pts"] and n != t["team"]]
        if beaten:
            gems.append(f"{t['team']}'s bench ({t['bench_pts']:.2f}) outscored these whole lineups: {', '.join(beaten)}")
        elif t["team"] != low_team and low_pts - t["bench_pts"] < 10:
            gems.append(f"{t['team']}'s bench ({t['bench_pts']:.2f}) finished {low_pts - t['bench_pts']:.2f} "
                        f"behind {low_team}'s whole lineup ({low_pts:.2f})")
    low_rid = next(rid for rid, t in card.items() if t["team"] == low_team)
    low_starts = sorted(p for p, _, rid in starts if rid == low_rid)
    for pts, pid, rid in sorted(starts, reverse=True)[:3]:
        k, total = 0, 0.0
        while k < len(low_starts) and total + low_starts[k] < pts:
            total += low_starts[k]
            k += 1
        if k >= 2 and rid != low_rid:
            gems.append(f"{name(pid)} ({pts:.2f}, {team[rid]}) outscored {low_team}'s {k} lowest starters combined ({total:.2f})")
    game_of = {t["team"]: i for i, g in enumerate(games) for t in (g["win"], g["lose"])}
    near = min(((abs(a[0] - b[0]), a, b) for i, a in enumerate(scores) for b in scores[i + 1:]
                if game_of[a[1]] != game_of[b[1]]), default=None)
    if near:
        gems.append(f"{near[1][1]} ({near[1][0]:.2f}) and {near[2][1]} ({near[2][0]:.2f}) finished {near[0]:.2f} apart in different games")
    for g in games:
        if g["win"]["proj"] and g["win"]["proj"] == g["lose"]["proj"]:
            gems.append(f"{g['win']['team']} and {g['lose']['team']} were both projected for {g['win']['proj']}, "
                        f"then finished {g['margin']:.2f} apart")
    same_name = defaultdict(set)
    for m in ms:
        for p in m["players"]:
            same_name[name(p)].add((p, team[m["roster_id"]], m["players_points"].get(p, 0)))
        words = {re.sub(r"'s$", "", w).lower() for w in re.findall(r"[A-Za-z']{4,}", team[m["roster_id"]])}
        for mm in ms:
            for p in mm["players"]:
                pts, started = mm["players_points"].get(p, 0), p in mm["starters"]
                notable = pts >= 20 or (started and pts <= 5) or (not started and pts >= 15)  # only when the namesake did something
                if notable and {info(p).get("first_name", "").lower(), info(p).get("last_name", "").lower()} & words:
                    how = "started" if p in mm["starters"] else "benched"
                    gems.append(f"{team[m['roster_id']]} shares a name with {info(p).get('full_name')}, who scored "
                                f"{mm['players_points'].get(p, 0):.2f} ({how}) for {team[mm['roster_id']]}")
    gems += [f"Two players named {n}, on " + " and ".join(sorted(t for _, t, _ in v))
             for n, v in same_name.items() if len(v) > 1 and max(pts for *_, pts in v) >= 20]

    pickups = []
    for t in get(f"{api}/transactions/{week}", []):
        if t.get("status") == "complete":
            for pid, rid in (t.get("adds") or {}).items():
                m = next((m for m in ms if m["roster_id"] == rid), None)
                pickups.append(dict(team=team[rid], player=name(pid), bid=(t.get("settings") or {}).get("waiver_bid"),
                                    pts=m["players_points"].get(pid, 0) if m else 0,
                                    started=bool(m) and pid in m["starters"]))
    spend = max((x for x in pickups if x["bid"]), key=lambda x: x["bid"], default=None)
    if spend:
        award("big_spender", "💸", "Big spender", spend["team"],
              f"${spend['bid']} on {spend['player']}, {spend['pts']:.1f} pts")

    # Next week: pairings, projections for the lineups as currently set, and lineup PSAs.
    nxt = None
    # Only the latest week gets a preview: lineups and injury tags are live data, wrong for past weeks.
    latest = week == int(cfg.get("last_scored_leg") or 0)
    upcoming = [m for m in get(f"{api}/matchups/{week + 1}", []) if m.get("matchup_id")] if latest else []
    if upcoming:
        nproj = {x["player_id"]: (x.get("stats") or {}).get(pts_key) or 0
                 for x in get(PROJ.format(season, week + 1), [])}
        playing = {t for g in get(f"https://api.sleeper.com/schedule/nfl/regular/{season}", [])
                   if g.get("week") == week + 1 for t in (g.get("home"), g.get("away"))}
        lineup = {r["roster_id"]: [p for p in r.get("starters") or [] if p != "0"] for r in rosters}

        def projected(rid):
            return round(sum(nproj.get(p, 0) for p in lineup[rid]), 1) if nproj else None

        psa = [dict(team=team[rid], player=name(p), status=info(p).get("injury_status") or "no game")
               for rid, st in lineup.items() for p in st
               if info(p).get("injury_status") in HURT or (playing and info(p).get("team") not in playing)]
        nxt = dict(week=week + 1, psa=psa, games=[
            dict(a=team[a["roster_id"]], b=team[b["roster_id"]], a_proj=projected(a["roster_id"]),
                 b_proj=projected(b["roster_id"])) for a, b in pairs(upcoming)])

    # Power rankings: season all-play win rate, then points for. Movement is vs the week before.
    def order(w):
        return sorted(snaps.get(w, {}), key=lambda rid: snaps[w][rid], reverse=True)
    now, before = order(last_regular), order(last_regular - 1)
    power = [dict(rank=i, team=team[rid], prev=before.index(rid) + 1 if before else None,
                  record=f"{rec[rid]['w']}-{rec[rid]['l']}" + (f"-{rec[rid]['t']}" if rec[rid]["t"] else ""),
                  all_play=f"{rec[rid]['ap_w']}-{rec[rid]['ap_l']}", pf=round(rec[rid]["pf"], 2),
                  this_week=card[rid]["pts"] if rid in card else None) for i, rid in enumerate(now, 1)]

    return dict(league=lg["name"], season=season, week=week, playoff_teams=cfg.get("playoff_teams"), teams=teams,
                power=power, games=games, awards=awards, gems=gems, lineups=lineups, pickups=pickups, standings=standings,
                next=nxt)


LINES = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["key", "line"],
                                    "properties": {"key": {"type": "string"}, "line": {"type": "string"}}}}
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["headline", "dek", "hero_value", "hero_caption", "pen_notes", "story_title", "story", "power_lines",
                       "award_lines", "game_lines", "preview_lines", "signoff"],
          "properties": {"headline": {"type": "string"}, "dek": {"type": "string"},
                         "hero_value": {"type": "string"}, "hero_caption": {"type": "string"},
                         "pen_notes": {"type": "array", "items": {"type": "string"}},
                         "story_title": {"type": "string"},
                         "story": {"type": "array", "items": {"type": "string"}}, "power_lines": LINES,
                         "award_lines": LINES, "game_lines": LINES,
                         "preview_lines": LINES, "signoff": {"type": "string"}}}


WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}


def research(client, facts):
    """Best effort: web-search the week's real NFL moments (big plays, bloopers, memes) for players in this league."""
    who = {}
    for team, lineup in facts["lineups"].items():
        for x in lineup["started"] + [b for b in lineup["bench"] if b["pts"] >= 15]:
            who.setdefault(x["player"], team)
    ask = (f"NFL Week {facts['week']} of the {facts['season']} season just finished. Search the web for the real-life moments fans "
           "are talking about: huge plays, bloopers, bizarre moments, viral memes, sideline drama. Focus on these players, who are "
           "on a fantasy league's rosters (their fantasy team in parentheses):\n" + "; ".join(f"{p} ({t})" for p, t in who.items())
           + "\n\nReply with up to 8 short bullets: the player, what happened, why people are talking about it, and one source URL. "
           "Only include on-field moments that the league, a team or an established sports outlet confirms. "
           "Skip injuries and anything off the field: legal, personal or health news.")
    messages = [{"role": "user", "content": ask}]
    for _ in range(5):  # a server-side search can pause mid-turn; resending the paused turn resumes it
        msg = client.messages.create(model=MODEL, max_tokens=16000, tools=[WEB_SEARCH], messages=messages,
                                     extra_body={"output_config": {"effort": "medium"}})
        if msg.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": msg.content}]
    return "".join(b.text for b in msg.content if b.type == "text").strip() or None


def write_copy(facts, previous=()):
    """Claude drafts the week, then a second pass punches it up. `previous` is this season's earlier copy."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("::warning::ANTHROPIC_API_KEY is not set, so this week gets template copy instead of jokes.")
        return template_copy(facts)
    try:
        import anthropic

        client, brief = anthropic.Anthropic(max_retries=4), (ROOT / "prompt.md").read_text()

        def ask(system, payload):
            # Streamed with room to think: 16k tokens ran out on a busy week and silently fell back to template copy.
            with client.beta.messages.stream(
                model=MODEL,
                max_tokens=128000,  # Opus 5.5's output ceiling; billed only for what's used
                betas=["server-side-fallback-2026-07-01"],
                system=system,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                extra_body={"fallbacks": "default",
                            "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}}},
            ) as stream:
                msg = stream.get_final_message()
            if msg.stop_reason != "end_turn":
                raise RuntimeError(f"stop_reason={msg.stop_reason}")
            # After a mid-stream fallback the new model continues the partial text, so the JSON spans text blocks.
            return json.loads("".join(b.text for b in msg.content if b.type == "text")), msg.model

        try:
            news = research(client.with_options(timeout=300, max_retries=1), facts)  # best effort: never stall the run
        except Exception as e:  # the recap works without it
            print(f"::warning::Skipped the web research for real-life plays ({e}).")
            news = None
        prior = list(previous)
        draft, model = ask(brief, dict(facts=facts, previous_weeks=prior, news=news))
        try:
            final, model = ask(brief + "\n\n---\n\n" + (ROOT / "punchup.md").read_text(),
                               dict(facts=facts, previous_weeks=prior, news=news, draft=draft))
        except Exception as e:  # the draft is real copy already: ship it rather than plain labels
            print(f"::warning::The punch-up pass failed ({e}); shipping the draft.")
            return dict(draft, by=f"{model} (draft only)", news=news)
        return dict(final, by=f"{model} (draft + punch-up)", news=news)
    except Exception as e:  # ponytail: any failure falls back to template copy so the numbers still ship
        print(f"::warning::Claude couldn't write the copy ({e}); using template copy.")
        return template_copy(facts)


def template_copy(f):
    a = {x["key"]: x for x in f["awards"]}
    lead = a.get("heartbreaker") or a["high"]
    return dict(by="template", headline=lead["label"].upper(), dek=f"{lead['team']}: {lead['stat']}.",
                hero_value=a["high"]["stat"].split(" ")[0], hero_caption=f"{a['high']['team']} led the week",
                pen_notes=[], story_title="", story=[], power_lines=[], award_lines=[], game_lines=[], preview_lines=[], signoff="")


BIG = ("blowout", "high", "low", "close", "heartbreaker")  # the big stuff; every other trophy is an "other fun stat"
AT = r"@\w+"  # @mentions get lit up in running text


def ems(s):
    """Rough width of s set in the display caps, in em, so big type can be sized to fit its column."""
    return sum(.11 if ch == " " else .27 if ch in "I1.,:;'’!|" else .73 if ch in "@MW%" else .53 for ch in s.upper())


def led_ems(s):
    """Advance width of s in the LED numerals (Big Shoulders Display Black), in em."""
    return sum(.27 if ch in "1.,:" else .45 if ch == "-" else .68 if ch == "%" else .22 if ch == " " else .5 for ch in s)


def ring(a, b):
    """The telestrator loop around the hero number: a rounded oval (semi-axes a, b in em) drawn in one stroke that
    spirals a little past where it started. Returns the viewBox width, height and path, in hundredths of an em."""
    import math
    grow, s = 1.075, 7  # how far the loop spirals out by the end, and room for the stroke
    w, h = 2 * (a * grow * 100 + s), 2 * (b * grow * 100 + s)
    pts = []
    for i in range(31):
        t = math.radians(-50 - 410 * i / 30)
        c, n = math.cos(t), math.sin(t)
        r = (1 + .055 * i / 30) * (1 + .015 * math.sin(2.5 * t))
        pts.append((w / 2 + a * 100 * r * math.copysign(abs(c) ** .8, c), h / 2 + b * 100 * r * math.copysign(abs(n) ** .8, n)))
    d = "M%.0f %.0f" % pts[0]
    for i in range(30):  # Catmull-Rom through the points, as cubic Beziers
        p0, p1, p2, p3 = pts[max(i - 1, 0)], pts[i], pts[i + 1], pts[min(i + 2, 30)]
        d += " C%.0f %.0f %.0f %.0f %.0f %.0f" % (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6,
                                                 p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6, *p2)
    return w, h, d


ARROW = ('<svg class="arrow" viewBox="0 0 44 30" aria-hidden="true" focusable="false">'
         '<path pathLength="1" d="M42 3 C30 5 16 12 7 26 M17 25 L7 27 L6 17"/></svg>')
SCRIBBLE = ('<svg class="scrib" viewBox="0 0 120 12" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
            '<path d="M3 8 C28 3 52 10 78 5 S108 3 117 7"/></svg>')


def page(shell, f, c, url, data):
    e = html.escape
    who = {t["team"]: t for t in f["teams"]}
    lines = {k: {x["key"]: x["line"] for x in c.get(k) or []}
             for k in ("award_lines", "game_lines", "preview_lines", "power_lines")}
    notes = [n if len(n) <= 22 else "" for n in ((n or "").strip() for n in c.get("pen_notes") or [])]  # marker notes stay short
    wk, league = f["week"], f["league"]

    def lit(s, pattern=r"(?<![\w.])[-+$]?\d+(?:[.,]\d+)*(?:%|st|nd|rd|th)?(?!\w)", hit=""):  # escape, light up numbers
        return re.sub(pattern, lambda m: f'<b class="hit">{m[0]}</b>' if m[0] == hit else f"<b>{m[0]}</b>", e(s, quote=False))

    def fit(name):  # widest word, so CSS can shrink a long name instead of breaking it mid-word
        return f"{max(map(ems, name.split() or [''])):.2f}"

    def note(i, extra=""):
        return f'<p class="pen">{e(notes[i])}{extra}</p>' if i < len(notes) and notes[i] else ""

    def avatar(team, size):
        t = who.get(team, {})
        if t.get("avatar"):
            return f'<img class="av" src="{e(t["avatar"])}" alt="" width="{size}" height="{size}" loading="lazy" decoding="async">'
        return f'<span class="av" aria-hidden="true">{e(team[:1].upper())}</span>'

    def manager(team):
        return who.get(team, {}).get("manager") or team

    def lower_third(team, size):
        mgr = manager(team)
        sub = f'<span class="l3-team">{e(team)}</span>' if mgr != team else ""
        return (f'<div class="l3">{avatar(team, size)}<p><span class="l3-name" style="--n:{fit("@" + mgr)}">@{e(mgr)}</span>'
                f'{sub}</p></div>')

    taped = set()  # each tale of the tape runs once; the same swap under a second trophy reads like a bug

    def tape(v):
        if not v or (v["a"], v["b"]) in taped:
            return ""
        taped.add((v["a"], v["b"]))
        top = max(v["a_pts"], v["b_pts"]) or 1
        return '<div class="tape">' + "".join(
            f'<p class="tp tp-{k}"><span class="tp-tag">{e(v[k + "_tag"])}</span><span class="tp-name">{e(v[k])}</span>'
            f'<b class="tp-pts">{v[k + "_pts"]:.2f}</b><span class="tp-bar" style="--p:{max(v[k + "_pts"], 0) / top * 100:.0f}%">'
            f'</span></p>' for k in "ab") + "</div>"

    def line(kind, key, cls):
        text = lines[kind].get(str(key))
        return f'<p class="{cls}">{lit(text, AT)}</p>' if text else ""  # @names light up, as in the Rundown

    def bumper(hid, title, kicker, pen=""):
        return f'<div class="bump"><h2 id="{hid}"><span>{e(title)}</span></h2><p class="bump-k">{e(kicker)}</p>{pen}</div>'

    def bug_row(team, pts, cls, fmt):
        shown = "–" if pts is None else fmt.format(pts)
        return (f'<p class="sb-row {cls}">{avatar(team, 34)}<span class="sb-t" style="--n:{fit(team)}">{e(team)}</span>'
                f'<b class="sb-s">{shown}</b></p>')

    # Week switcher: every saved week of this season, plus previous/next steps across all of them.
    def week_url(x):
        return f'{SITE}{x["season"]}/{x["week"]}/'

    weeks = [d["facts"] for d in data]
    here = next((i for i, x in enumerate(weeks) if (x["season"], x["week"]) == (f["season"], wk)), None)

    def step(i, cls, label):
        if here is None or not 0 <= i < len(weeks):
            return f'<span class="step {cls} off" aria-hidden="true"></span>'
        return (f'<a class="step {cls}" href="{week_url(weeks[i])}" rel="{cls}">'
                f'<span class="sr">{label}: week {weeks[i]["week"]}</span></a>')

    chips = "".join(
        f'<li><a href="{week_url(x)}"{" aria-current=page" if i == here else ""}>Wk {x["week"]}</a></li>'
        for i, x in enumerate(weeks) if x["season"] == f["season"])
    week_nav = (f'<nav class="weeks" aria-label="Weeks">{step((here or 0) - 1, "prev", "Previous")}'
                f'<ol class="wk-list" role="list">{chips}</ol>{step((here or 0) + 1, "next", "Next")}</nav>')

    # Lead: headline, dek, and the stat graphic. The analyst's loop clears the digits' corners by 0.07em,
    # with more air above, below and to the sides; the board leaves room around the loop.
    head, value, cap = c.get("headline") or "", c.get("hero_value") or "", c.get("hero_caption") or ""
    stat = ""
    if value:
        x, y = (led_ems(value) - .08) / 2 + .07, .41 + .07  # half the digits' ink box, plus clearance
        b = .64
        a = x / (1 - (y / b) ** 2.5) ** .4 / .985
        w, h, d = ring(a, b / .985)
        stat = (f'<div class="stat" style="--rw:{w / 100:.2f};--rh:{h / 100:.2f};--lp:{(h / 100 + .2 - 1) / 2:.2f}">'
                f'<p class="stat-tab">The number</p>{note(0, ARROW)}'
                f'<div class="stat-box"><span class="led-glow"><span class="led">{e(value)}</span></span>'
                f'<svg class="ring" viewBox="0 0 {w:.0f} {h:.0f}" aria-hidden="true" focusable="false">'
                f'<path pathLength="1" d="{d}"/></svg></div>'
                f'{f"<p class=stat-cap>{e(cap)}</p>" if cap else ""}</div>')

    def crawl_item(i, g):
        quip = lines["game_lines"].get(str(i))
        return (f'<span class="ci"><b class="ci-k">Final</b><span class="ci-t">{e(g["win"]["team"])}</span>'
                f'<b class="ci-s">{g["win"]["pts"]:.2f}</b><span class="ci-t ci-l">{e(g["lose"]["team"])}</span>'
                f'<b class="ci-s ci-l">{g["lose"]["pts"]:.2f}</b>{f"<span class=ci-q>{e(quip)}</span>" if quip else ""}</span>')

    reel = "".join(crawl_item(i, g) for i, g in enumerate(f["games"], 1))
    secs = max(30, len(re.sub("<[^>]+>", "", reel)) // 8)  # about 65px a second
    crawl = (f'<div class="crawl"><p class="crawl-k" aria-hidden="true">Finals</p>'
             f'<input type="checkbox" id="hold" class="hold-box"><label for="hold" class="hold"><span class="sr">Pause the scores ticker</span></label>'
             f'<div class="crawl-view" aria-hidden="true"><p class="crawl-track" style="--dur:{secs}s">'
             f'<span class="crawl-set">{reel}</span><span class="crawl-set">{reel}</span></p></div></div>') if reel else ""
    # The dek follows the number, so a phone's first screen is the pun and the circled number; on wide screens the
    # grid puts it back under the headline. The card bug only shows on the link-preview card (card.html).
    brand = re.sub(r"[^\w\s'’().&-]", "", league).strip() or league  # the league's own name, minus any emoji
    mark = "".join(w[:1] for w in league.split()[:2]).upper()
    dek = c.get("dek") or ""
    lead = (f'<section class="lead" aria-labelledby="lead-h" data-wk="{wk}"><div class="lead-in"><div class="lead-copy">'
            f'<p class="kick">Week {wk} · Top story</p>'
            f'<h1 id="lead-h" class="headline" style="--hw:{max(map(ems, head.split() or [""])):.2f};--ht:{ems(head):.2f}">{e(head)}</h1>'
            f'<p class="card-bug" aria-hidden="true"><span class="mark">{e(mark)}</span>{brand}</p></div>'
            f'{stat}{f"<p class=dek>{lit(dek, AT)}</p>" if dek else ""}</div>{crawl}</section>')

    # The Rundown: the lead segment, in the writer's words. Skipped when there's no story (template copy).
    story = [s.strip() for s in c.get("story") or [] if s and s.strip()]
    story_title = f'<h3 class="story-title">{e(c["story_title"])}</h3>' if c.get("story_title") else ""
    rundown = (f'<section class="seg rundown" aria-labelledby="run-h">{bumper("run-h", "The Rundown", f"Week {wk} · from the desk", note(1, SCRIBBLE))}'
               f'<div class="story">{story_title}{"".join(f"<p>{lit(s, AT)}</p>" for s in story)}</div></section>') if story else ""

    # Power rankings: all ten, with movement. The analyst circles the week's biggest climb.
    power = f.get("power") or []
    climbs = [p["prev"] - p["rank"] for p in power if p.get("prev")]
    top_climb = max(climbs, default=0)

    def movement(p):
        if not p.get("prev"):
            return '<span class="mv new">New</span>'
        delta = p["prev"] - p["rank"]
        if delta:
            hot = " hot" if delta == top_climb >= 3 else ""
            return (f'<span class="mv {"up" if delta > 0 else "down"}{hot}"><span class="sr">{"up" if delta > 0 else "down"} </span>'
                    f'{abs(delta)}</span>')
        return '<span class="mv same"><span class="sr">no change</span></span>'

    def ranked(p):
        mgr = manager(p["team"])
        # Joined with real spaces so a narrow screen can wrap between the parts (each part stays whole).
        meta = " ".join(f"<span>{e(x)}</span>" for x in (f"@{mgr}" if mgr != p["team"] else "", p.get("record"),
                                                         f'{p["all_play"]} all-play' if p.get("all_play") else "") if x)
        return (f'<li class="pr-row"><span class="pr-rk">{p["rank"]}</span>{movement(p)}{avatar(p["team"], 44)}'
                f'<p class="pr-team"><b style="--n:{fit(p["team"])}">{e(p["team"])}</b></p><p class="pr-meta">{meta}</p>'
                f'{line("power_lines", p["team"], "pr-line")}</li>')

    rankings = (f'<section class="seg power" aria-labelledby="pow-h">{bumper("pow-h", "Power Rankings", "All-play record, then points", note(2, SCRIBBLE))}'
                f'<ol class="pr" role="list">{"".join(map(ranked, power))}</ol></section>') if power else ""

    # Trophies: the supporting stats package. The big stuff first, then the other fun stats.
    def award(a):
        big = a["key"] in BIG
        return (f'<article class="aw{" aw-big" if big else ""}"><h4 class="aw-tag"><span aria-hidden="true">{e(a["emoji"])}</span>'
                f'{e(a["label"])}</h4>{lower_third(a["team"], 56 if big else 48)}<p class="aw-stat">{lit(a["stat"], hit=value)}</p>'
                f'{line("award_lines", a["key"], "aw-line")}{tape(a.get("vs"))}</article>')

    big = "".join(award(a) for a in f["awards"] if a["key"] in BIG)
    more = "".join(award(a) for a in f["awards"] if a["key"] not in BIG)
    count = f"The stats package · {len(f['awards'])} awards"
    trophies = (f'<section class="seg" aria-labelledby="tro-h">{bumper("tro-h", "Trophies", count, note(3, SCRIBBLE))}'
                + (f'<h3 class="sr">The big stuff</h3><div class="aws aws-big">{big}</div>' if big else "")
                + (f'<h3 class="sub">Other fun stats</h3><div class="aws aws-more">{more}</div>' if more else "")
                + "</section>")

    games = "".join(
        f'<article class="sb"><h3 class="sr">{e(g["win"]["team"])} beat {e(g["lose"]["team"])}</h3>'
        f'<p class="sb-top"><span class="chip">Final</span><span>Game {i}</span><span class="sb-m">+{g["margin"]:.2f}</span></p>'
        f'{bug_row(g["win"]["team"], g["win"]["pts"], "win", "{:.2f}")}{bug_row(g["lose"]["team"], g["lose"]["pts"], "lose", "{:.2f}")}'
        f'<p class="sb-duel"><span class="k">Top guns</span><span>{e(g["win"]["top"]["player"])} <b>{g["win"]["top"]["pts"]:.1f}</b></span>'
        f'<span><i>vs</i>{e(g["lose"]["top"]["player"])} <b>{g["lose"]["top"]["pts"]:.1f}</b></span></p>'
        f'{line("game_lines", i, "sb-line")}</article>'
        for i, g in enumerate(f["games"], 1))
    scoreboard = (f'<section class="seg" aria-labelledby="sb-h">{bumper("sb-h", "Scoreboard", f"Week {wk} finals", note(4, SCRIBBLE))}'
                  f'<div class="bugs">{games}</div></section>')

    upcoming = ""
    n = f.get("next")
    if n:
        def preview(i, g):
            a, b = g.get("a_proj"), g.get("b_proj")
            fav = "a" if (a or 0) > (b or 0) else "b" if (b or 0) > (a or 0) else ""
            return (f'<article class="sb pre"><h3 class="sr">{e(g["a"])} vs {e(g["b"])}</h3>'
                    f'<p class="sb-top"><span class="chip">Wk {n["week"]}</span><span>Projected</span></p>'
                    f'{bug_row(g["a"], a, "fav" if fav == "a" else "", "{:.1f}")}'
                    f'{bug_row(g["b"], b, "fav" if fav == "b" else "", "{:.1f}")}{line("preview_lines", i, "sb-line")}</article>')

        psa = "".join(f'<li><span class="st">{e(x["status"])}</span><span><b>{e(x["player"])}</b> in the {e(x["team"])} lineup</span></li>'
                      for x in n.get("psa") or [])
        psa = f'<aside class="psa" aria-labelledby="psa-h"><h3 id="psa-h">Lineup PSA</h3><ul>{psa}</ul></aside>' if psa else ""
        next_title = f"Week {n['week']}"
        upcoming = (f'<section class="seg" aria-labelledby="next-h">{bumper("next-h", next_title, "Coming up · projected")}'
                    f'<div class="bugs">{"".join(preview(i, g) for i, g in enumerate(n["games"], 1))}{psa}</div></section>')

    cut, rows = f.get("playoff_teams") or 0, []
    for i, s in enumerate(f["standings"], 1):
        rows.append(f'<tr{" class=in" if i <= cut else ""}><th scope="row"><span class="rk">{i}</span>{e(s["team"])}</th>'
                    f'<td>{s["w"]}-{s["l"]}{"-" + str(s["t"]) if s["t"] else ""}</td><td>{s["pf"]:.2f}</td><td>{s["pa"]:.2f}</td>'
                    f'<td>{s["all_play"]}</td><td class="{"pos" if s["luck"] > 0 else "neg"}">{s["luck"]:+.2f}</td>'
                    f'<td>{s["bench_left"]:.1f}</td></tr>')
        if i == cut < len(f["standings"]):
            rows.append(f'<tr class="cut"><td colspan="7"><span>Playoff line · top {cut} get in</span></td></tr>')
    teams_wk = sorted((t for g in f["games"] for t in (g["win"], g["lose"])), key=lambda t: -t["eff"])
    report = "".join(
        f'<tr><th scope="row">{e(t["team"])}</th><td>{t["pts"]:.2f}</td><td>{t["opt"]:.2f}</td>'
        f'<td>{t["opt"] - t["pts"]:.2f}</td><td class="eff">{t["eff"]:g}%<span style="--p:{t["eff"]:g}%"></span></td></tr>'
        for t in teams_wk)
    nerd = (f'<section class="seg" aria-labelledby="nerd-h">{bumper("nerd-h", "Nerd Corner", "Standings & lineup math", note(5, SCRIBBLE))}'
            f'<details class="fold"><summary><span><span class="if-shut">Show</span><span class="if-open">Hide</span> the standings and lineup math</span></summary><div class="boards"><div class="scroll" tabindex="0" role="region" aria-label="Standings table">'
            f'<table class="standings"><caption>Standings</caption><thead><tr><th scope="col">Team</th><th scope="col">W-L</th>'
            f'<th scope="col">PF</th><th scope="col">PA</th><th scope="col" title="Record if you played every team every week">All-play</th>'
            f'<th scope="col" title="Wins above what your all-play rate predicts">Luck</th>'
            f'<th scope="col" title="Season points left on the bench">Benched</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<div class="scroll" tabindex="0" role="region" aria-label="Lineup report table"><table class="report">'
            f'<caption>Lineup report, week {wk}</caption><thead><tr><th scope="col">Team</th><th scope="col">Pts</th>'
            f'<th scope="col">Max</th><th scope="col">Left</th><th scope="col">Eff</th></tr></thead><tbody>{report}</tbody></table></div>'
            f'</div></details></section>')

    archive = "".join(
        f'<li><a href="{week_url(d["facts"])}"{" aria-current=page" if i == here else ""}>'
        f'<span class="arc-wk">Wk {d["facts"]["week"]}</span>{e(d["copy"].get("headline") or "")}</a></li>'
        for i, d in reversed(list(enumerate(data))))
    signoff = f'<p class="pen signoff">{e(c["signoff"])}{SCRIBBLE}</p>' if c.get("signoff") else ""
    body = (f'<header class="mast"><div class="mast-in"><p class="mark" aria-hidden="true">{e(mark)}</p>'
            f'<p class="brand"><b>{e(league)}</b><span>{e(str(f["season"]))} season recap</span></p>{week_nav}</div></header>'
            f'<main>{lead}{rundown}{rankings}{trophies}{scoreboard}{upcoming}{nerd}</main>'
            f'<footer class="foot">{signoff}'
            f'<nav aria-labelledby="arc-h"><h2 id="arc-h" class="foot-h">Previously on {e(league)}</h2><ul class="archive">{archive}</ul></nav>'
            f'<p class="fine">Numbers from Sleeper. Jokes from Claude. Updates Tuesday mornings.</p></footer>')
    # Pun first: iMessage shows only the preview image and a line or two of title.
    title = f"{head} · {brand} Week {wk}" if head else f"{brand} Week {wk}"
    alt = " ".join(x for x in (f"Week {wk}: {head}." if head else f"Week {wk}.",
                               f"The number: {value}, circled in red marker." if value else "", cap) if x)
    return (shell.replace("{{title}}", e(title)).replace("{{description}}", e(dek))
            .replace("{{image}}", e(url + "og.jpg")).replace("{{image_alt}}", e(alt))
            .replace("{{url}}", e(url)).replace("{{brand}}", e(brand)).replace("{{body}}", body))


def load(p):
    """A week file. A broken hand edit stops the run naming the file and the fix, instead of a traceback."""
    try:
        return json.loads(p.read_text())
    except ValueError as err:
        print(f"::error file={p.relative_to(ROOT)}::{p.name} isn't valid JSON ({err}). A straight double quote "
              'inside a line must be written \\" and the last item in a list takes no comma.')
        sys.exit(1)


def render():
    import shutil
    shell = (ROOT / "template.html").read_text()
    data = [load(p) for p in sorted((ROOT / "weeks").glob("*.json"))]
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    for d in data:
        f = d["facts"]
        url = f"{SITE}{f['season']}/{f['week']}/"
        out = docs / f["season"] / str(f["week"]) / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        doc = page(shell, f, d["copy"], url, data)
        out.write_text(doc)
        # The link-preview card: the same page in card mode, without the show-open script. The workflow screenshots
        # it to og.jpg (every page's og:image) and deletes it before deploying.
        card = doc.replace('<html lang="en">', '<html lang="en" class="card">', 1)
        (out.parent / "card.html").write_text(re.sub(r"<script>.*?</script>", "", card, count=1, flags=re.S))
    if not data:  # a brand-new site before its first recap
        (docs / "index.html").write_text("<!doctype html><meta charset=utf-8><title>Coming soon</title>"
                                         "<p style='font:20px system-ui;padding:2rem'>The first recap lands after this week's games.</p>")
    if data:
        f, c = data[-1]["facts"], data[-1]["copy"]
        url = f"{SITE}{f['season']}/{f['week']}/"
        (docs / "index.html").write_text(page(shell, f, c, url, data))
    icon = ROOT / "apple-touch-icon.png"  # iMessage and Slack show it beside links; also the home-screen icon
    if icon.exists():
        shutil.copyfile(icon, docs / icon.name)


def save(path, week):
    path.write_text(json.dumps(week, indent=1, ensure_ascii=False) + "\n")


def main():
    if sys.argv[1:] != ["render"]:
        facts = build_facts(os.environ.get("WEEK"))
        path = ROOT / "weeks" / f"{facts['season']}-{facts['week']:02d}.json"
        saved = load(path) if path.exists() else None
        copy = saved and saved["copy"]
        if saved and saved.get("locked") and os.environ.get("DRY_RUN") != "true":
            # Frozen: no run rewrites it, fresh or not. Hand edits on GitHub still work; dry runs save nothing.
            print(f"::notice::Week {facts['week']} is locked, so this run leaves it alone. "
                  f"To rewrite it, delete the \"locked\" line from {path.relative_to(ROOT)}.")
        else:
            if saved and os.environ.get("GITHUB_EVENT_NAME") == "schedule" and copy.get("by") != "template":
                # Sleeper hasn't scored a newer week, or this is the retry run: leave the published week, hand edits and all.
                print(f"::notice::Week {facts['week']} is already up and Sleeper has nothing newer. Nothing to do.")
                facts = saved["facts"]
            elif (not copy or os.environ.get("FRESH") == "true"
                    or (copy.get("by") == "template" and os.environ.get("ANTHROPIC_API_KEY"))):
                earlier = [load(q) for q in sorted((ROOT / "weeks").glob(f"{facts['season']}-*.json"))]
                fresh = write_copy(facts, [dict(week=d["facts"]["week"], **{k: v for k, v in d["copy"].items() if k not in ("by", "news")})
                                           for d in earlier if d["facts"]["week"] < facts["week"]])
                if fresh.get("by") != "template" or not copy:  # a failed rewrite never replaces jokes we already have
                    copy = fresh
            path.parent.mkdir(exist_ok=True)
            # Freeze by default: a week with real jokes locks as soon as it's saved. Plain-label (template) weeks stay
            # open so the afternoon retry, or a rerun, can still write their jokes.
            locked = bool(saved and saved.get("locked")) or copy.get("by") != "template"
            save(path, {**({"locked": True} if locked else {}), "facts": facts, "copy": copy})
        if os.environ.get("GITHUB_ENV"):  # tells the workflow's later steps which week this run wrote
            with open(os.environ["GITHUB_ENV"], "a") as env:
                env.write(f"WEEK_FILE={path.relative_to(ROOT)}\n")
    render()


if __name__ == "__main__":
    main()
