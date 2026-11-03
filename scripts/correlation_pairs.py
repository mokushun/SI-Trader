import sqlite3, numpy as np, pandas as pd, itertools, json
DB = "/home/hatch/workspace/oanda_json/m1.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

def load(step_sec):
    # sample bars where t % step == 0, mid close
    q = f"SELECT inst, t, (bc+ac)/2.0 as mid FROM m1 WHERE t % {step_sec} = 0 ORDER BY inst, t"
    df = pd.read_sql(q, con)
    pivot = df.pivot(index='t', columns='inst', values='mid')
    pivot.index = pd.to_datetime(pivot.index, unit='s', utc=True)
    return pivot

def corr_pairs(price, label, min_obs=500):
    # log returns, only where time gap equals expected? pandas diff on index irregular - use returns and drop NaN pairwise
    rets = np.log(price / price.shift(1))
    # Mask returns spanning > 2x typical step (weekend gaps) - infer step median
    gaps = pd.Series(price.index).diff().dt.total_seconds()
    typical = gaps.median()
    valid = gaps <= typical*1.5
    valid.iloc[0]=False
    rets = rets[valid.values]
    corr = rets.corr(min_periods=min_obs)
    pairs=[]
    cols=list(corr.columns)
    for a,b in itertools.combinations(cols,2):
        c=corr.loc[a,b]
        n=rets[[a,b]].dropna().shape[0]
        if pd.notna(c):
            pairs.append((a,b,float(c),int(n)))
    pairs.sort(key=lambda x: abs(x[2]), reverse=True)
    print(f"\n===== {label}  obs median n={rets.shape[0]} rows, instruments={len(cols)} =====")
    print("Top positive:")
    for a,b,c,n in sorted(pairs, key=lambda x: x[2], reverse=True)[:15]:
        print(f"  {a:12s} {b:12s}  r={c:+.3f}  n={n}")
    print("Top negative:")
    for a,b,c,n in sorted(pairs, key=lambda x: x[2])[:15]:
        print(f"  {a:12s} {b:12s}  r={c:+.3f}  n={n}")
    print("Strongest |r| overall:")
    for a,b,c,n in pairs[:20]:
        print(f"  {a:12s} {b:12s}  r={c:+.3f}  n={n}")
    return corr, rets, pairs

# Hourly full period from 2020-10-01
hourly = load(3600)
hourly = hourly.loc["2020-10-01":]
print("hourly shape", hourly.shape, hourly.index.min(), hourly.index.max())
corr_h, rets_h, pairs_h = corr_pairs(hourly, "HOURLY log returns 2020-10-01..2026-10-29")

# Daily (sample 00:00 UTC)
daily = load(86400)
daily = daily.loc["2020-10-01":]
print("\ndaily shape", daily.shape)
corr_d, rets_d, pairs_d = corr_pairs(daily, "DAILY (00:00 UTC sample) log returns", min_obs=300)

# Hourly last 1 year
hourly_1y = hourly.loc["2025-10-29":]
corr_1y, rets_1y, pairs_1y = corr_pairs(hourly_1y, "HOURLY last 1y 2025-10-29..2026-10-29")

# Compare full vs 1y for top pairs
print("\n===== Stability: top hourly full-period pairs, r_full vs r_1y =====")
for a,b,c,n in pairs_h[:15]:
    c1 = corr_1y.loc[a,b] if a in corr_1y.columns and b in corr_1y.columns else np.nan
    print(f"  {a:12s} {b:12s}  full={c:+.3f}  1y={c1:+.3f}")

# Save matrices
corr_h.round(3).to_csv("/home/hatch/workspace/SI-Trader/outputs/corr_hourly_full.csv")
corr_d.round(3).to_csv("/home/hatch/workspace/SI-Trader/outputs/corr_daily_full.csv")
corr_1y.round(3).to_csv("/home/hatch/workspace/SI-Trader/outputs/corr_hourly_1y.csv")
with open("/home/hatch/workspace/SI-Trader/outputs/correlation_pairs.json","w") as f:
    json.dump({"hourly_full":pairs_h,"daily_full":pairs_d,"hourly_1y":pairs_1y}, f, indent=1)
print("\nsaved csv/json to SI-Trader/outputs/")
