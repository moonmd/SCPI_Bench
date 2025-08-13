from __future__ import annotations
import argparse, glob
from core.transport import SocketTransport, USBTMCTransport, LoggingTransport
from core.plan_runner import run_plan, Context
from drivers.siglent.spd3303xe import SPD3303XE
from drivers.siglent.sdm3045x import SDM3045X
from drivers.siglent.sds1104xe import SDS1104XE
from drivers.ams.ens210_serial import ENS210Serial


def parse_host_port(s: str):
    if ":" in s:
        host, port = s.split(":", 1)
        return host, int(port)
    return s, 5025


def _usbtmc_idn(dev: str) -> str | None:
    try:
        t = USBTMCTransport(dev)
        try:
            return t.query("*IDN?") or None
        finally:
            # ensure we close the handle used for scan
            try:
                t._close()
            except Exception:
                pass
    except Exception:
        return None


def _usb_autodetect():
    mapping = {}
    for dev in sorted(glob.glob("/dev/usbtmc*")):
        idn = _usbtmc_idn(dev)
        if not idn:
            continue
        if "SPD3303X" in idn and "spd" not in mapping:
            mapping["spd"] = dev
        elif "SDM3045X" in idn and "sdm" not in mapping:
            mapping["sdm"] = dev
        elif "SDS1104X" in idn and "scope" not in mapping:
            mapping["scope"] = dev
    return mapping


def main():
    p = argparse.ArgumentParser(prog="sigbench")
    sub = p.add_subparsers(dest="cmd", required=True)

    scanp = sub.add_parser("scan", help="List autodetected USBTMC instruments and suggested role mapping")

    ensp = sub.add_parser("ens", help="Read ENS210 via serial USB-I2C dongle for sanity check")
    ensp.add_argument("--ens210", required=True, help="/dev/ttyACM* path to AMS USB-I2C dongle")
    ensp.add_argument("--addr", default="0x43", help="I2C address of ENS210 (default 0x43)")
    ensp.add_argument("--count", type=int, default=5, help="Number of readings to take")
    ensp.add_argument("--delay", type=float, default=1.0, help="Delay between readings (seconds)")

    runp = sub.add_parser("run", help="Run a YAML test plan")
    runp.add_argument("plan")
    runp.add_argument("--spd")
    runp.add_argument("--sdm")
    runp.add_argument("--scope")
    runp.add_argument("--ens210", help="/dev/ttyACM* path to AMS USB-I2C dongle (optional)")
    runp.add_argument("--ens-only", action="store_true", help="Run without SPD/SDM; only log ENS210 if provided")
    runp.add_argument("--debug-log", help="Path to write SCPI I/O log (NDJSON)")
    runp.add_argument("--tcp-oneshot", action="store_true", help="Use non-persistent TCP (one connection per command)")
    runp.add_argument("--out", required=True)

    args = p.parse_args()

    if args.cmd == "scan":
        devices = sorted(glob.glob("/dev/usbtmc*"))
        if not devices:
            print("No /dev/usbtmc* devices found.")
            return
        print("Detected USBTMC devices and IDNs:")
        for dev in devices:
            idn = _usbtmc_idn(dev) or "(no response)"
            print(f"  {dev}: {idn}")
        autod = _usb_autodetect()
        if autod:
            print("\nSuggested role mapping:")
            for role in ("spd", "sdm", "scope"):
                if role in autod:
                    print(f"  {role}: {autod[role]}")
        return

    if args.cmd == "ens":
        addr = int(args.addr, 0)
        try:
            # Reuse the same debug log file if provided
            log_fp = open(args.debug_log, "a") if getattr(args, "debug_log", None) else None
            ens = ENS210Serial(args.ens210, addr=addr, log_file=log_fp)
        except Exception as e:
            raise SystemExit(f"Failed to open ENS210 serial dongle at {args.ens210}: {e}")
        try:
            import time as _time
            print("timestamp,temp_c,temp_k,rh_pct,ok,t_valid,h_valid,t_crc_ok,h_crc_ok")
            for _ in range(max(args.count, 1)):
                rec = ens.read()
                ts = f"{_time.time():.3f}"
                print(
                    f"{ts},{rec.get('temp_c')},{rec.get('temp_k')},{rec.get('rh_pct')},"
                    f"{rec.get('ok')},{rec.get('t_valid')},{rec.get('h_valid')},{rec.get('t_crc_ok')},{rec.get('h_crc_ok')}"
                )
                _time.sleep(max(args.delay, 0.0))
        finally:
            try:
                ens.close()
            except Exception:
                pass
        return

    if args.cmd == "run":
        spd_arg = args.spd
        sdm_arg = args.sdm
        scope_arg = args.scope

        if not args.ens_only and (not spd_arg or not sdm_arg):
            raise SystemExit("Missing SPD/SDM targets. Provide --spd and --sdm, or use --ens-only.")

        # Open debug log once if requested
        log_fp = open(args.debug_log, "a") if args.debug_log else None

        def open_target(arg):
            if isinstance(arg, str) and arg.startswith("/dev/usbtmc"):
                t = USBTMCTransport(arg)
                return LoggingTransport(t, role="usbtmc", log_file=log_fp) if log_fp else t
            h, p = parse_host_port(arg)
            # Default to persistent TCP unless user explicitly requests one-shot
            t = SocketTransport(h, p, persistent=not args.tcp_oneshot, connect_backoff_s=0.05 if args.tcp_oneshot else 0.0)
            return LoggingTransport(t, role=f"tcp:{h}:{p}", log_file=log_fp) if log_fp else t

        spd = SPD3303XE(open_target(spd_arg)) if spd_arg else None
        sdm = SDM3045X(open_target(sdm_arg)) if sdm_arg else None
        scope = SDS1104XE(open_target(scope_arg)) if scope_arg else None
        ens = ENS210Serial(args.ens210, log_file=log_fp) if args.ens210 else None

        ctx = Context(psu=spd, dmm=sdm, scope=scope, ens210=ens)
        run_plan(args.plan, ctx, args.out)


if __name__ == "__main__":
    main()
