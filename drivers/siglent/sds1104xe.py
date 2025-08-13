from __future__ import annotations
from core.instrument import Instrument
from typing import Literal
import re

Chan = Literal["C1","C2","C3","C4"]

class SDS1104XE(Instrument):
    def run(self):  self.t.write("RUN")
    def stop(self): self.t.write("STOP")
    def single(self): self.t.write("SING")
    def autoset(self): self.t.write("AUTO")
    def set_channel(self, ch: Chan, on: bool=True, scale: float|None=None, offset: float|None=None, probe: int|None=None):
        parts: list[str] = [f"{ch}:TRA {'ON' if on else 'OFF'}"]
        if scale is not None:  parts.append(f"{ch}:SCAL {scale}")
        if offset is not None: parts.append(f"{ch}:OFFS {offset}")
        if probe is not None:  parts.append(f"{ch}:PROB {probe}")
        self.t.write("; ".join(parts))
    def set_timebase(self, scale_s_div: float, points: int|None=None):
        parts: list[str] = [f"TDIV {scale_s_div}"]
        if points is not None: parts.append(f"ACQ:MEMD {points}")
        self.t.write("; ".join(parts))
    def set_trigger_edge(self, src: Chan, level: float, slope: str="POS"):
        self.t.write("; ".join([
            "TRIG:MODE EDGE",
            f"TRIG:EDGE:SOUR {src}",
            f"TRIG:EDGE:SLOP {slope}",
            f"TRIG:LEV {level}",
        ]))
    def _ensure_measurement_enabled(self) -> None:
        if getattr(self, "_meas_enabled", False):
            return
        try:
            self.t.write("MEAS:STAT ON")
            self._meas_enabled = True
        except Exception:
            pass

    def _pava_value(self, ch: Chan, item: str) -> float:
        # Prefer PAVA since it often returns reliably as labeled text
        resp = self.t.query(f"{ch}:PAVA? {item}")
        # Expected like: "C1:PAVA VPP,3.2000V" -> extract first float
        import re
        m = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", resp or "")
        if not m:
            raise RuntimeError(f"Unexpected PAVA response: {resp!r}")
        return float(m.group(0))

    def measure_vpp(self, ch: Chan) -> float:
        self._ensure_measurement_enabled()
        try:
            return self._pava_value(ch, "VPP")
        except Exception:
            # Try MEAS variants
            try:
                return float(self.t.query(f"MEAS:ITEM? VPP,{ch}"))
            except Exception:
                return float(self.t.query(f"MEAS:VPP? {ch}"))

    def measure_vrms(self, ch: Chan) -> float:
        self._ensure_measurement_enabled()
        for item in ("VRMS", "RMS"):
            try:
                return self._pava_value(ch, item)
            except Exception:
                continue
        # Fallback MEAS variants
        try:
            return float(self.t.query(f"MEAS:ITEM? VRMS,{ch}"))
        except Exception:
            return float(self.t.query(f"MEAS:VRMS? {ch}"))
    def get_waveform(self, ch: Chan) -> tuple[list[float], list[float]]:
        # Configure waveform parameters and pause acquisition for a consistent read
        self.t.write("STOP")
        self.t.write("WAV:MODE NORM"); self.t.write("WAV:FORM ASC"); self.t.write(f"WAV:SOUR {ch}")
        # Limit returned points to keep responses fast and reliable
        try:
            self.t.write("WAV:POIN 1200")
        except Exception:
            pass
        # Ensure previous configuration commands are processed before querying
        _ = self.t.query("*OPC?")
        # Preamble may be large; increase timeout if supported
        try:
            self.t.set_timeout(10.0)  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            pre = self.t.query("WAV:PRE?")
            # Extract the first six floats from preamble (XINC, XORIG, XREF, YINC, YORIG, YREF)
            nums = [float(m.group(0)) for m in re.finditer(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", pre)]
            if len(nums) < 6:
                raise RuntimeError("incomplete preamble")
            xinc, xorig, xref, yinc, yorig, yref = nums[:6]
        except Exception:
            # Fallback: query components individually (smaller responses)
            def qf(cmd: str) -> float:
                return float(self.t.query(cmd))
            xinc = qf("WAV:XINC?")
            xorig = qf("WAV:XOR?") if hasattr(self, 't') else qf("WAV:XORIG?")
            try:
                xref = qf("WAV:XREF?")
            except Exception:
                xref = 0.0
            yinc = qf("WAV:YINC?")
            try:
                yorig = qf("WAV:YOR?") if hasattr(self, 't') else qf("WAV:YORIG?")
            except Exception:
                yorig = 0.0
            try:
                yref = qf("WAV:YREF?")
            except Exception:
                yref = 0.0
        raw = self.t.query("WAV:DATA?")
        ys = [float(v) for v in raw.split(",") if v.strip()]
        xs=[xorig+i*xinc for i in range(len(ys))]; vs=[(p-yref)*yinc+yorig for p in ys]
        try:
            self.t.write("RUN")
        except Exception:
            pass
        return xs,vs
