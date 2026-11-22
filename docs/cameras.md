# Idea: camera feeds on the dashboard

**The goal:** show UniFi Protect cameras (already in Home Assistant) on the TV, either popping up when there's motion or sitting in a fixed tile. This note is the feasibility check, not a built feature.

## Verdict

**Feasible, and a good fit.** The dashboard already has the two things this needs: a layout editor (a camera tile is one more widget) and a server that keeps credentials private. The work is a small server module that talks to Home Assistant, plus one TV widget. A fixed tile showing a snapshot that refreshes every second or two is the cheap version; smooth live video costs more effort and Pi CPU.

## How the pictures can get from Home Assistant to the TV

| Method | What it is | Delay | Weight on the Pi |
|---|---|---|---|
| **Snapshots** | Home Assistant's camera proxy returns a JPEG; the page re-fetches it every 1–2 s | 1–3 s | Very light |
| **MJPEG** | Home Assistant's `camera_proxy_stream` endpoint, shown in a plain image tag | about 1 s | Light at low resolution |
| **WebRTC / MSE** | Home Assistant's go2rtc (or a separate one) restreams the camera | under 1 s | Moderate; the Pi decodes H.264 |
| **HLS** | Home Assistant's stream integration | 3–10 s | Moderate; high delay, so a poor fit for live viewing |

Use the camera's **low-resolution stream** for the TV (UniFi Protect offers several qualities per camera). A Pi 4 handles one or two small H.264 streams comfortably, not a wall of 4K.

## Proposed design

1. **A `cameras` setting.** The Home Assistant address, a long-lived access token (stored like every other secret, never sent to a browser), and a list of camera entities with a friendly name and the motion sensor that goes with each.
2. **Server proxy.** `/api/cam/<id>/snapshot` (and later `/stream`) fetches from Home Assistant with the token and passes the picture through. The TV and the settings site reach it with the TV key or a settings session, like every other data feed, so the Home Assistant token never leaves the server.
3. **Motion events.** The server listens to Home Assistant's WebSocket API for state changes of the chosen motion or person-detected sensors (or polls every couple of seconds as a simpler first version) and keeps a short list of cameras that are active, each with an expiry. `GET /api/cameras` returns that list.
4. **Two ways to show it**, chosen per camera in the layout editor:
   - **A fixed tile** (a new `camera` widget): a box you move and resize like any other, refreshing a snapshot or showing a stream.
   - **A motion pop-up:** a large overlay with the camera and its name when motion starts, closing by itself after about 30 seconds of quiet. It can ignore certain hours, and only fire for people (not cars) if Protect's smart detections are exposed.
5. **Settings.** A Cameras page (expert) to add and test cameras, and a simple switch for "pop up on motion".

## Effort

| Piece | Rough size |
|---|---|
| Settings, validation, proxy for snapshots, a camera tile widget | about a day |
| Motion pop-ups (WebSocket or polling, expiry, overlay, quiet hours) | about a day |
| Low-latency live video (go2rtc/WebRTC, tuned for the Pi) | two to three days, mostly testing on the real hardware |

## Things to decide or watch

- **Privacy:** the TV is on a wall that guests see. Decide which cameras may ever appear, and whether pop-ups are suppressed when the TV is in "guest" use. Indoor cameras are best left off.
- **Network:** the dashboard server must reach Home Assistant, and Home Assistant must reach the cameras (UniFi Protect). Nothing needs to be exposed to the internet.
- **Load:** every viewer of a live stream is a stream. A tile that is always on means one constant stream; pop-ups mean short bursts.
- **Motion noise:** raw motion sensors fire on shadows and insects. Prefer Protect's person or vehicle detection, and keep a cool-down so the pop-up doesn't flicker.
- **Home Assistant versions:** the camera proxy and WebSocket interfaces are long-standing, but check the current documentation of the UniFi Protect integration for entity names before building.
- **Alternative with no server work:** embed Home Assistant's own camera card in an iframe widget. Quick to try, but it needs a login on the TV and gives no motion pop-ups on its own.

## Suggested order

1. Snapshot tile + the proxy (proves the path end to end).
2. Motion pop-ups on the same proxy.
3. Live video, only if the snapshot rate isn't good enough.
