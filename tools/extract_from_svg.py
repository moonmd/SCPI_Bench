#!/usr/bin/env python3
import sys, re, base64, json, csv
if len(sys.argv) < 2:
    print("Usage: extract_from_svg.py path.svg", file=sys.stderr); sys.exit(2)
text = open(sys.argv[1], "r", encoding="utf-8").read()
m = re.search(r"<sigbench>([^<]+)</sigbench>", text)
if not m:
    print("No embedded sigbench data found", file=sys.stderr); sys.exit(1)
b = base64.b64decode(m.group(1))
obj = json.loads(b.decode("utf-8"))
xs, ys = obj.get("x", []), obj.get("y", [])
w = csv.writer(sys.stdout, lineterminator="\n")
w.writerow(["t_s","v"])
for t, v in zip(xs, ys):
    w.writerow([t, v])
