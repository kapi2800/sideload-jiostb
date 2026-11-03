#!/usr/bin/env python3
"""
doctor.py — Comprehensive diagnostics and verification for the JioSTB sideload chain.

Modes:
  --quick       Ports + NBI + DNS check only (safe any time, zero side effects)
  (default)     Full chain checklist + device status
  --watch       Polls _lastInform, task queue, and ADB port 5555 until device connects
  --simulate    Sends a synthetic CWMP Inform through pass_front -> GenieACS,
                creates and cleans up a test device to prove the PC side works 100%
"""

import sys
import os
import socket
import ssl
import json
import time
import urllib.request
import urllib.error
import urllib.parse
import argparse

# Terminal colors
CYAN = '\033[0;36m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
RED = '\033[0;31m'
NC = '\033[0m'
BOLD = '\033[1m'

DEFAULT_NBI = os.environ.get("NBI_URL", "http://127.0.0.1:7557")
DEFAULT_CWMP = os.environ.get("CWMP_URL", "http://127.0.0.1:7547")
DEFAULT_TLS_PORT = int(os.environ.get("TLS_PORT", 8443))

def port_open(host, port, timeout=0.8):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False

def get_lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    return sorted(list(ips)) or ["127.0.0.1"]

def check_dns(domain="acs.oss.jio.com"):
    try:
        return socket.gethostbyname(domain)
    except Exception:
        return None

def query_nbi(endpoint, nbi_url=DEFAULT_NBI, method="GET", data=None):
    url = f"{nbi_url.rstrip('/')}/{endpoint.lstrip('/')}"
    req = urllib.request.Request(url, method=method)
    if data is not None:
        if isinstance(data, dict) or isinstance(data, list):
            req.data = json.dumps(data).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        elif isinstance(data, (bytes, str)):
            req.data = data.encode("utf-8") if isinstance(data, str) else data
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)

def run_quick(nbi_url=DEFAULT_NBI, tls_port=DEFAULT_TLS_PORT):
    print(f"\n{BOLD}Running Quick Chain Health Check...{NC}")
    ok = True

    # 1. pass_front.py
    pf_open = port_open("127.0.0.1", tls_port)
    if pf_open:
        print(f"  {GREEN}[PASS]{NC} pass_front.py listening on port {tls_port}")
    else:
        print(f"  {RED}[FAIL]{NC} pass_front.py is NOT listening on port {tls_port}")
        ok = False

    # 2. GenieACS CWMP (:7547)
    cwmp_open = port_open("127.0.0.1", 7547)
    if cwmp_open:
        print(f"  {GREEN}[PASS]{NC} GenieACS CWMP listening on port 7547")
    else:
        print(f"  {RED}[FAIL]{NC} GenieACS CWMP is NOT listening on port 7547 (Docker container running?)")
        ok = False

    # 3. GenieACS NBI (:7557)
    status, body = query_nbi("devices/?projection=_id", nbi_url=nbi_url)
    if status == 200:
        print(f"  {GREEN}[PASS]{NC} GenieACS NBI reachable at {nbi_url}")
    else:
        print(f"  {RED}[FAIL]{NC} GenieACS NBI unreachable at {nbi_url} (HTTP {status})")
        ok = False

    # 4. DNS Override for acs.oss.jio.com
    dns_ip = check_dns("acs.oss.jio.com")
    lan_ips = get_lan_ips()
    if dns_ip:
        if dns_ip in lan_ips or dns_ip == "127.0.0.1":
            print(f"  {GREEN}[PASS]{NC} DNS acs.oss.jio.com -> {dns_ip} (Matches host LAN IP)")
        else:
            print(f"  {YELLOW}[WARN]{NC} DNS acs.oss.jio.com -> {dns_ip} (Expected host LAN IP: {', '.join(lan_ips)})")
    else:
        print(f"  {RED}[FAIL]{NC} DNS acs.oss.jio.com could not be resolved! Check Technitium DNS / hosts.")
        ok = False

    return ok

def run_full(nbi_url=DEFAULT_NBI, tls_port=DEFAULT_TLS_PORT, stb_ip=None):
    quick_ok = run_quick(nbi_url=nbi_url, tls_port=tls_port)

    print(f"\n{BOLD}Device & Task Status:{NC}")
    status, body = query_nbi("devices/?projection=_id,_lastInform", nbi_url=nbi_url)
    devices = []
    if status == 200:
        try:
            devices = json.loads(body)
        except Exception:
            devices = []

    print(f"  Total devices in GenieACS: {len(devices)}")
    for d in devices:
        did = d.get("_id", "unknown")
        last_inf = d.get("_lastInform", "never")
        print(f"    • Device: {CYAN}{did}{NC} | Last Inform: {last_inf}")
        # Query tasks
        t_status, t_body = query_nbi(f"tasks/?query={urllib.parse.quote(json.dumps({'device': did}))}", nbi_url=nbi_url)
        if t_status == 200:
            try:
                tasks = json.loads(t_body)
                print(f"      Tasks in queue: {len(tasks)}")
                for t in tasks:
                    t_name = t.get("name", "task")
                    fault = t.get("fault")
                    f_str = f" {RED}(Fault: {fault}){NC}" if fault else ""
                    print(f"        - {t_name}{f_str}")
            except Exception:
                pass

    if stb_ip:
        adb_port = 5555
        stb_adb = port_open(stb_ip, adb_port, timeout=2.0)
        if stb_adb:
            print(f"  {GREEN}[PASS]{NC} STB ({stb_ip}:{adb_port}) ADB port is OPEN!")
        else:
            print(f"  {YELLOW}[WAIT]{NC} STB ({stb_ip}:{adb_port}) ADB port is closed or not reachable yet.")

    return quick_ok

def run_watch(nbi_url=DEFAULT_NBI, stb_ip=None):
    print(f"\n{BOLD}{CYAN}Watching for STB connection (Press Ctrl+C to stop)...{NC}")
    last_seen = {}
    while True:
        try:
            status, body = query_nbi("devices/?projection=_id,_lastInform", nbi_url=nbi_url)
            if status == 200:
                devices = json.loads(body)
                for d in devices:
                    did = d.get("_id")
                    linf = d.get("_lastInform")
                    if did not in last_seen or last_seen[did] != linf:
                        last_seen[did] = linf
                        print(f"\n{GREEN}★ [{time.strftime('%H:%M:%S')}] Inform received from device: {did}{NC}")
                        print(f"   Timestamp: {linf}")
                        # Check task queue
                        t_status, t_body = query_nbi(f"tasks/?query={urllib.parse.quote(json.dumps({'device': did}))}", nbi_url=nbi_url)
                        if t_status == 200:
                            tasks = json.loads(t_body)
                            print(f"   Pending tasks: {len(tasks)}")
            if stb_ip and port_open(stb_ip, 5555, timeout=1.0):
                print(f"\n{GREEN}★ ADB port 5555 is OPEN on STB {stb_ip}! You can now run 'adb connect {stb_ip}:5555'{NC}")
            sys.stdout.write(".")
            sys.stdout.flush()
            time.sleep(3)
        except KeyboardInterrupt:
            print("\nWatch stopped.")
            break
        except Exception as e:
            time.sleep(3)

def run_simulate(tls_port=DEFAULT_TLS_PORT, nbi_url=DEFAULT_NBI):
    print(f"\n{BOLD}Simulating Full CWMP Chain (Synthetic Inform)...{NC}")
    test_sn = "SIMULATED-9999"
    device_id = f"000000-TEST-JioSTB-{test_sn}"

    # SOAP Inform payload, shaped like a REAL first-boot CPE inform: 0 BOOTSTRAP
    # + 1 BOOT events, the full Device.DeviceInfo identity block, and explicitly
    # xsi:type'd values. A bare 1-BOOT inform with one untyped parameter gets an
    # InformResponse (so steps 1-2 pass) but GenieACS 1.2 never commits it to
    # MongoDB -- the simulated device silently never exists.
    soap_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:soap-enc="http://schemas.xmlsoap.org/soap/encoding/"
               xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:cwmp="urn:dslforum-org:cwmp-1-0">
  <soap:Header>
    <cwmp:ID soap:mustUnderstand="1">1001</cwmp:ID>
  </soap:Header>
  <soap:Body>
    <cwmp:Inform>
      <DeviceId>
        <Manufacturer>TEST</Manufacturer>
        <OUI>000000</OUI>
        <ProductClass>TEST-JioSTB</ProductClass>
        <SerialNumber>{test_sn}</SerialNumber>
      </DeviceId>
      <Event soap-enc:arrayType="cwmp:EventStruct[2]">
        <EventStruct>
          <EventCode>0 BOOTSTRAP</EventCode>
          <CommandKey></CommandKey>
        </EventStruct>
        <EventStruct>
          <EventCode>1 BOOT</EventCode>
          <CommandKey></CommandKey>
        </EventStruct>
      </Event>
      <MaxEnvelopes>1</MaxEnvelopes>
      <CurrentTime>{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}</CurrentTime>
      <RetryCount>0</RetryCount>
      <ParameterList soap-enc:arrayType="cwmp:ParameterValueStruct[8]">
        <ParameterValueStruct>
          <Name>Device.DeviceInfo.Manufacturer</Name>
          <Value xsi:type="xsd:string">TEST</Value>
        </ParameterValueStruct>
        <ParameterValueStruct>
          <Name>Device.DeviceInfo.ManufacturerOUI</Name>
          <Value xsi:type="xsd:string">000000</Value>
        </ParameterValueStruct>
        <ParameterValueStruct>
          <Name>Device.DeviceInfo.ProductClass</Name>
          <Value xsi:type="xsd:string">TEST-JioSTB</Value>
        </ParameterValueStruct>
        <ParameterValueStruct>
          <Name>Device.DeviceInfo.SerialNumber</Name>
          <Value xsi:type="xsd:string">{test_sn}</Value>
        </ParameterValueStruct>
        <ParameterValueStruct>
          <Name>Device.DeviceInfo.ModelName</Name>
          <Value xsi:type="xsd:string">JHSD200-TEST</Value>
        </ParameterValueStruct>
        <ParameterValueStruct>
          <Name>Device.DeviceInfo.SpecVersion</Name>
          <Value xsi:type="xsd:string">1.4</Value>
        </ParameterValueStruct>
        <ParameterValueStruct>
          <Name>Device.DeviceInfo.SoftwareVersion</Name>
          <Value xsi:type="xsd:string">11.3.24</Value>
        </ParameterValueStruct>
        <ParameterValueStruct>
          <Name>Device.ManagementServer.ParameterKey</Name>
          <Value xsi:type="xsd:string"></Value>
        </ParameterValueStruct>
      </ParameterList>
    </cwmp:Inform>
  </soap:Body>
</soap:Envelope>"""

    # We post to pass_front.py via HTTPS
    # Create SSL context that accepts custom cert
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    url = f"https://127.0.0.1:{tls_port}/"
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": ""
    }

    print(f"  [1/4] Sending synthetic Inform over TLS to {url} ...")
    session_cookie = None
    try:
        req = urllib.request.Request(url, data=soap_body.encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            # Extract set-cookie
            for k, v in resp.getheaders():
                if k.lower() == "set-cookie" and "session=" in v:
                    session_cookie = v.split(";")[0]
            if "InformResponse" in resp_body:
                print(f"  {GREEN}[PASS]{NC} Inform accepted by GenieACS! (Session cookie: {session_cookie or 'none'})")
            else:
                print(f"  {YELLOW}[WARN]{NC} Upstream response received but no InformResponse found.")
    except Exception as e:
        print(f"  {RED}[FAIL]{NC} Could not connect to TLS front on port {tls_port}: {e}")
        return False

    # Send empty POST to complete session
    print(f"  [2/4] Sending empty POST with session cookie to close CWMP session...")
    try:
        empty_headers = {}
        if session_cookie:
            empty_headers["Cookie"] = session_cookie
        req2 = urllib.request.Request(url, data=b"", headers=empty_headers, method="POST")
        with urllib.request.urlopen(req2, context=ctx, timeout=8) as resp2:
            print(f"  {GREEN}[PASS]{NC} CWMP session completed cleanly (HTTP {resp2.status})")
    except Exception as e:
        print(f"  {YELLOW}[WARN]{NC} Empty POST notice: {e}")

    # Verify device exists in NBI. GenieACS 1.2 has no GET /devices/{id} route
    # (that path returns 405), and this docker build percent-encodes hyphens in
    # stored device ids ("TEST%2DJioSTB"), so an exact-equality query on the
    # clean id never matches. Fetch the list and compare normalised ids.
    print(f"  [3/4] Checking device presence in GenieACS NBI...")
    found_id = None
    for attempt in range(30):
        status, body = query_nbi("devices/?projection=_id,_lastInform", nbi_url=nbi_url)
        if status == 200:
            try:
                for d in json.loads(body):
                    if str(d.get("_id", "")).replace("%2D", "-") == device_id:
                        found_id = d.get("_id")
                        break
            except ValueError:
                pass
        if found_id:
            break
        if attempt and attempt % 5 == 0:
            print(f"        ... still waiting for GenieACS to flush the device ({attempt * 2}s)")
        time.sleep(2)
    if found_id:
        print(f"  {GREEN}[PASS]{NC} Simulated device confirmed in GenieACS database! (stored id: {found_id})")
    else:
        print(f"  {RED}[FAIL]{NC} Simulated device not found in GenieACS NBI (last HTTP {status})")
        # Self-diagnose: dump everything the ACS actually stored, so we can tell
        # "database empty" (inform silently dropped) from "stored under a
        # different _id" (id-format mismatch), and point at the CWMP log.
        d_status, d_body = query_nbi("devices/?projection=_id,_lastInform", nbi_url=nbi_url)
        if d_status == 200:
            try:
                devices = json.loads(d_body)
                if devices:
                    print(f"        Devices currently in GenieACS:")
                    for d in devices:
                        print(f"          - {d.get('_id', '?')}  (last inform: {d.get('_lastInform', '?')})")
                else:
                    print(f"        GenieACS database is EMPTY: the Inform is answered with 200/")
                    print(f"        InformResponse but never stored. Check the CWMP server log:")
                    print(f"            docker logs genieacs --tail 50")
            except ValueError:
                print(f"        Could not parse the device list (HTTP {d_status}).")
        return False

    # Clean up device (DELETE /devices/{id} is a real NBI route, unlike GET).
    # The stored id may contain literal "%2D"; percent-encode it again ("%252D")
    # so a URL-decoding router maps it back to the stored form, and also try the
    # raw spelling before giving up.
    print(f"  [4/4] Cleaning up simulated device from database...")
    del_status = 0
    for enc_id in (urllib.parse.quote(str(found_id), safe=""), str(found_id)):
        del_status, _ = query_nbi(f"devices/{enc_id}", nbi_url=nbi_url, method="DELETE")
        if del_status in (200, 204):
            break
    if del_status in (200, 204):
        print(f"  {GREEN}[PASS]{NC} Simulated device cleanly deleted from GenieACS.")
    else:
        print(f"  {YELLOW}[WARN]{NC} Cleanup status: HTTP {del_status} (delete it from the GenieACS UI at :3000 if it lingers)")

    print(f"\n{GREEN}{BOLD}Result: Full PC-side chain (TLS -> cookie proxy -> GenieACS CWMP -> NBI) is 100% OPERATIONAL!{NC}\n")
    return True

def main():
    parser = argparse.ArgumentParser(description="JioSTB Sideload Diagnostic & Chain Health Doctor")
    parser.add_argument("--quick", action="store_true", help="Quick check (ports, NBI, DNS) without side-effects")
    parser.add_argument("--simulate", action="store_true", help="Prove full PC-side chain with synthetic Inform")
    parser.add_argument("--watch", action="store_true", help="Continuously poll for device Inform and ADB opening")
    parser.add_argument("--nbi", default=DEFAULT_NBI, help=f"GenieACS NBI URL (default: {DEFAULT_NBI})")
    parser.add_argument("--tls-port", type=int, default=DEFAULT_TLS_PORT, help=f"TLS front port (default: {DEFAULT_TLS_PORT})")
    parser.add_argument("--stb-ip", default=None, help="STB LAN IP address for connectivity check")

    args = parser.parse_args()

    if args.quick:
        ok = run_quick(nbi_url=args.nbi, tls_port=args.tls_port)
        sys.exit(0 if ok else 1)
    elif args.simulate:
        ok = run_simulate(tls_port=args.tls_port, nbi_url=args.nbi)
        sys.exit(0 if ok else 1)
    elif args.watch:
        run_watch(nbi_url=args.nbi, stb_ip=args.stb_ip)
    else:
        ok = run_full(nbi_url=args.nbi, tls_port=args.tls_port, stb_ip=args.stb_ip)
        sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
