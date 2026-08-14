"""
Latency Atlas — a live, crowdsourced network-latency radar.

Every browser tab that opens a WebSocket connection to this server becomes a
"probe". The server pings each probe on a fixed interval, times the reply,
geolocates the probe's IP, and broadcasts the full swarm state (position +
latency) to every connected client. Each client projects that state onto a
radar-style view using a real azimuthal-equidistant projection centered on
the server's own location, so distance from center is real great-circle
distance and angle is real compass bearing.

Run locally:
    pip install -r requirements.txt
    uvicorn main:app --reload

Then open http://127.0.0.1:8000 in a few different browser tabs (or on your
phone over the same network) and watch the radar populate.
"""

import asyncio
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI(title="Latency Atlas")

PING_INTERVAL_SECONDS = 1.5
GEOLOCATION_TIMEOUT_SECONDS = 3.0

# Used whenever we can't resolve a real location (localhost / private IPs,
# or a failed lookup) so the demo still has something to show on the radar.
FALLBACK_CITIES = [
    {"city": "New Delhi", "country": "India", "lat": 28.6139, "lon": 77.2090},
    {"city": "New York", "country": "USA", "lat": 40.7128, "lon": -74.0060},
    {"city": "London", "country": "UK", "lat": 51.5072, "lon": -0.1276},
    {"city": "Tokyo", "country": "Japan", "lat": 35.6762, "lon": 139.6503},
    {"city": "Sao Paulo", "country": "Brazil", "lat": -23.5505, "lon": -46.6333},
    {"city": "Sydney", "country": "Australia", "lat": -33.8688, "lon": 151.2093},
    {"city": "Lagos", "country": "Nigeria", "lat": 6.5244, "lon": 3.3792},
    {"city": "Cape Town", "country": "South Africa", "lat": -33.9249, "lon": 18.4241},
]

# The radar's origin point. Set at startup by locating the server's own
# public IP; falls back to a fixed point if that lookup fails or the server
# is running fully offline.
hub = {"city": "Origin", "country": "", "lat": 0.0, "lon": 0.0, "resolved": False}


@dataclass
class Client:
    id: str
    ws: WebSocket
    city: str
    country: str
    lat: float
    lon: float
    latency_ms: float = 0.0
    connected_at: float = field(default_factory=time.time)


clients: Dict[str, Client] = {}


def is_private_ip(ip: str) -> bool:
    return (
        ip.startswith(("127.", "10.", "192.168.", "::1", "0."))
        or ip in ("localhost", "testclient")
        or ip.startswith("172.")  # covers the 172.16.0.0/12 private range closely enough for a demo
    )


async def locate_ip(ip: Optional[str]) -> dict:
    """Best-effort IP geolocation with a graceful, demo-friendly fallback."""
    if not ip or is_private_ip(ip):
        base = random.choice(FALLBACK_CITIES)
        return {
            "city": base["city"],
            "country": base["country"],
            "lat": base["lat"] + random.uniform(-3, 3),
            "lon": base["lon"] + random.uniform(-3, 3),
        }
    try:
        async with httpx.AsyncClient(timeout=GEOLOCATION_TIMEOUT_SECONDS) as http:
            resp = await http.get(f"https://ipapi.co/{ip}/json/")
            data = resp.json()
            if data.get("latitude") is not None and data.get("longitude") is not None:
                return {
                    "city": data.get("city") or "Unknown",
                    "country": data.get("country_name") or "Unknown",
                    "lat": float(data["latitude"]),
                    "lon": float(data["longitude"]),
                }
    except Exception:
        pass
    base = random.choice(FALLBACK_CITIES)
    return {"city": base["city"], "country": base["country"], "lat": base["lat"], "lon": base["lon"]}


def client_ip(websocket: WebSocket) -> Optional[str]:
    forwarded = websocket.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return websocket.client.host if websocket.client else None


def state_payload() -> dict:
    return {
        "type": "state",
        "hub": hub,
        "clients": [
            {
                "id": c.id,
                "city": c.city,
                "country": c.country,
                "lat": c.lat,
                "lon": c.lon,
                "latency_ms": round(c.latency_ms, 1),
            }
            for c in clients.values()
        ],
    }


async def broadcast_state() -> None:
    payload = json.dumps(state_payload())
    dead = []
    for cid, c in clients.items():
        try:
            await c.ws.send_text(payload)
        except Exception:
            dead.append(cid)
    for cid in dead:
        clients.pop(cid, None)


@app.on_event("startup")
async def resolve_hub_location() -> None:
    try:
        async with httpx.AsyncClient(timeout=GEOLOCATION_TIMEOUT_SECONDS) as http:
            resp = await http.get("https://ipapi.co/json/")
            data = resp.json()
            if data.get("latitude") is not None:
                hub.update(
                    city=data.get("city") or "Origin",
                    country=data.get("country_name") or "",
                    lat=float(data["latitude"]),
                    lon=float(data["longitude"]),
                    resolved=True,
                )
    except Exception:
        pass  # keep the (0, 0) fallback origin


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    ip = client_ip(websocket)
    loc = await locate_ip(ip)
    cid = str(uuid.uuid4())[:8]
    client = Client(id=cid, ws=websocket, **loc)
    clients[cid] = client

    await websocket.send_text(json.dumps({"type": "welcome", "id": cid, "hub": hub}))
    await broadcast_state()

    async def ping_loop() -> None:
        while True:
            try:
                await websocket.send_text(json.dumps({"type": "ping", "ts": time.time()}))
                await asyncio.sleep(PING_INTERVAL_SECONDS)
            except Exception:
                break

    pinger = asyncio.create_task(ping_loop())

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            if msg.get("type") == "pong":
                sent_ts = msg.get("ts")
                if isinstance(sent_ts, (int, float)):
                    client.latency_ms = max(0.0, (time.time() - sent_ts) * 1000)
                    await broadcast_state()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        pinger.cancel()
        clients.pop(cid, None)
        await broadcast_state()


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse("static/index.html")
