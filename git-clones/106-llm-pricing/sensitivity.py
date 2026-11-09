"""Does the finding survive the assumption it leans on?

The agentic comparison hinges on cache_rewrite_every — how many requests one
Anthropic cache write is amortised over. I chose 8. If the conclusion only
holds at 8, it is not a conclusion, it is a choice of input. This sweeps it.
"""
import json, sys, copy
import compute

d = json.load(open(sys.argv[1]))
models = {m["name"]: m for m in d["models"]}
base = compute.WORKLOADS["agentic coding loop"]

PAIRS = [("Claude Sonnet 5.5", "gpt-6.1-sol"),
         ("Claude Opus 5.5", "gpt-5.6-sol"),
         ("Claude Haiku 5.5", "gpt-6-luna")]
SWEEP = [1, 2, 4, 8, 16, 32, 64, 1000]

print("Agentic loop, 120k in / 4k out, 88% cached.")
print("Ratio = Anthropic cost / competitor cost at identical list price.\n")
print(f"{'rewrite every':>14} | " + " | ".join(f"{a.split()[-1]:>8}" for a, _ in PAIRS))
print("-" * 52)
rows = {}
for n in SWEEP:
    wl = copy.deepcopy(base); wl["cache_rewrite_every"] = n
    cells = []
    for a, b in PAIRS:
        ca = compute.cost_per_1k(models[a], wl)
        cb = compute.cost_per_1k(models[b], wl)
        cells.append(ca / cb)
        rows.setdefault(f"{a} vs {b}", {})[n] = {
            "anthropic": round(ca, 2), "competitor": round(cb, 2),
            "ratio": round(ca / cb, 3)}
    print(f"{n:>14} | " + " | ".join(f"{c:>7.2f}x" for c in cells))

print("""
Three different mechanisms, and they behave differently:

  Sonnet 5.5 vs gpt-6.1-sol   identical list price AND identical cache read
                              rate. The whole gap is the cache-write charge,
                              so it amortises away: 4.33x down to 1.00x.

  Opus 5.5 vs gpt-5.6-sol     Opus reads cache at half the price but pays to
                              write it. Which model is cheaper INVERTS on
                              rewrite frequency alone: 3.82x worse when the
                              cache expires every request, 0.88x — cheaper —
                              when it rarely does.

  Haiku 5.5 vs gpt-6-luna     a 5x gap that does not amortise at all, because
                              it is a context cliff above 100k tokens, not a
                              write charge.

The second row is the point. For two models at the same published price, the
pricing page cannot tell you which is cheaper, even in principle — the answer
depends on your own cache hit pattern.
""")

# Isolate the two mechanisms.
wl_nowrite = copy.deepcopy(base); wl_nowrite["cache_rewrite_every"] = 0
print("With cache writes removed entirely (the fairest case for Anthropic):")
for a, b in PAIRS:
    ca = compute.cost_per_1k(models[a], wl_nowrite)
    cb = compute.cost_per_1k(models[b], wl_nowrite)
    print(f"  {a:<20} ${ca:>8,.2f}   vs  {b:<14} ${cb:>8,.2f}   {ca/cb:>5.2f}x")

json.dump({"sweep": rows, "note": "cache_rewrite_every swept over the agentic workload"},
          open(sys.argv[2], "w"), indent=2)
