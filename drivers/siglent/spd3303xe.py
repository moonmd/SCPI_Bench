from __future__ import annotations
from core.instrument import Instrument


class SPD3303XE(Instrument):
    """Siglent SPD3303X-E power supply SCPI driver."""

    def set_voltage(self, channel: str, voltage_v: float) -> None:
        # Explicit channel addressing is the most compatible across firmwares
        self.t.write(f"{channel}:VOLT {voltage_v}")

    def set_current(self, channel: str, current_a: float) -> None:
        self.t.write(f"{channel}:CURR {current_a}")

    def measure_voltage(self, channel: str) -> float:
        return float(self.t.query(f"MEAS:VOLT? {channel}"))

    def measure_current(self, channel: str) -> float:
        return float(self.t.query(f"MEAS:CURR? {channel}"))

    def output_on(self, channel: str) -> None:
        # Some firmware revisions require remote mode and different output syntaxes
        # Try a handful of compatible enables to cover variations
        chnum = {"CH1": 1, "CH2": 2, "CH3": 3}.get(channel.upper())
        try:
            self.t.write("SYST:REM")
        except Exception:
            pass
        try:
            self.t.write(f"INST {channel}")
        except Exception:
            pass
        for cmd in [
            "OUTP ON",                      # master output on
            f"OUTP {channel},ON",           # per-channel on
            "OUTPut:STATe ON",              # alternate master syntax
            (f"OUTPut{chnum}:STATe ON" if chnum else None),  # alternate per-channel syntax
        ]:
            if not cmd:
                continue
            try:
                self.t.write(cmd)
            except Exception:
                pass
        # Optional: block until operations complete if supported
        try:
            _ = self.t.query("*OPC?")
        except Exception:
            pass

    def output_off(self, channel: str) -> None:
        self.t.write(f"OUTP {channel},OFF")
