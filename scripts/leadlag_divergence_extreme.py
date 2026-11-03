import numpy as np, json
z=np.load("/tmp/leadlag_data.npz")
def get(inst): return z[f"{inst}_t"], z[f"{inst}_mid"], z[f"{inst}_bo"], z[f"{inst}_ao"]
def kret(t,p,K):
    r=np.full(len(t),np.nan); ok=np.zeros(len(t),bool); ok[K:]=(t[K:]-t[:-K]==K*60); r[K:]=np.log(p[K:]/p[:-K]); r[~ok]=np.nan; return r
PAIRS=[("SPX500_USD","JP225_USD"),("NAS100_USD","SPX500_USD"),("EUR_USD","GBP_USD"),("USD_JPY","GBP_JPY"),("SPX500_USD","AUD_JPY")]
K=5
for A,B in PAIRS:
    ta,pa,_,_=get(A); tb,pb,bbo,bao=get(B)
    common,ia,ib=np.intersect1d(ta,tb,return_indices=True); t=common; pa=pa[ia]; pb=pb[ib]; bbo=bbo[ib]; bao=bao[ib]
    Ra=kret(t,pa,K); Rb=kret(t,pb,K); m=np.isfinite(Ra)&np.isfinite(Rb)
    beta=float(np.cov(Ra[m],Rb[m])[0,1]/np.var(Ra[m])); D=(beta*Ra-Rb)*10000; absD=np.abs(D[m])
    for pct in (99, 99.5):
        thr=np.nanpercentile(absD,pct)
        for H in (15,30):
            idx=np.where(m & (np.abs(D)>=thr))[0]; tn=[]; tg=[]; last=-10**9
            for i in idx:
                e=i+1; x=e+H
                if e<=last: continue
                if x>=len(t): break
                if t[x]-t[e]!=H*60: continue
                s=np.sign(D[i])
                if s>0: net=np.log(bbo[x]/bao[e])*10000; gross=np.log(pb[x-1]/pb[e-1])*10000
                else: net=-np.log(bao[x]/bbo[e])*10000; gross=-np.log(pb[x-1]/pb[e-1])*10000
                tn.append(net); tg.append(gross); last=x
            tn=np.array(tn); tg=np.array(tg)
            if len(tn)>30:
                pf=tn[tn>0].sum()/-tn[tn<0].sum() if (tn<0).any() else np.inf
                print(f"{A}->{B} |D|>=p{pct}({thr:.1f}bp) H={H}: n={len(tn)} gross={tg.mean():+.2f}bp NET={tn.mean():+.2f}bp t={tn.mean()/(tn.std(ddof=1)/np.sqrt(len(tn))):+.2f} win={(tn>0).mean()*100:.0f}% PF={pf:.2f}")
    print()
