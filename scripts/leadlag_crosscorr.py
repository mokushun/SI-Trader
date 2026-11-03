import sqlite3, numpy as np, pandas as pd, time
DB="/home/hatch/workspace/oanda_json/m1.db"
INSTS=["SPX500_USD","NAS100_USD","JP225_USD","EUR_USD","GBP_USD","USD_JPY","EUR_JPY","GBP_JPY","AUD_JPY"]
con=sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
t0=time.time()
data={}
for inst in INSTS:
    q="SELECT t, bc, ac, bo, ao FROM m1 WHERE inst=? ORDER BY t"
    arr=np.array(con.execute(q,(inst,)).fetchall(), dtype=np.float64)  # t as float but int up to 1.7e9 exact in float64 ok
    t=arr[:,0].astype(np.int64)
    mid_c=(arr[:,1]+arr[:,2])/2
    spread_bp=(arr[:,2]-arr[:,1])/mid_c*10000
    data[inst]={"t":t,"mid":mid_c,"bo":arr[:,3],"ao":arr[:,4],"spread_bp_med":float(np.median(spread_bp))}
    print(inst, len(t), f"spread_med_bp={np.median(spread_bp):.2f}", f"elapsed={time.time()-t0:.0f}s", flush=True)
np.savez_compressed("/tmp/leadlag_data.npz", **{f"{k}_t":v["t"] for k,v in data.items()}, **{f"{k}_mid":v["mid"] for k,v in data.items()}, **{f"{k}_bo":v["bo"] for k,v in data.items()}, **{f"{k}_ao":v["ao"] for k,v in data.items()})
import json
json.dump({k:{"spread_bp_med":v["spread_bp_med"],"n":len(v["t"])} for k,v in data.items()}, open("/home/hatch/workspace/SI-Trader/outputs/leadlag_meta.json","w"), indent=1)

# Build common timeline per pair on the fly for lag corr
def aligned(a,b):
    # intersect timestamps
    ta,tb=data[a]["t"],data[b]["t"]
    ia=np.isin(ta,tb); ib=np.isin(tb,ta)
    # Since both sorted and mostly identical, use intersection via np.intersect1d with indices
    common, idx_a, idx_b = np.intersect1d(ta,tb, return_indices=True)
    return common, data[a]["mid"][idx_a], data[b]["mid"][idx_b]

def lag_corr(a,b,maxlag=10):
    t, pa, pb = aligned(a,b)
    # 1-min log returns, valid only if t diff ==60
    dt=np.diff(t)
    valid=dt==60
    ra=np.full(len(t), np.nan); rb=np.full(len(t), np.nan)
    ra[1:]=np.log(pa[1:]/pa[:-1]); rb[1:]=np.log(pb[1:]/pb[:-1])
    ra[1:][~valid]=np.nan; rb[1:][~valid]=np.nan
    res={}
    for lag in range(-maxlag, maxlag+1):
        # corr( ra[t], rb[t+lag] ) lag>0 means a leads b
        if lag>=0:
            x=ra[:len(ra)-lag] if lag>0 else ra
            y=rb[lag:] if lag>0 else rb
        else:
            x=ra[-lag:]; y=rb[:len(rb)+lag]
        m=np.isfinite(x)&np.isfinite(y)
        res[lag]=float(np.corrcoef(x[m], y[m])[0,1]) if m.sum()>1000 else np.nan
    return res

pairs=[("SPX500_USD","JP225_USD"),("NAS100_USD","SPX500_USD"),("EUR_USD","GBP_USD"),("USD_JPY","GBP_JPY"),("USD_JPY","EUR_JPY"),("SPX500_USD","AUD_JPY"),("EUR_USD","USD_JPY")]
print("\n=== Lag cross-correlation of 1-min log returns ===")
print("lag>0 : first instrument leads second by lag minutes")
for a,b in pairs:
    r=lag_corr(a,b)
    print(f"\n{a} -> {b}")
    print("  " + "  ".join(f"{lag:+d}:{r[lag]:+.3f}" for lag in range(-5,6)))
    print(f"  lag+10:{r[10]:+.3f}  lag-10:{r[-10]:+.3f}   contemporaneous(0):{r[0]:+.3f}")
