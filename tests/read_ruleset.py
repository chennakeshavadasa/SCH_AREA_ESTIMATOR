#!/usr/bin/env python3
"""Read sky130A.tcl and dump ruleset/setup info to read_ruleset.txt"""
import re
from pathlib import Path

TCL = Path("/home/nithin/.ciel/sky130A/libs.tech/magic/sky130A.tcl")
OUT = Path("read_ruleset.txt")

if not TCL.exists():
    print(f"NOT FOUND: {TCL}"); exit(1)

content = TCL.read_text(errors="replace")
lines   = content.splitlines()

out = []
out.append(f"FILE: {TCL}  ({len(lines)} lines)\n")

out.append("═"*70)
out.append("SECTION 1: Every line containing 'ruleset'")
out.append("═"*70)
for i, l in enumerate(lines, 1):
    if "ruleset" in l:
        out.append(f"  {i:5d}:  {l.rstrip()}")

out.append("\n" + "═"*70)
out.append("SECTION 2: Every line containing 'tech_setup' or 'sky130::setup'")
out.append("═"*70)
for i, l in enumerate(lines, 1):
    if "tech_setup" in l or ("setup" in l and "sky130" in l):
        out.append(f"  {i:5d}:  {l.rstrip()}")

out.append("\n" + "═"*70)
out.append("SECTION 3: Full proc bodies that reference 'ruleset'")
out.append("═"*70)
proc_starts = [i for i,l in enumerate(lines) if re.match(r'\s*proc\s+', l)]
for start in proc_starts:
    depth = 0; end = start
    for j in range(start, min(start+2000, len(lines))):
        depth += lines[j].count("{") - lines[j].count("}")
        if j > start and depth <= 0:
            end = j; break
    block = "\n".join(lines[start:end+1])
    if "ruleset" in block:
        proc_name = re.match(r'\s*proc\s+(\S+)', lines[start])
        name = proc_name.group(1) if proc_name else "?"
        out.append(f"\n── PROC: {name}  (lines {start+1}–{end+1}) ──")
        for l in lines[start:end+1]:
            out.append(f"  {l.rstrip()}")

result = "\n".join(out)
OUT.write_text(result)
print(f"Done — {len(result)} chars written to {OUT}")
print(f"Copy {OUT} to ~/eda/ and share it")
