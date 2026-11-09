"""What four real workload shapes actually cost on each model.

The pricing page gives you input, output and cached-input rates. It does not
tell you what you will pay, because what you pay depends on the shape of your
traffic: how much of your input repeats, how much the model writes back, and
whether you cross a context cliff.

This takes the published rates in prices.json and runs four workload shapes
through them. Every assumption is a number in WORKLOADS that you can change.

  python3 compute.py prices.json costs.json
"""
import json, sys, itertools

# Each workload is one request. `cached_share` is the fraction of input tokens
# served from cache in steady state. `cache_rewrite_every` is how many requests
# one cache write is amortised over — Anthropic bills cache writes, OpenAI and
# Google do not, so this term is zero for them and is a real cost asymmetry
# rather than a modelling choice.
WORKLOADS = {
    "agentic coding loop": {
        "input": 120_000, "output": 4_000, "cached_share": 0.88,
        "cache_rewrite_every": 8,
        "about": "long system prompt and file context resent every turn",
    },
    "RAG chat": {
        "input": 40_000, "output": 600, "cached_share": 0.85,
        "cache_rewrite_every": 20,
        "about": "fixed corpus in the prompt, short answers",
    },
    "bulk classification": {
        "input": 800, "output": 40, "cached_share": 0.0,
        "cache_rewrite_every": 0,
        "about": "short unique inputs, one-word answers, nothing to cache",
    },
    "long-document synthesis": {
        "input": 180_000, "output": 5_000, "cached_share": 0.0,
        "cache_rewrite_every": 0,
        "about": "a different large document every time",
    },
}

REQUESTS = 1_000   # every cost below is per 1,000 requests


def rates(model, prompt_tokens, at_date=None):
    """Effective per-MTok rates, after context cliffs and scheduled changes."""
    r = {"input": model["input"], "output": model["output"],
         "cache_read": model["cache_read"],
         "cache_write": model.get("cache_write", 0.0)}
    cliff = model.get("context_cliff")
    if cliff and prompt_tokens > cliff["above_tokens"]:
        r.update({k: v for k, v in cliff.items() if k != "above_tokens"})
        r["cache_write"] = r["cache_read"] * 12.5 if model.get("cache_write") else 0.0
    sched = model.get("scheduled_change")
    if sched and at_date and at_date >= sched["date"]:
        r.update({k: v for k, v in sched.items() if k != "date"})
    return r


def cost_per_1k(model, wl, at_date=None):
    r = rates(model, wl["input"], at_date)
    cached = wl["input"] * wl["cached_share"]
    fresh = wl["input"] - cached
    per_req = (fresh / 1e6) * r["input"] + (cached / 1e6) * r["cache_read"] \
        + (wl["output"] / 1e6) * r["output"]
    # Amortised cache writes. Only Anthropic bills these.
    if wl["cache_rewrite_every"] and r["cache_write"]:
        per_req += (cached / 1e6) * r["cache_write"] / wl["cache_rewrite_every"]
    return per_req * REQUESTS


def main(pricefile, out):
    d = json.load(open(pricefile))
    models = d["models"]
    results = {}

    for wname, wl in WORKLOADS.items():
        rows = [{"model": m["name"], "vendor": m["vendor"], "tier": m["tier"],
                 "cost": round(cost_per_1k(m, wl), 2)} for m in models]
        rows.sort(key=lambda x: x["cost"])
        for i, row in enumerate(rows):
            row["rank"] = i + 1
        results[wname] = {"assumptions": wl, "rows": rows}

    # Does the ranking actually change between workloads? Compare like tiers.
    inversions = []
    for wa, wb in itertools.combinations(WORKLOADS, 2):
        ra = {r["model"]: r["rank"] for r in results[wa]["rows"]}
        rb = {r["model"]: r["rank"] for r in results[wb]["rows"]}
        for m1, m2 in itertools.combinations(ra, 2):
            if (ra[m1] < ra[m2]) != (rb[m1] < rb[m2]):
                ca = {r["model"]: r["cost"] for r in results[wa]["rows"]}
                cb = {r["model"]: r["cost"] for r in results[wb]["rows"]}
                inversions.append({
                    "a": m1, "b": m2, "workload_1": wa, "workload_2": wb,
                    "w1_cheaper": m1 if ra[m1] < ra[m2] else m2,
                    "w2_cheaper": m1 if rb[m1] < rb[m2] else m2,
                    "w1_costs": [ca[m1], ca[m2]], "w2_costs": [cb[m1], cb[m2]]})

    # Models whose published list prices are identical.
    same = {}
    for m in models:
        k = (m["input"], m["output"])
        same.setdefault(k, []).append(m)
    identical = []
    for (inp, outp), ms in same.items():
        if len(ms) > 1 and len({m["vendor"] for m in ms}) > 1:
            entry = {"list_input": inp, "list_output": outp,
                     "models": [m["name"] for m in ms],
                     "cache_read": {m["name"]: m["cache_read"] for m in ms},
                     "agentic_cost_per_1k": {
                         m["name"]: round(cost_per_1k(m, WORKLOADS["agentic coding loop"]), 2)
                         for m in ms}}
            c = entry["agentic_cost_per_1k"]
            entry["spread"] = round(max(c.values()) / min(c.values()), 2)
            identical.append(entry)

    json.dump({"source": d["sources"], "retrieved": d["retrieved"],
               "requests_modelled": REQUESTS,
               "workloads": results, "rank_inversions": inversions,
               "identical_list_price_groups": identical},
              open(out, "w"), indent=2)

    for wname, res in results.items():
        print(f"\n{wname.upper()}  ({res['assumptions']['about']})")
        print(f"  {res['assumptions']['input']:,} in / "
              f"{res['assumptions']['output']:,} out, "
              f"{res['assumptions']['cached_share']:.0%} cached")
        for r in res["rows"][:5]:
            print(f"   {r['rank']:2d}. {r['model']:<22} ${r['cost']:>9,.2f}")
        print(f"   ... {res['rows'][-1]['model']} ${res['rows'][-1]['cost']:,.2f}")
    print(f"\nrank inversions between workloads: {len(inversions)}")
    for g in identical:
        print(f"\nIDENTICAL LIST PRICE  ${g['list_input']}/{g['list_output']}: "
              f"{', '.join(g['models'])}")
        for n, c in g["agentic_cost_per_1k"].items():
            print(f"   {n:<22} cache read ${g['cache_read'][n]:<6} "
                  f"agentic ${c:,.2f}/1k")
        print(f"   spread: {g['spread']}x")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
