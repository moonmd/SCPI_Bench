from __future__ import annotations
import argparse, socket, threading, time, random


def serve(bind: str, handler):
    host, port = bind.split(":"); port = int(port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port)); sock.listen(5)
    print(f"Mock server listening on {bind}")
    while True:
        conn, _ = sock.accept()
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk: break
            data += chunk
            if data.endswith(b"\n"): break
        cmd = data.decode().strip()
        resp = handler(cmd)
        if resp is not None: conn.sendall((resp + "\n").encode())
        conn.close()


def spd_handler():
    state = {"idn":"SIGLENT,SPD3303X-E,MOCK,1.00","CH1":{"V":5.0,"I":1.0,"ON":False}}
    def h(cmd: str):
        if cmd == "*IDN?": return state["idn"]
        if cmd.startswith("CH1:VOLT "): state["CH1"]["V"] = float(cmd.split()[-1]); return None
        if cmd.startswith("CH1:CURR "): state["CH1"]["I"] = float(cmd.split()[-1]); return None
        if cmd == "MEAS:VOLT? CH1": v = state["CH1"]["V"] - (0.01 if state["CH1"]["ON"] else 0.0); return f"{v:.6f}"
        if cmd == "MEAS:CURR? CH1": i = state["CH1"]["I"] if state["CH1"]["ON"] else 0.0; return f"{i:.6f}"
        if cmd == "OUTP CH1,ON": state["CH1"]["ON"] = True; return None
        if cmd == "OUTP CH1,OFF": state["CH1"]["ON"] = False; return None
        if cmd == "SYST:ERR?": return "0,No error"
        return ""
    return h


def sdm_handler():
    state = {"idn":"SIGLENT,SDM3045X,MOCK,1.00","func":"VOLT:DC","rng":10.0,"last_v":5.0}
    def h(cmd: str):
        if cmd == "*IDN?": return state["idn"]
        if cmd.startswith('FUNC "') and cmd.endswith('"'): state["func"] = cmd.split('"')[1]; return None
        if cmd.startswith("CONF:VOLT:DC"):
            parts = cmd.split()
            if len(parts) == 2:
                try: state["rng"] = float(parts[1])
                except Exception: pass
            return None
        if cmd == "READ?":
            v = state["last_v"] + random.uniform(-0.002, 0.002); return f"{v:.6f}"
        if cmd == "SYST:ERR?": return "0,No error"
        return ""
    return h


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--spd", default="127.0.0.1:15025")
    ap.add_argument("--sdm", default="127.0.0.1:15026")
    args = ap.parse_args()
    threading.Thread(target=serve, args=(args.spd, spd_handler()), daemon=True).start()
    threading.Thread(target=serve, args=(args.sdm, sdm_handler()), daemon=True).start()
    print("Press Ctrl+C to stop.")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: pass
