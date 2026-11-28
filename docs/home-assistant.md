# Home Assistant: locks, doors and cameras on the TV

The dashboard can show what Home Assistant knows: **status tiles** (locks, doors and windows, garage doors and gates, the alarm, who's home, and also lights, switches, thermostats, sensors, media players and vacuums), a **camera tile**, a **four-camera wall** that shows up to four cameras at once, and a **pop-up** that covers the screen when a camera's motion sensor fires. It works with anything Home Assistant exposes, including UniFi Protect cameras through its UniFi Protect integration.

## Setting it up

1. In Home Assistant, open your **profile** (your name, bottom left), go to **Security**, and under **Long-lived access tokens** select **Create token**. Copy it.
2. In the dashboard settings turn on **Expert settings**, open **Smart home → Connection**, enter Home Assistant's address (as the dashboard *server* reaches it, for example `http://homeassistant.local:8123`), paste the token and select **Test**, then **Save connection**.
3. Select **Load what Home Assistant has**. The security-minded items (locks, doors, alarm, people, motion, cameras) are listed first as **Suggested**; pick **Everything** or search by name to find the rest. Add what you want as tiles, and the cameras. For each camera, pick the motion sensor (or person-detected sensor) that should make it pop up.
4. Under **On the screen**, select **Add the status strip**, **Add a camera tile** or **Add a 4-camera wall** (it goes under the Today panel). Move and resize them in **Layout**; in Layout you can also pin the camera tile to one camera, or leave it to show each in turn.

Choosing what shows (renaming tiles, removing things, switching pop-ups) is available in Simple mode too, so anyone can do it. The connection itself is under Expert settings.

## What shows

| Thing | Tile says | Color |
|---|---|---|
| Lock | Locked, Unlocked, Jammed (and "Unlocked 12 min" once it's been that long) | green, red, red |
| Door, window, garage door, gate | Closed, Open (and "Open 20 min") | green, amber, red |
| Alarm | Armed home / away / night, Off, Arming, **TRIGGERED** | green, grey, amber, red |
| Person | Home, Away | green, grey |
| Anything missing in Home Assistant | Unavailable | grey |

A summary sits at the left of the strip: **All secure**, **Something is open**, or **Needs attention: Back door**. The "warn after" time (default 10 minutes, or never) is set on the Smart home page.

## How it works

- The **server** holds the token and talks to Home Assistant's REST API. The TV page and the settings site never see the token.
- `GET /api/home` returns the tiles, worked out on the server (one request per chosen item, in parallel, cached for a few seconds).
- `GET /api/cameras` says which cameras have motion now. The server checks each camera's motion sensors, remembers when motion started, and keeps a camera "active" for the pop-up time after the last motion, so a pop-up doesn't flicker. It only does this while a TV is asking.
- `GET /api/ha/camera/<id>.jpg` returns a still picture through the server, shared for a second so several viewers don't hammer the camera. Only cameras you chose can be fetched; any other id gets a 404.
- Add `?w=<pixels>` and the server asks Home Assistant for a picture of that width (160 to 1920), so a 4K camera doesn't send a huge image to a small tile. The tile asks for 640, the wall for 480 and the pop-up for 1280.
- On the TV, the camera tile and the wall refresh their pictures about every two seconds, and a pop-up refreshes once a second.
- Saving a new access token switches Home Assistant on. If the TV says it is switched off, the Smart home page has a **Turn it on** button.
- **Privacy rule:** lock states and camera pictures are available only to the TV (its TV key) or a signed-in settings session. The "Others can view" setting never exposes them.

## Limits worth knowing

- These are **still pictures refreshed every second or two**, not live video. That keeps the Pi cool and the setup simple; it's enough to see who's at the door.
- Use the right sensor. A plain motion sensor fires on shadows and insects. Where Home Assistant offers a **person detected** (or vehicle) sensor for a camera, use that for the pop-up.
- The TV is on a wall that guests see. Choose cameras on purpose; indoor cameras are best left off.
- Home Assistant must be reachable from the **server**, not just from your phone. Nothing needs to be exposed to the internet.

## Possible next steps

- Smooth live video through Home Assistant's go2rtc (WebRTC), tuned for the Pi.
- Quiet hours for pop-ups, and pop-ups only for people.
- A doorbell press as its own pop-up.
- More tile types (garage door open/close buttons would need write access, which this deliberately doesn't have).
