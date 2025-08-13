from __future__ import annotations
from .transport import Transport

class Instrument:
    def __init__(self, transport: Transport): self.t = transport
    def idn(self) -> str: return self.t.query("*IDN?")
    def reset(self) -> None: self.t.write("*RST")
    def clear(self) -> None: self.t.write("*CLS")
    def error(self) -> str: return self.t.query("SYST:ERR?")
