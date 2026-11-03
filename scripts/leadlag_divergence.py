import numpy as np, json, time
z=np.load("/tmp/leadlag_data.npz")
meta=json.load(open("/home/hatch/workspace/SI-Trader/outputs/leadlag_meta.json"))

def get(inst):
    return z[f"{inst}_t"], z[f"{inst}_mid"], z[f"{inst}_bo"], z[f"{inst}_ao"]

def kret(t, p, K):
    r=np.full(len(t), np.nan)
    ok=np.zeros(len(t), bool)
    ok[K:]=(t[K:]-t[:-K]==K*60)
    r[K:]=np.log(p[K:]/p[:-K])
    r[~ok]=np.nan
    return r

def futret(t,p,i,H):
    # return from close at i to close at i+H if contiguous
    if i+H>=len(t): return np.nan
    if t[i+H]-t[i]!=H*60: return np.nan
    return np.log(p[i+H]/p[i])

PAIRS=[("SPX500_USD","JP225_USD"),("NAS100_USD","SPX500_USD"),("EUR_USD","GBP_USD"),("USD_JPY","GBP_JPY"),("SPX500_USD","AUD_JPY")]
K=5
for A,B in PAIRS:
    ta,pa,_,_=get(A); tb,pb,bbo,bao=get(B)
    common, ia, ib=np.intersect1d(ta,tb,return_indices=True)
    t=common; pa=pa[ia]; pb=pb[ib]; bbo=bbo[ib]; bao=bao[ib]
    Ra=kret(t,pa,K); Rb=kret(t,pb,K)
    m=np.isfinite(Ra)&np.isfinite(Rb)
    beta=float(np.cov(Ra[m], Rb[m])[0,1]/np.var(Ra[m]))
    corr=float(np.corrcoef(Ra[m],Rb[m])[0,1])
    D=(beta*Ra-Rb)*10000  # bp, positive = B lagging behind A-implied move (B should rise)
    absD=np.abs(D[m])
    qs=np.nanpercentile(absD,[50,90,95,99])
    spread=meta[B]["spread_bp_med"]
    print(f"\n===== {A} (lead) -> {B} (lag)  K={K}min  beta={beta:.2f} corr5m={corr:.3f} =====")
    print(f"  |divergence| bp: median={qs[0]:.1f} p90={qs[1]:.1f} p95={qs[2]:.1f} p99={qs[3]:.1f}   vs {B} median spread={spread:.2f}bp (round-trip cost approx = 1x spread)")
    # ---- Event study on extreme divergence (p99), thinned 60min ----
    thr=qs[3]
    idx=np.where(m & (np.abs(D)>=thr))[0]
    events=[]; last=-10**9
    for i in idx:
        if i-last>=60:
            events.append(i); last=i
    print(f"  extreme events |D|>=p99({thr:.1f}bp), thinned>=60min: n={len(events)}")
    for H in (5,15,30,60):
        cu=[]; ld=[]
        for i in events:
            fb=futret(t,pb,i,H); fa=futret(t,pa,i,H)
            if np.isfinite(fb) and np.isfinite(fa):
                s=np.sign(D[i]); cu.append(s*fb*10000); ld.append(s*fa*10000)
        cu=np.array(cu); ld=np.array(ld)
        if len(cu)>30:
            print(f"    +{H:2d}min: lag catch-up signed mean={cu.mean():+.2f}bp (t={cu.mean()/(cu.std(ddof=1)/np.sqrt(len(cu))):+.2f}, win={(cu>0).mean()*100:.0f}%) | lead signed mean={ld.mean():+.2f}bp (positive=lead kept going, negative=lead reverted)")
    # ---- Executable backtest, |D|>=p95, thinned by hold H=15, entry next bar open ----
    H=15; thr95=qs[2]
    idx=np.where(m & (np.abs(D)>=thr95))[0]
    trades_net=[]; trades_gross=[]; last=-10**9
    for i in idx:
        e=i+1; x=e+H
        if e<=last: continue
        if x>=len(t): break
        if t[x]-t[e]!=H*60: continue
        s=np.sign(D[i])
        if s>0:
            net=np.log(bbo[x]/bao[e])*10000; gross=np.log(pb[x-1]/pb[e-1])*10000
        else:
            net=-np.log(bao[x]/bbo[e])*10000; gross=-np.log(pb[x-1]/pb[e-1])*10000
        trades_net.append(net); trades_gross.append(gross); last=x
    tn=np.array(trades_net); tg=np.array(trades_gross)
    if len(tn)>30:
        pf=tn[tn>0].sum()/-tn[tn<0].sum() if (tn<0).any() else np.inf
        print(f"  BACKTEST |D|>=p95({thr95:.1f}bp) H={H}min entry=next-bar-open bid/ask, non-overlap: n={len(tn)}")
        print(f"    gross mid mean={tg.mean():+.2f}bp/trade | NET executable mean={tn.mean():+.2f}bp/trade (t={tn.mean()/(tn.std(ddof=1)/np.sqrt(len(tn))):+.2f}, win={(tn>0).mean()*100:.0f}%, PF={pf:.2f})")
        print(f"    implied cost drag = gross-net = {tg.mean()-tn.mean():.2f}bp")
print("\ndone")
