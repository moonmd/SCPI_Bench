from __future__ import annotations
import serial, time, re, json

class ENS210Serial:
    def __init__(self, port: str, addr: int = 0x43, baud: int = 115200, timeout: float = 0.5, ignore_crc: bool = False, log_file=None):
        self.port = port; self.addr = addr; self.dev8 = (addr << 1) & 0xFE
        self.ignore_crc = ignore_crc
        self.log = log_file
        self.ser = serial.Serial(port, baudrate=baud, timeout=timeout)
        self._write("\n"); time.sleep(0.1)
        # Configure I2C options once; values are hex without 0x prefix on this dongle
        out = self._query(f"i2c opt dev {self.dev8:02X} asize 1 vsize 1 speed 100000")
        if "error" in (out or "").lower():
            self._query(f"i2c opt dev {self.addr:02X} asize 1 vsize 1 speed 100000")
        # Small delay then scan to wake firmware
        time.sleep(0.05)
        self._query("i2c scan")

    def close(self):
        try: self.ser.close()
        except Exception: pass

    def _write(self, s: str):
        if not s.endswith("\n"): s += "\n"
        self.ser.write(s.encode("ascii"))
        self._log("write", s.strip())

    def _read_all(self) -> str:
        self.ser.flush()
        chunks = []
        deadline = time.time() + 0.4
        last_data_ts = time.time()
        while time.time() < deadline:
            ch = self.ser.read(256)
            if ch:
                chunks.append(ch)
                last_data_ts = time.time()
                # If we received a newline and buffer is momentarily empty, allow a brief settle
                if b"\n" in ch:
                    time.sleep(0.01)
            else:
                # No immediate data; if we've been idle for 100ms and past deadline margin, break
                if (time.time() - last_data_ts) > 0.1 and time.time() > (deadline - 0.2):
                    break
                time.sleep(0.01)
        out = b"".join(chunks).decode(errors="ignore")
        if out:
            self._log("read", out.strip())
        return out

    def _query(self, s: str) -> str:
        self._write(s); return self._read_all()

    def _log(self, op: str, data: str) -> None:
        if not self.log:
            return
        try:
            rec = {"ts": time.time(), "role": "ens210", "op": op, "remote": self.port, "data": data}
            self.log.write(json.dumps(rec, separators=(",", ":")) + "\n")
            try:
                self.log.flush()
            except Exception:
                pass
        except Exception:
            pass

    def _ensure_opt(self) -> None:
        # Leave device selection sticky per user guidance; do not re-send each transaction
        return

    @staticmethod
    def _crc7(val: int) -> int:
        CRC7POLY=0x89; CRC7WIDTH=7; CRC7IVEC=0x7F; DATA7WIDTH=17
        pol = CRC7POLY << (DATA7WIDTH - CRC7WIDTH - 1)
        bit = 1 << (DATA7WIDTH - 1)
        v = (val << CRC7WIDTH); pol <<= CRC7WIDTH; v |= CRC7IVEC
        while bit >= 1:
            if (v & (bit << CRC7WIDTH)): v ^= pol
            bit >>= 1; pol >>= 1
        return v & ((1<<CRC7WIDTH)-1)

    @staticmethod
    def _decode(val24: int):
        data = (val24 >> 0) & 0xFFFF
        valid = (val24 >> 16) & 0x1
        crc = (val24 >> 17) & 0x7F
        payl = (val24 >> 0) & 0x1FFFF
        crc_ok = (ENS210Serial._crc7(payl) == crc)
        return data, bool(valid), crc_ok

    @staticmethod
    def _try_alternate_decode(b0: int, b1: int, b2: int):
        # Try alternate byte order if CRC fails: MSB-first
        val24 = (b0 << 16) | (b1 << 8) | b2
        return ENS210Serial._decode(val24), val24

    def start_single_shot(self):
        # Prefer raw command on this dongle; fall back to others
        resp = self._query("i2c raw 22 03")
        if not resp:
            resp = self._query("i 22 03")
        if not resp:
            self._query("i2c wr 22 03")

    def read_t_h_raw(self):
        # This dongle supports combined raw write+read: 'i2c raw 30 r6'
        resp = self._query("i2c raw 30 r6")
        # Parse a line like: "i2c: raw dev 86: f6 4c e3 54 12 34 error=none"
        line = None
        for ln in (resp or "").splitlines():
            if ln.strip().startswith("i2c: raw dev"):
                line = ln; break
        if not line:
            # Last resort: extract any hex pairs from resp
            hexbytes = re.findall(r"\b([0-9a-fA-F]{2})\b", resp or "")
            if len(hexbytes) < 6:
                raise RuntimeError("No read data from dongle")
            b = [int(x,16) for x in hexbytes[:6]]
        else:
            # Only parse bytes after the last colon to skip the 'dev XX:' token
            payload = line.split(":")[-1]
            hexbytes = re.findall(r"\b([0-9a-fA-F]{2})\b", payload)
            b = [int(x,16) for x in hexbytes if re.fullmatch(r"[0-9a-fA-F]{2}", x)][:6]
        t_val = (b[2]<<16)|(b[1]<<8)|b[0]
        h_val = (b[5]<<16)|(b[4]<<8)|b[3]
        return t_val, h_val

    def read(self):
        self.start_single_shot(); time.sleep(0.12)
        t_raw, h_raw = self.read_t_h_raw()
        t_data, t_valid, t_crc_ok = self._decode(t_raw)
        h_data, h_valid, h_crc_ok = self._decode(h_raw)
        if not t_crc_ok:
            (alt_t, alt_valid, alt_crc_ok), alt_val = self._try_alternate_decode((t_raw>>16)&0xFF, (t_raw>>8)&0xFF, t_raw&0xFF)
            if alt_crc_ok:
                t_data, t_valid, t_crc_ok = alt_t, alt_valid, alt_crc_ok
        if not h_crc_ok:
            (alt_h, alt_valid_h, alt_crc_ok_h), alt_val_h = self._try_alternate_decode((h_raw>>16)&0xFF, (h_raw>>8)&0xFF, h_raw&0xFF)
            if alt_crc_ok_h:
                h_data, h_valid, h_crc_ok = alt_h, alt_valid_h, alt_crc_ok_h
        t_k = t_data / 64.0; t_c = t_k - 273.15; rh = h_data / 512.0
        ok = t_valid and h_valid and (t_crc_ok and h_crc_ok or self.ignore_crc)
        return {"temp_c": t_c, "temp_k": t_k, "rh_pct": rh, "ok": ok,
                "t_valid": t_valid, "h_valid": h_valid, "t_crc_ok": t_crc_ok, "h_crc_ok": h_crc_ok}
