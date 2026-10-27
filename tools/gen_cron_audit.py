#!/usr/bin/env python3
"""Generate a cron audit (Markdown, to the path given as argv[1]) from LIVE state: systemd per unit,
the organ_run_log ledger, each organ's static INSERT targets, and each table's
observed last write. Re-run after any re-lay; nothing here is typed."""
import re, subprocess, sqlite3, sys, os
from pathlib import Path
from datetime import datetime
import sqlite_vec

HOME = Path.home(); UNIT_DIR = HOME/".config/systemd/user"; ROOT = HOME/".claude/memory"
DB = ROOT/"claude_memory.db"
def sc(*a): return subprocess.run(["systemctl","--user",*a],capture_output=True,text=True).stdout.strip()
def show(u,*p):
    d={}
    for ln in sc("show",u,*[f"-p{x}" for x in p]).splitlines():
        if "=" in ln: k,v=ln.split("=",1); d[k]=v
    return d

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); conn.enable_load_extension(True); sqlite_vec.load(conn)
# observed last write per table
last_write = {}
for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
    cols=[r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")]
    ts=next((k for k in ("created_at","run_started_at","observed_at","timestamp","ts","updated_at","recorded_at","generated_at","scanned_at","started_at") if k in cols),None)
    if not ts: continue
    try: mx=conn.execute(f"SELECT MAX({ts}) FROM '{t}'").fetchone()[0]; last_write[t]=str(mx or "-")[:16]
    except Exception: pass

rows=[]
for t in sorted(UNIT_DIR.glob("wizard-library-*.timer")):
    name=t.stem; unit=name.replace("wizard-library-",""); svc=name+".service"
    ts=show(t.name,"UnitFileState","ActiveState"); ss=show(svc,"Result","ExecMainStatus")
    cal="; ".join(re.findall(r"^OnCalendar=(.*)$",t.read_text(),re.M))
    sf=UNIT_DIR/svc; stxt=sf.read_text() if sf.exists() else ""
    m=re.search(r"^ExecStart=(.*)$",stxt,re.M); toks=(m.group(1).replace("%h",str(HOME)).split() if m else [])
    py=next((x for x in toks if x.endswith(".py")),"")
    organ=Path(py).name if py else "-"
    # static writers in the organ module (+ claude_memory.py verb if that is the target)
    writes=set()
    if py and Path(py).exists():
        # the entry module plus ONE level of local organ imports (belief_cli -> belief_engine)
        srcs=[Path(py)]
        src0=Path(py).read_text(errors="replace")
        for mod in set(re.findall(r"^\s*(?:from\s+(\w+)\s+import|import\s+(\w+))",src0,re.M)):
            name=mod[0] or mod[1]; cand=Path(py).parent/f"{name}.py"
            if cand.exists() and cand!=Path(py): srcs.append(cand)
        for f in srcs:
            writes|={w for w in re.findall(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+([a-z_0-9]+)",f.read_text(errors="replace"))}
    # ledger: last run + lateness
    r=conn.execute("SELECT run_started_at, lateness_seconds, result FROM organ_run_log WHERE unit_name=? ORDER BY run_started_at DESC LIMIT 1",(t.name,)).fetchone()
    last, late, lres = (r[0][:16], r[1], r[2]) if r else ("-",None,"-")
    late_s = "-" if late is None else (f"{late//3600}h{(late%3600)//60:02d}m" if late>=3600 else f"{late//60}m")
    # generation: does the installed service still carry the old-generation markers?
    old = bool(re.search(r"^Environment=CUDA_VISIBLE_DEVICES=|EnvironmentFile=/home/|anaconda3/bin/python3",stxt,re.M))
    packaged=(ROOT/"cron"/svc).exists()
    # verdict
    if ts.get("UnitFileState")!="enabled" or ts.get("ActiveState")!="active": v="NOT ARMED"
    elif ss.get("ExecMainStatus","0")!="0": v=f"EXIT {ss.get('ExecMainStatus')}"
    elif not py or not Path(py).exists(): v="TARGET MISSING"
    else: v="OK"
    if old: v+=" · old-gen unit"
    if not packaged: v+=" · personal"
    # tables written with observed last write
    wl=", ".join(f"`{w}`({last_write.get(w,'?')})" for w in sorted(writes) if w in last_write) or "-"
    rows.append((unit,organ,re.sub(r"\s+[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)+$", "", cal)[:28],wl,last,late_s,v))   # any IANA zone suffix

date=datetime.now().strftime("%Y-%m-%d %H:%M %Z")
out=[]
out.append(f"# Cron audit — {date}\n")
out.append("*Generated from live state by `gen_cron_audit.py`: `systemctl show` per unit, the `organ_run_log` ledger, each organ module's static `INSERT INTO` targets, and each table's observed last write. Nothing here is typed. Re-run after any re-lay.*\n")
out.append(f"**Population:** {len(rows)} timer files under `~/.config/systemd/user/`. Ledger timestamps are UTC.\n")
out.append("| unit | organ | OnCalendar | tables written (last write, UTC) | last run (UTC) | late | verdict |")
out.append("|---|---|---|---|---|---|---|")
for r in rows: out.append("| "+" | ".join(str(x) for x in r)+" |")
open(sys.argv[1],"w").write("\n".join(out)+"\n")
print(f"wrote {sys.argv[1]}: {len(rows)} rows; old-gen units: {sum('old-gen' in r[6] for r in rows)}; not-OK: {sum(not r[6].startswith('OK') for r in rows)}")
