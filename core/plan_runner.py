from __future__ import annotations
import time, csv, yaml, statistics
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional
from .logging_io import write_parquet, save_svg_with_embedded_data

@dataclass
class Context:
    psu: Any
    dmm: Any
    scope: Optional[Any] = None
    ens210: Optional[Any] = None


def run_plan(plan_path: str, ctx: Context, out_path: str):
    with open(plan_path, "r") as f:
        plan = yaml.safe_load(f)

    steps = plan.get("steps", [])
    sample = float(plan.get("sample_rate_hz", 1.0))
    hold_default = float(plan.get("hold_s", 1.0))
    status_every_s = float(plan.get("status_every_s", 0.0))

    safety = plan.get("safety", {})
    vmax = float(safety.get("vmax", 17.0))
    # vmin_abort is opt-in. If not provided in the plan, it is disabled.
    _vmin = safety.get("vmin_abort", None)
    vmin_abort = float(_vmin) if _vmin is not None else None
    max_hours = float(safety.get("max_hours", 12.0))
    # Optional temp safety using ENS210 if available
    _maxt = safety.get("maxtemp_c", None)
    maxtemp_c = float(_maxt) if _maxt is not None else None
    _maxdtemp = safety.get("max_dtemp_c_per_min", None)
    max_dtemp_c_per_min = float(_maxdtemp) if _maxdtemp is not None else None
    temp_window_s = float(safety.get("temp_window_s", 60.0))
    negdv = safety.get("negdv", {"enabled": False})
    negdv_enabled = bool(negdv.get("enabled", False))
    negdv_window_s = float(negdv.get("window_s", 60.0))
    negdv_threshold_v = float(negdv.get("threshold_v", -0.06))
    negdv_require_s = float(negdv.get("require_s", 120.0))

    t0 = time.time()
    plan_start = t0
    window = deque()
    temp_window = deque()

    def negdv_ok() -> bool:
        if not negdv_enabled or len(window) < 2:
            return False
        t_first, v_first = window[0]
        t_last, v_last = window[-1]
        duration = t_last - t_first
        dv = v_last - v_first
        return duration >= negdv_require_s and dv <= negdv_threshold_v

    use_parquet = out_path.lower().endswith((".parquet", ".pq"))
    if use_parquet:
        rows: list[dict] = []
    else:
        fcsv = open(out_path, "w", newline="")
        writer = csv.writer(fcsv)
        writer.writerow(["t_s","v_set","i_set","v_meas","i_meas","scope_vpp","scope_vrms","temp_c","humidity_pct","ens_ok"])
        try:
            fcsv.flush()
        except Exception:
            pass

    def safe_read_ens():
        if not ctx.ens210:
            return None
        try:
            return ctx.ens210.read()
        except Exception:
            return {"temp_c": None, "temp_k": None, "rh_pct": None, "ok": False}

    last_status = 0.0
    for idx, step in enumerate(steps):
        psu_cfg = step.get("psu", {})
        dmm_cfg = step.get("dmm", {})
        scope_cfg = step.get("scope")
        hold = float(step.get("hold_s", hold_default))

        ch = psu_cfg.get("ch", "CH1")
        if ctx.psu:
            if "current" in psu_cfg:
                ctx.psu.set_current(ch, float(psu_cfg["current"]))
            if "voltage" in psu_cfg:
                ctx.psu.set_voltage(ch, float(psu_cfg["voltage"]))
            if psu_cfg.get("on", True):
                ctx.psu.output_on(ch)
            else:
                ctx.psu.output_off(ch)

        func = dmm_cfg.get("function", "VOLT:DC")
        rng = dmm_cfg.get("range", None)
        if ctx.dmm:
            ctx.dmm.set_function(func, rng)

        if scope_cfg and ctx.scope:
            chs = scope_cfg.get("channel", "C1")
            probe = scope_cfg.get("probe")
            scale = scope_cfg.get("scale")
            tdiv = scope_cfg.get("tdiv", 0.001)
            trig_level = scope_cfg.get("trig_level", 0.02)
            trig_slope = scope_cfg.get("trig_slope", "POS")
            points = scope_cfg.get("points")
            # Keep the scope running; we'll arm a single capture just-in-time
            ctx.scope.set_channel(chs, on=True, scale=scale, probe=probe)
            ctx.scope.set_timebase(tdiv, points=points)
            ctx.scope.set_trigger_edge(chs, trig_level, trig_slope)

        t_end = time.time() + hold
        last_scope = (None, None)
        if not step.get("accumulate_window", False):
            window.clear()

        while time.time() < t_end:
            if (time.time() - plan_start) > max_hours * 3600:
                print("max_hours reached; aborting")
                return
            vmeas = ctx.dmm.read() if ctx.dmm else None
            imeas = ctx.psu.measure_current(ch) if ctx.psu else None
            if vmeas is not None and vmeas > vmax:
                print("vmax exceeded; turning off and aborting")
                if ctx.psu:
                    ctx.psu.output_off(ch)
                return
            if (vmin_abort is not None) and (vmeas is not None) and idx == 1 and vmeas < vmin_abort and psu_cfg.get("on", True):
                print(f"vmin_abort triggered (v={vmeas} < {vmin_abort}); turning off and aborting")
                if ctx.psu:
                    ctx.psu.output_off(ch)
                return

            if scope_cfg and ctx.scope and last_scope == (None, None):
                delay = float(scope_cfg.get("delay_s", 0.0))
                if (t_end - time.time()) <= (hold - delay):
                    # Use built-in measurements only to avoid large waveform transfers
                    vpp = ctx.scope.measure_vpp(scope_cfg.get("channel", "C1"))
                    vrms = ctx.scope.measure_vrms(scope_cfg.get("channel", "C1"))
                    last_scope = (vpp, vrms)
                    try:
                        ctx.scope.run()
                    except Exception:
                        pass

            now = time.time()
            if vmeas is not None:
                window.append((now, vmeas))
                while window and (now - window[0][0]) > negdv_window_s:
                    window.popleft()
            # Temperature safety checks if ENS210 present
            ens = safe_read_ens()
            if ens and ens.get("temp_c") is not None:
                temp_c_val = float(ens.get("temp_c"))
                temp_window.append((now, temp_c_val))
                while temp_window and (now - temp_window[0][0]) > temp_window_s:
                    temp_window.popleft()
                if (maxtemp_c is not None) and temp_c_val > maxtemp_c:
                    print(f"maxtemp_c exceeded (t={temp_c_val:.2f}C > {maxtemp_c}C); turning off and aborting")
                    if ctx.psu:
                        ctx.psu.output_off(ch)
                    return
                if (max_dtemp_c_per_min is not None) and len(temp_window) >= 2:
                    t_first, temp_first = temp_window[0]
                    duration = now - t_first
                    if duration >= max(10.0, 0.25 * temp_window_s):
                        slope_per_min = (temp_c_val - temp_first) / max(duration, 1e-9) * 60.0
                        if slope_per_min > max_dtemp_c_per_min:
                            print(f"max_dtemp_c_per_min exceeded (dT/dt={slope_per_min:.2f}C/min > {max_dtemp_c_per_min}C/min); turning off and aborting")
                            if ctx.psu:
                                ctx.psu.output_off(ch)
                            return
            if step.get("terminate_on_negdv", False) and negdv_ok():
                if ctx.psu:
                    ctx.psu.output_off(ch)
                ens = safe_read_ens()
                rec = {
                    "t_s": now - t0,
                    "v_set": psu_cfg.get("voltage"),
                    "i_set": psu_cfg.get("current"),
                    "v_meas": vmeas,
                    "i_meas": imeas,
                    "scope_vpp": last_scope[0],
                    "scope_vrms": last_scope[1],
                    "temp_c": (ens or {}).get("temp_c"),
                    "humidity_pct": (ens or {}).get("rh_pct"),
                    "ens_ok": (ens or {}).get("ok"),
                }
                if use_parquet:
                    rows.append(rec)
                else:
                    writer.writerow(list(rec.values()))
                    try:
                        fcsv.flush()
                    except Exception:
                        pass
                return

            ens = safe_read_ens()
            rec = {
                "t_s": now - t0,
                "v_set": psu_cfg.get("voltage"),
                "i_set": psu_cfg.get("current"),
                "v_meas": vmeas,
                "i_meas": imeas,
                "scope_vpp": last_scope[0],
                "scope_vrms": last_scope[1],
                "temp_c": (ens or {}).get("temp_c"),
                "humidity_pct": (ens or {}).get("rh_pct"),
                "ens_ok": (ens or {}).get("ok"),
            }
            if use_parquet:
                rows.append(rec)
            else:
                writer.writerow(list(rec.values()))
                try:
                    fcsv.flush()
                except Exception:
                    pass
            # Periodic status to stdout
            if status_every_s > 0:
                if (now - last_status) >= status_every_s:
                    print(f"t={now - t0:6.1f}s step={idx} vset={psu_cfg.get('voltage')} v={vmeas} i={imeas} temp={(ens or {}).get('temp_c')}")
                    last_status = now
            time.sleep(1.0 / max(sample, 1e-9))

    if use_parquet:
        write_parquet(rows, out_path)
    else:
        fcsv.close()
