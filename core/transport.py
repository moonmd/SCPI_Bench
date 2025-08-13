from __future__ import annotations
import os
import socket
import time
from typing import Optional
import json


class Transport:
    def write(self, cmd: str) -> None:
        raise NotImplementedError

    def read(self) -> str:
        raise NotImplementedError

    def query(self, cmd: str) -> str:
        self.write(cmd)
        return self.read()


class SocketTransport(Transport):
    """Persistent TCP socket transport for SCPI.

    Many instruments behave best with a single persistent session rather than
    one connection per command. This transport maintains a socket and
    reconnects on failure.
    """

    def __init__(self, host: str, port: int = 5025, timeout: float = 5.0, persistent: bool = True, connect_backoff_s: float = 0.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.persistent = persistent
        self.connect_backoff_s = connect_backoff_s
        self._sock: Optional[socket.socket] = None

    def _connect(self) -> socket.socket:
        if self.persistent:
            if self._sock is not None:
                return self._sock
            s = socket.create_connection((self.host, self.port), timeout=self.timeout)
            s.settimeout(self.timeout)
            self._sock = s
            return s
        # Non-persistent: pace connects to avoid server refusal under rapid churn
        if self.connect_backoff_s > 0:
            try:
                time.sleep(self.connect_backoff_s)
            except Exception:
                pass
        # Non-persistent: always create a fresh socket
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(self.timeout)
        return s

    def _close(self, sock: Optional[socket.socket] = None) -> None:
        try:
            if self.persistent:
                if self._sock is not None:
                    self._sock.close()
            else:
                if sock is not None:
                    sock.close()
        finally:
            if self.persistent:
                self._sock = None

    def _send(self, payload: bytes) -> Optional[socket.socket]:
        s = self._connect()
        try:
            s.sendall(payload)
        except Exception:
            # reconnect once
            if self.persistent:
                self._close()
                s = self._connect()
                s.sendall(payload)
            else:
                # recreate socket and retry once
                try:
                    self._close(s)
                except Exception:
                    pass
                s = self._connect()
                s.sendall(payload)
        return s if not self.persistent else None

    def _recv_until_nl(self, sock: Optional[socket.socket] = None) -> bytes:
        s = sock if (sock is not None) else self._connect()
        data = b""
        deadline = time.time() + max(self.timeout, 1.0) * 2.5
        while True:
            try:
                chunk = s.recv(4096)
            except Exception as exc:
                # allow a few timeout retries if we're mid-message
                if (isinstance(exc, TimeoutError) or isinstance(exc, socket.timeout)) and time.time() < deadline:
                    continue
                # On read failure, close to reset session for next command
                self._close(s if not self.persistent else None)
                raise
            if not chunk:
                break
            data += chunk
            if data.endswith(b"\n"):
                break
            if time.time() >= deadline:
                # Give up and return what we have; caller will parse/handle
                break
        return data

    def write(self, cmd: str) -> None:
        payload = (cmd + "\n").encode()
        if self.persistent:
            self._send(payload)
        else:
            s = self._send(payload)
            try:
                self._close(s)
            except Exception:
                pass

    def read(self) -> str:
        out = self._recv_until_nl()
        return out.decode().strip() if out else ""

    def query(self, cmd: str) -> str:
        payload = (cmd + "\n").encode()
        if self.persistent:
            self._send(payload)
            return self.read()
        s = self._send(payload)
        try:
            out = self._recv_until_nl(s)
            return out.decode().strip() if out else ""
        finally:
            try:
                self._close(s)
            except Exception:
                pass

    def set_timeout(self, timeout: float) -> None:
        """Adjust socket timeout for subsequent operations."""
        self.timeout = timeout
        if self._sock is not None:
            try:
                self._sock.settimeout(timeout)
            except Exception:
                pass


class USBTMCTransport(Transport):
    """Persistent Linux USBTMC character-device transport.

    Uses a single read/write handle to the `/dev/usbtmcX` device. Implements
    simple retry-once semantics on write failure and resets the handle on
    read failures to recover from stalled sessions.
    """

    def __init__(self, path: str, timeout_s: float = 5.0, inter_query_delay_s: float = 0.02):
        self.path = path
        self.timeout_s = timeout_s
        self.inter_query_delay_s = inter_query_delay_s
        self._f: Optional[object] = None

    def _open(self):
        if self._f is not None:
            return self._f
        # Try to extend kernel usbtmc timeout via sysfs if available
        try:
            base = os.path.basename(self.path)
            sysfs = f"/sys/class/usbtmc/{base}/io_timeout"
            if os.path.exists(sysfs):
                with open(sysfs, "w") as fp:
                    fp.write(str(int(self.timeout_s * 1000)))
            # Enable termination on newline if supported by driver
            term_path = f"/sys/class/usbtmc/{base}/term_char"
            term_en_path = f"/sys/class/usbtmc/{base}/term_char_enabled"
            if os.path.exists(term_path):
                with open(term_path, "w") as fp:
                    fp.write("10")  # '\n'
            if os.path.exists(term_en_path):
                with open(term_en_path, "w") as fp:
                    fp.write("1")
        except Exception:
            pass
        # Open once for read/write in binary mode, unbuffered
        self._f = open(self.path, "r+b", buffering=0)
        return self._f

    def _close(self) -> None:
        try:
            if self._f is not None:
                try:
                    self._f.close()
                finally:
                    self._f = None
        finally:
            self._f = None

    def write(self, cmd: str) -> None:
        payload = (cmd + "\n").encode()
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            f = self._open()
            try:
                f.write(payload)
                return
            except Exception as exc:
                last_exc = exc
                # retry after short delay and reopen
                self._close()
                time.sleep(0.05)
        # if we exhausted retries, re-raise the last error
        assert last_exc is not None
        raise last_exc

    def read(self) -> str:
        f = self._open()
        # Try a couple of times in case the device needs a moment
        last_exc: Optional[Exception] = None
        for _ in range(3):
            try:
                data = f.read(65536)
                if data:
                    return data.decode(errors="ignore").strip()
                # empty read; brief wait then retry
                time.sleep(0.02)
            except Exception as exc:
                last_exc = exc
                # reset on read failure so that subsequent operations can recover
                self._close()
                time.sleep(0.02)
        if last_exc:
            raise last_exc
        return ""

    def query(self, cmd: str) -> str:
        self.write(cmd)
        if self.inter_query_delay_s > 0:
            time.sleep(self.inter_query_delay_s)
        return self.read()


class LoggingTransport(Transport):
    """Transparent logging wrapper around another Transport.

    Writes newline-delimited JSON records to the provided file-like object.
    Records include timestamp seconds, role, op (write/read), remote, and data.
    """

    def __init__(self, inner: Transport, role: str, log_file):
        self.inner = inner
        self.role = role
        self.log_file = log_file
        self.remote = self._detect_remote()

        # Log an open event
        self._log("open", "", {"remote": self.remote})

    def _detect_remote(self) -> str:
        try:
            if isinstance(self.inner, SocketTransport):
                return f"{self.inner.host}:{self.inner.port}"
        except Exception:
            pass
        try:
            if isinstance(self.inner, USBTMCTransport):
                return getattr(self.inner, "path", "usbtmc")
        except Exception:
            pass
        return "unknown"

    def _log(self, op: str, data: str, extra: Optional[dict] = None) -> None:
        try:
            rec = {"ts": time.time(), "role": self.role, "op": op, "remote": self.remote, "data": data}
            if extra:
                rec.update(extra)
            self.log_file.write(json.dumps(rec, separators=(",", ":")) + "\n")
            try:
                self.log_file.flush()
            except Exception:
                pass
        except Exception:
            # Never let logging break I/O
            pass

    # Delegate attribute access for non-Transport APIs (e.g., set_timeout)
    def __getattr__(self, item):
        return getattr(self.inner, item)

    def write(self, cmd: str) -> None:
        self._log("write", cmd)
        return self.inner.write(cmd)

    def read(self) -> str:
        resp = self.inner.read()
        self._log("read", resp)
        return resp
