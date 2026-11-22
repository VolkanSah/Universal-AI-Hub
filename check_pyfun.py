"""check_pyfun.py - prueft app/.pyfun auf Konsistenz (laeuft ohne Netz, ohne Keys).
Aufruf: python check_pyfun.py   (Exit-Code 1 bei Fehlern)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import config

c = config.get()
prov, models, tools = c.get("LLM_PROVIDERS", {}), c.get("MODELS", {}), c.get("TOOLS", {})
search = c.get("SEARCH_PROVIDERS", {})
errs, warns, cycles = [], [], set()
csv = lambda v: [x.strip() for x in v.split(",") if x.strip()]

for pn, p in prov.items():
    declared = csv(p.get("models", ""))
    if p.get("default_model") and p["default_model"] not in declared:
        errs.append(f"[{pn}] default_model '{p['default_model']}' fehlt in models=")
    for m in declared:
        blk = models.get(m)
        if blk is None:
            warns.append(f"[{pn}] Modell '{m}' hat keinen [MODEL]-Block (keine Limits/Kosten)")
        elif blk.get("provider") != pn:
            errs.append(f"[{pn}] Modell '{m}' gehoert im [MODEL]-Block zu '{blk.get('provider')}'")
    fb = p.get("fallback_to", "")
    if fb and fb not in prov:
        errs.append(f"[{pn}] fallback_to '{fb}' existiert nicht")
    seen, cur = [], pn
    while cur and cur in prov and cur not in seen:
        seen.append(cur); cur = prov[cur].get("fallback_to", "")
    if cur in seen:
        cyc = frozenset(seen[seen.index(cur):])
        if cyc not in cycles:
            cycles.add(cyc)
            warns.append("Fallback-Kreis: " + " -> ".join(seen[seen.index(cur):] + [cur]) + " (visited faengt es ab)")

declared_all = {m for p in prov.values() for m in csv(p.get("models", ""))}
for mn, m in models.items():
    if m.get("provider") not in prov:
        errs.append(f"[MODEL {mn}] Provider '{m.get('provider')}' unbekannt")
    elif mn not in declared_all:
        warns.append(f"[MODEL {mn}] wird von keinem Provider in models= deklariert (Waise)")
    if not m.get("requests_per_day"):
        warns.append(f"[MODEL {mn}] kein requests_per_day -> kein Tageslimit moeglich")

for tn, t in tools.items():
    if t.get("active", "false").lower() != "true":
        continue
    dp, pt = t.get("default_provider", ""), t.get("provider_type", "")
    pool = search if pt == "search" else prov
    if pt in ("llm", "search", "heal", "heal_generate") and dp not in pool:
        errs.append(f"[TOOL {tn}] default_provider '{dp}' unbekannt")
    if pt in ("heal", "heal_generate"):
        warns.append(f"[TOOL {tn}] Healer aktiv: fuehrt Code ohne Sandbox aus!")

print(f"{len(prov)} Provider, {len(models)} Modelle, {len(tools)} Tools")
for w in warns: print("WARN ", w)
for e in errs: print("ERROR", e)
print("OK" if not errs else f"{len(errs)} Fehler")
sys.exit(1 if errs else 0)
