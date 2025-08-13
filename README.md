# SCPI_Bench

- Drivers: Siglent SPD3303X‑E (PSU), SDM3045X (DMM), AMS ENS210 (temp/RH via USB‑I²C)
- Features: safety (vmax, vmin_abort, max_hours, optional negative‑ΔV), USB autodetect, CSV/Parquet logging, optional scope waveform capture (SVG with embedded raw data)
- Transports: TCP socket (IP:port) and USBTMC devices (`/dev/usbtmc*`)

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## CLI usage

```bash
python cli.py run <plan.yaml> [--spd HOST[:PORT]|/dev/usbtmcX] [--sdm HOST[:PORT]|/dev/usbtmcX] \
                        [--scope HOST[:PORT]|/dev/usbtmcX] [--ens210 /dev/ttyACM*] \
                        --out <results.csv|results.parquet>
```

- **--spd / --sdm / --scope**: Specify targets for PSU, DMM, and scope. Accepts `HOST[:PORT]` or a `/dev/usbtmc*` device.
- **--ens210**: Serial device for AMS USB‑I²C dongle (e.g. `/dev/ttyACM0`). Optional.
- **--out**:
  - If ends with `.csv`, writes CSV with columns: `t_s,v_set,i_set,v_meas,i_meas,scope_vpp,scope_vrms,temp_c,humidity_pct,ens_ok`.
  - If ends with `.parquet`/`.pq`, writes Parquet with equivalent fields.

## Examples

### Quick start (mock devices)
```bash
python tests/mock_scpi_server.py --spd 127.0.0.1:15025 --sdm 127.0.0.1:15026
```
```bash
python cli.py run examples/siglent_psu_dmm.yaml \
  --spd 127.0.0.1:15025 --sdm 127.0.0.1:15026 --out results.csv
```

### Scan USB (/dev/usbtmc*)
```
python cli.py scan
```

### LAN instruments
```bash
python cli.py run examples/siglent_psu_dmm.yaml \
  --spd 192.168.1.50:5025 --sdm 192.168.1.51:5025 --out results.parquet
```

### ENS210 only logging
```bash
python cli.py ens --ens210 /dev/ttyACM0 --count 5 --delay 1.0
```

### Battery rehab (with safety/neg‑ΔV)
```bash
python cli.py run examples/nimh_rehab.yaml --spd /dev/usbtmc0 --sdm /dev/usbtmc1 \
  --ens210 /dev/ttyACM0 --out nimh_rehab.csv --debug-log debug.log
```

### TBD: Scope
```bash
python cli.py run examples/scope_capture.yaml \
  --scope /dev/usbtmc2 --ens210 /dev/ttyACM0 --out results.csv
```

## TBD: Scope waveform exports
- When a plan step includes a `scope:` section and a scope is connected, the runner captures one acquisition per step (after an optional delay) and computes Vpp/Vrms.
- SVG files with embedded raw data are saved alongside results: `capture_step<idx>_<channel>.svg`.
- Recover embedded waveforms as CSV:

```bash
python tools/extract_from_svg.py capture_step1_C1.svg > recovered.csv
```

## Example plans
- `examples/siglent_psu_dmm.yaml` – basic PSU/DMM sweep
- `examples/scope_capture.yaml` – adds scope capture parameters
- `examples/ens210_only.yaml` – log ENS210 temp/RH only
- `examples/nimh_rehab.yaml` – long‑running charge plan with safety and negative‑ΔV termination

## Notes
- Safety guardrails can be set in YAML under `safety:` (see `nimh_rehab.yaml`).
