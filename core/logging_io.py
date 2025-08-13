from __future__ import annotations
import io, json, base64
from typing import Optional

def write_parquet(rows: list[dict], out_path: str) -> None:
    import pyarrow as pa, pyarrow.parquet as pq
    if not rows:
        pq.write_table(pa.table({}), out_path); return
    cols = sorted({k for r in rows for k in r.keys()})
    arrays = {c: [r.get(c, None) for r in rows] for c in cols}
    table = pa.table(arrays)
    pq.write_table(table, out_path, compression="zstd")


def save_svg_with_embedded_data(xs: list[float], ys: list[float], out_svg: str, title: str = "Waveform", extra_meta: Optional[dict] = None) -> dict:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(8,3), dpi=120)
    ax = fig.add_subplot(111)
    ax.plot(xs, ys)
    ax.set_title(title); ax.set_xlabel("Time (s)"); ax.set_ylabel("Voltage (V)")
    fig.tight_layout()
    buf = io.StringIO(); fig.savefig(buf, format="svg"); plt.close(fig)
    svg_text = buf.getvalue()
    payload = {"format":"sigbench/waveform","version":1,"x":xs,"y":ys,"meta":extra_meta or {}}
    b64 = base64.b64encode(json.dumps(payload, separators=(",",":")).encode()).decode()
    if "<metadata>" in svg_text:
        svg_text = svg_text.replace("<metadata>", f"<metadata><sigbench>{b64}</sigbench>", 1)
    else:
        insert_at = svg_text.find(">")+1
        svg_text = svg_text[:insert_at] + f"<metadata><sigbench>{b64}</sigbench></metadata>" + svg_text[insert_at:]
    open(out_svg, "w", encoding="utf-8").write(svg_text)
    return {"points": len(xs), "file": out_svg}
