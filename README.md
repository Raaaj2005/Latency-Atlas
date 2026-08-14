# Latency Atlas

A live, crowdsourced network-latency radar. Every browser tab that opens a
WebSocket connection becomes a "probe" — the server pings it continuously,
geolocates it by IP, and broadcasts the full swarm's position and latency to
every other connected client in real time. The result is a shared, rotating
radar scope that fills up with whoever is currently looking at the page.

WebSockets aren't a feature bolted onto this project — they're the whole
mechanism. There is no polling, no REST endpoint for state, no page refresh.
A client's only channel to the server is a persistent WebSocket carrying a
continuous ping/pong stream in one direction and broadcast state updates in
the other.

## How it works

1. A browser tab opens a WebSocket connection to the FastAPI server.
2. The server resolves the client's approximate location from its IP
   (falling back to a randomized demo city for localhost/private IPs, so it
   still looks good when you test with a few tabs on one machine).
3. The server pings that client every 1.5s over the open socket; the client
   replies immediately with a pong, and the server times the round trip.
4. On every latency update, the server broadcasts the full swarm state
   (every connected client's city, coordinates, and current latency) to
   **all** connected clients.
5. Each client projects that shared state onto a radar view using a real
   [azimuthal-equidistant projection](https://en.wikipedia.org/wiki/Azimuthal_equidistant_projection)
   centered on the server's own location — so distance from center is real
   great-circle distance (haversine), and angle from center is real compass
   bearing. It's not a stylized map; the geometry is genuine.

## Stack

- **Backend:** FastAPI + native WebSockets (`fastapi.WebSocket`), `httpx` for
  async IP geolocation lookups (via [ipapi.co](https://ipapi.co))
- **Frontend:** vanilla JS + `<canvas>`, no framework, no build step — one
  HTML file
- **Deploy target:** Render (see `render.yaml`)

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn main:app --reload
```

Open `http://127.0.0.1:8000` in a few different tabs (or on your phone over
the same Wi-Fi) and watch the radar populate. On localhost, probes get
randomized demo coordinates since private IPs can't be geolocated — the
latency numbers are still real, only the position is simulated.

## Deploying

The included `render.yaml` deploys this as-is on [Render](https://render.com)'s
free tier — connect the repo and it builds automatically. Any host that
supports long-lived WebSocket connections and ASGI apps (Fly.io, Railway,
a plain VM behind nginx) works the same way; just point it at
`uvicorn main:app --host 0.0.0.0 --port $PORT`.

## Known limitations / next steps

- IP geolocation is approximate (city-level) and the free `ipapi.co` tier is
  rate-limited — fine for a demo, not for production scale.
- Broadcasts fire on every latency update rather than on a fixed tick, which
  is simple but chattier than necessary at high client counts. A batched
  broadcast loop (e.g. one tick per second regardless of client count) would
  scale further.
- No persistence — the swarm state lives entirely in memory and resets on
  restart, which is intentional for a "who's here right now" visualization
  but worth knowing.

## License

MIT
