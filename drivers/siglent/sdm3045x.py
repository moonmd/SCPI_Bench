from __future__ import annotations
from core.instrument import Instrument
import re

class SDM3045X(Instrument):
    def set_function(self, function: str = "VOLT:DC", rng: float | None = None) -> None:
        self._last_function = function
        self.t.write(f'FUNC "{function}"')
        if function.upper().startswith("VOLT:DC"):
            self.t.write("CONF:VOLT:DC" + (f" {rng}" if rng is not None else ""))
        # Ensure a simple immediate trigger and single sample for READ?
        try:
            self.t.write("TRIG:COUN 1")
            self.t.write("TRIG:SOUR IMM")
            self.t.write("SAMP:COUN 1")
        except Exception:
            pass
    def read(self) -> float:
        # Some SDM firmware revisions may require FETCH? after triggering
        # Ensure a fresh initiation sequence
        self.t.write("ABORt")
        self.t.write("INIT")
        resp = self.t.query("READ?")
        if not resp:
            # Try FETCH? per SCPI if READ? didn't return
            resp = self.t.query("FETCh?")
        if not resp and getattr(self, "_last_function", "").upper().startswith("VOLT:DC"):
            # As a last resort, use MEASure:VOLTage:DC?
            resp = self.t.query("MEAS:VOLT:DC?")
        # Extract first float from response to be robust to extra tokens
        if resp:
            m = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", resp)
            if m:
                return float(m.group(0))
        # If still nothing, surface instrument error if any
        try:
            err = self.t.query("SYST:ERR?")
        except Exception:
            err = None
        raise RuntimeError(f"SDM3045X returned no data for measurement. Last error: {err}")
