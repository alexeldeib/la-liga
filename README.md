# La Liga

The weekly recap for the La Liga fantasy league: **https://liga.alexeldeib.xyz**

Every Tuesday morning, a GitHub Action pulls the week from Sleeper's public API, picks the trophies, has Claude search the web for the week's real big plays and memes, writes the jokes (a draft, then a punch-up pass), and redeploys the site. Share the link once; it updates itself.

The run is scheduled for 9:17am Eastern, but GitHub often starts scheduled runs hours late, so look for it by early afternoon. A second run in the afternoon retries anything the morning missed and does nothing if the week is already up. The site goes live as soon as a run finishes, so any edits land after the league can already see the page.

## Hand-editing a week

1. Check **Actions** first. If a recap run is in progress, wait for it to finish.
2. On GitHub, open `weeks/2026-NN.json` (NN is the week in two digits, like `04`), click the pencil, and change only the `copy` section: `headline`, `dek`, `hero_value`, `hero_caption`, `pen_notes`, `story`, `signoff`, and the `line` text in `power_lines`, `award_lines`, `game_lines` and `preview_lines`. Leave each `key` alone; it ties a line to its card.
3. Keep it valid JSON. Write a straight double quote inside a line as `\"` (curly quotes “ ” need nothing), and don't put a comma after the last item in a list. A broken file stops the deploy, GitHub emails you the file name and the problem, and the site keeps its last good version until you fix it.
4. Commit to `main`. The site redeploys in a minute or two.
5. If you write a whole week yourself after it shipped with plain labels, also change its `"by"` from `"template"` to your name, or the afternoon run will replace your words with Claude's.

## Freezing weeks

A week locks automatically once the next week goes up: its file gets `"locked": true` at the top. Runs never touch a locked week, not even with **fresh**, so the jokes and numbers you signed off on stay put. Hand edits on GitHub still work the same way.

To regenerate a locked week, delete its `"locked": true,` line, commit, then rerun it with **fresh**.

## Rerunning a week

Go to **Actions → Weekly recap → Run workflow**:

- **week**: leave blank for the latest finished week, or enter a week number.
- **fresh** ticked: Claude rewrites that week's jokes from scratch (about $1.50). This replaces your hand edits to that week.
- **fresh** unticked, on a week that's already up: refreshes only the numbers, for example after NFL stat corrections land mid-week. The jokes stay. If a correction changed a result or a trophy winner, tick **fresh** instead, because each joke was written for the old winner.
- **dry run**: shows the jokes and the web-research brief in the run log and summary, without committing or deploying. It works on locked weeks too, since it saves nothing. Tick **fresh** too, or you'll just see the existing jokes. The repo is public, so anyone can read that summary.

When a run emails you:

- **"shipped without jokes"**: Claude failed (an outage, refusal or limit), so the numbers went out with plain labels. The afternoon run retries on its own, or rerun that week with **fresh**.
- **"Commit the new week" failed**: someone pushed to the same week while the run was going. Your change is safe; rerun if you still want the run's output.
- **A manual run shows as cancelled**: a newer run replaced it in the queue. Run it again.

## Knobs

- `prompt.md` sets the writer's voice and league lore. Add in-jokes, rivalries, and past champions there.
- `punchup.md` is the second pass: Claude rereads its draft as an editor and makes it funnier.
- `template.html` controls the look. `recap.py` builds the facts and the page.

## Season notes

- Weeks 15 to 17 are the playoffs, one week per round. The scoreboard doesn't label playoff rounds yet.
- Once week 17 is up, scheduled runs do nothing, and GitHub switches the schedule off after 60 days without a commit.
- Next season, Sleeper gives the renewed league a new ID. Put it in `LEAGUE_ID` at the top of `.github/workflows/weekly.yml`, turn the workflow back on (**Actions → Weekly recap → Enable workflow**), and refresh the lore in `prompt.md`.

Each run makes three Claude Opus 5.5 calls (web research, draft, punch-up), which usually costs $1 to $2. A season runs about $20 to $35.

## One-time setup

Give the Action an Anthropic API key so Claude can write the jokes. Without one, the site still updates, with plain trophy labels.

```bash
gh secret set ANTHROPIC_API_KEY -R alexeldeib/la-liga
```

Then set a monthly spend limit for that key's workspace in the Anthropic Console, so a runaway week can't surprise you.

## Local

```bash
export LEAGUE_ID=1381427556288323584 SITE_URL=https://liga.alexeldeib.xyz/
python3 recap.py          # recap the latest scored week
python3 recap.py render   # rebuild docs/ from weeks/*.json
```
