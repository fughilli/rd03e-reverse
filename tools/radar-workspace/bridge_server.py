#!/usr/bin/env python3
"""Rd-03E radar workspace bridge — runs in the claude-container.

- Stages `swd_agent.py` onto rig-radar and spawns it inside a HITL reservation
  over tailscale-ssh (the reservation is held for the agent's lifetime).
- Reads the agent's newline-JSON stream and broadcasts it to browser WebSocket
  clients; relays browser commands back to the agent's stdin.
- Serves the static web workspace (web/) on the same port (single origin).

Exposed to the user via the claude-container router as a named service
(see .claude-container-overlay/overlay.json -> services.radar).

Stdlib only (no pip deps): a tiny asyncio HTTP + WebSocket server.
"""
import asyncio
import base64
import hashlib
import json
import os
import sys
import mimetypes

RIG = os.environ.get("RADAR_RIG", "100.88.205.48")
SOCK = os.environ.get("TS_SOCK", "/var/run/tailscale/tailscaled.sock")
TAILSCALE = os.environ.get("TAILSCALE_BIN", "/usr/local/bin/tailscale")
# `tailscale ssh` execs the system `ssh`; the container's nix openssh isn't on
# the default PATH, so make it reachable for our subprocesses.
OPENSSH_BIN = os.environ.get(
    "OPENSSH_BIN",
    "/nix/store/zrxjw1m5w2x689znfkpnkpji2r015cas-openssh-10.0p2/bin")
if OPENSSH_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = OPENSSH_BIN + ":" + os.environ.get("PATH", "")
RIG_PY = "/nix/store/y5xpacpm5057gh8nm8s7n935zq21faky-python3-3.12.12/bin/python3.12"
RIG_HITL = "/nix/store/245p0gci0bqblfkanw5x476pha515vw5-hitl-reserve-0.1.0/bin/hitl"
AGENT_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "swd_agent.py")
AGENT_STAGE = "/run/hitl-flash/swd_agent.py"
AGENT_REMOTE = "/run/hitl/flash-lock/swd_agent.py"
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PORT = int(os.environ.get("RADAR_PORT", "8091"))
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

clients = set()          # set of asyncio.StreamWriter for connected WS clients
agent_stdin = None       # current agent process stdin (StreamWriter) or None
last_ready = None        # cache the most recent {"t":"ready"} for new clients
last_status = None
# Sticky agent mode: the SWD link periodically hangs and the agent is respawned
# as a fresh process in IDLE mode, silently dropping whatever continuous-tracking
# mode the user had engaged. We persist the user's last mode/decim choice here and
# replay it into each fresh agent the instant it reports "ready", so HW-hop (or
# fast-track) tracking resumes transparently across a respawn — with no browser
# connected and no client-side round-trip. A new/reconnecting browser is also
# primed with this state so its UI reflects what the agent is actually doing.
sticky_mode = None       # latest engaged {"t":"hop"|"fasttrack", "on":True, ...} or None
sticky_decim = None      # latest {"t":"decim","n":..} or None


def modestate_msg():
    """A synthetic message that tells a browser which continuous mode is live."""
    m = sticky_mode or {}
    return {"t": "modestate",
            "mode": m.get("t") if m.get("on") else None,
            "bin": m.get("bin"),
            "decim": (sticky_decim or {}).get("n")}


def note_sticky(cmd):
    """Update sticky mode/decim from a browser command (idempotent)."""
    global sticky_mode, sticky_decim
    if not isinstance(cmd, dict):
        return
    ct = cmd.get("t")
    if ct in ("hop", "fasttrack"):
        sticky_mode = cmd if cmd.get("on") else None   # off clears the mode
    elif ct == "decim":
        sticky_decim = cmd


def ssh_argv(remote_cmd):
    return [TAILSCALE, "--socket=" + SOCK, "ssh", "root@" + RIG, remote_cmd]


async def stage_agent():
    with open(AGENT_LOCAL, "rb") as f:
        data = f.read()
    proc = await asyncio.create_subprocess_exec(
        *ssh_argv("cat > " + AGENT_STAGE),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate(data)
    if proc.returncode != 0:
        tail = b"\n".join(l for l in (err or b"").splitlines()
                          if b"kex" not in l and b"ML-KEM" not in l
                          and b"post-quantum" not in l)[-400:]
        sys.stderr.write("stage rc=%s err=%r\n" % (proc.returncode, tail)); sys.stderr.flush()
    return proc.returncode == 0


# ---------------------------------------------------------------- WebSocket ---
def ws_frame(payload: bytes, opcode=0x1) -> bytes:
    """Server->client frame (unmasked)."""
    b = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        b.append(n)
    elif n < 65536:
        b.append(126)
        b += n.to_bytes(2, "big")
    else:
        b.append(127)
        b += n.to_bytes(8, "big")
    b += payload
    return bytes(b)


async def ws_send(writer, obj):
    try:
        writer.write(ws_frame(json.dumps(obj, separators=(",", ":")).encode()))
        await writer.drain()
    except Exception:
        clients.discard(writer)


async def broadcast(obj):
    frame = ws_frame(json.dumps(obj, separators=(",", ":")).encode())
    dead = []
    for w in list(clients):
        try:
            w.write(frame)
            await w.drain()
        except Exception:
            dead.append(w)
    for w in dead:
        clients.discard(w)


async def ws_read_frame(reader):
    """Read one client->server frame; return (opcode, payload) or (None, None)."""
    hdr = await reader.readexactly(2)
    opcode = hdr[0] & 0x0F
    masked = hdr[1] & 0x80
    ln = hdr[1] & 0x7F
    if ln == 126:
        ln = int.from_bytes(await reader.readexactly(2), "big")
    elif ln == 127:
        ln = int.from_bytes(await reader.readexactly(8), "big")
    mask = await reader.readexactly(4) if masked else b"\x00\x00\x00\x00"
    data = bytearray(await reader.readexactly(ln))
    for i in range(ln):
        data[i] ^= mask[i & 3]
    return opcode, bytes(data)


async def handle_ws(reader, writer, key):
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    writer.write((
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        "Sec-WebSocket-Accept: " + accept + "\r\n\r\n"
    ).encode())
    await writer.drain()
    clients.add(writer)
    # Prime the new client with cached state.
    if last_ready:
        await ws_send(writer, last_ready)
    if last_status:
        await ws_send(writer, last_status)
    await ws_send(writer, modestate_msg())   # sync UI to the live tracking mode
    try:
        while True:
            opcode, data = await ws_read_frame(reader)
            if opcode == 0x8:      # close
                break
            if opcode == 0x9:      # ping -> pong
                writer.write(ws_frame(data, 0xA))
                await writer.drain()
                continue
            if opcode in (0x1, 0x2) and data:
                # browser -> agent command. Remember sticky mode/decim so it can be
                # replayed into a fresh agent after a respawn.
                try:
                    note_sticky(json.loads(data))
                except Exception:
                    pass
                if agent_stdin is not None:
                    try:
                        agent_stdin.write(data + b"\n")
                        await agent_stdin.drain()
                    except Exception:
                        pass
    except Exception:
        pass
    finally:
        clients.discard(writer)
        try:
            writer.close()
        except Exception:
            pass


# --------------------------------------------------------------- HTTP static -
async def serve_static(reader, writer, path):
    if path == "/" or not path:
        path = "/index.html"
    path = path.split("?", 1)[0]
    safe = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
    if not safe.startswith(WEB_DIR) or not os.path.isfile(safe):
        body = b"not found"
        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: %d\r\n\r\n%s"
                     % (len(body), body))
        await writer.drain()
        writer.close()
        return
    ctype = mimetypes.guess_type(safe)[0] or "application/octet-stream"
    with open(safe, "rb") as f:
        body = f.read()
    writer.write((
        "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
        "Cache-Control: no-cache\r\nConnection: close\r\n\r\n" % (ctype, len(body))
    ).encode() + body)
    await writer.drain()
    writer.close()


async def handle_conn(reader, writer):
    try:
        req_line = await reader.readline()
        if not req_line:
            writer.close()
            return
        parts = req_line.decode("latin1").split()
        path = parts[1] if len(parts) > 1 else "/"
        headers = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            k, _, v = line.decode("latin1").partition(":")
            headers[k.strip().lower()] = v.strip()
        if headers.get("upgrade", "").lower() == "websocket":
            await handle_ws(reader, writer, headers.get("sec-websocket-key", ""))
        else:
            await serve_static(reader, writer, path)
    except Exception:
        try:
            writer.close()
        except Exception:
            pass


# --------------------------------------------------------------- agent pump --
async def agent_pump():
    global agent_stdin, last_ready, last_status
    while True:
        try:
            ok = await stage_agent()
        except Exception as e:
            sys.stderr.write("stage exception: %r\n" % e); sys.stderr.flush()
            ok = False
        sys.stderr.write("stage ok=%s\n" % ok); sys.stderr.flush()
        if not ok:
            await broadcast({"t": "bridge", "state": "stage_failed"})
            await asyncio.sleep(3)
            continue
        remote = "%s reserve --unit radar-bench --host http://127.0.0.1:8087 --actor agent -- %s %s" % (
            RIG_HITL, RIG_PY, AGENT_REMOTE)
        errlog = open("/workspace/.claude/radar-workspace/agent.err", "ab")
        sys.stderr.write("spawning agent: %s\n" % remote); sys.stderr.flush()
        proc = await asyncio.create_subprocess_exec(
            *ssh_argv(remote),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=errlog,
            limit=1 << 20,
        )
        agent_stdin = proc.stdin
        await broadcast({"t": "bridge", "state": "agent_up"})
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line or line[:1] != b"{":
                    continue  # skip reservation banner / non-JSON
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                t = obj.get("t")
                if t == "ready":
                    last_ready = obj
                    # Re-engage the user's selected tracking mode on this fresh
                    # agent so a respawn doesn't silently drop it. decim first
                    # (cheap state), then the mode toggle. Fire-and-forget: if the
                    # firmware is hop-incapable the agent answers with ftstatus.
                    for c in (sticky_decim, sticky_mode):
                        if c is not None:
                            try:
                                proc.stdin.write((json.dumps(c) + "\n").encode())
                                await proc.stdin.drain()
                            except Exception:
                                pass
                    await broadcast(modestate_msg())   # resync UIs post-respawn
                elif t == "status":
                    last_status = obj
                await broadcast(obj)
        except Exception:
            pass
        agent_stdin = None
        rc = proc.returncode
        sys.stderr.write("agent exited rc=%s\n" % rc); sys.stderr.flush()
        await broadcast({"t": "bridge", "state": "agent_down"})
        try:
            proc.kill()
        except Exception:
            pass
        await asyncio.sleep(2)


async def main():
    mimetypes.add_type("application/javascript", ".js")
    server = await asyncio.start_server(handle_conn, "0.0.0.0", PORT)
    sys.stderr.write("radar bridge on :%d, web=%s\n" % (PORT, WEB_DIR))
    sys.stderr.flush()
    pump = asyncio.create_task(agent_pump())  # keep a reference (avoid GC)

    def _done(t):
        try:
            exc = t.exception()
        except Exception:
            exc = None
        sys.stderr.write("agent_pump task ended exc=%r\n" % exc); sys.stderr.flush()
    pump.add_done_callback(_done)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
