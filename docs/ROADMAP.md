# Roadmap

Where this could go next, grouped by what it gives the family and how much work it is. Effort is a rough guess: **S** is an evening, **M** is a weekend, **L** is a project.

## Do next: trust and upkeep

These make the dashboard something nobody has to think about.

| Idea | Why | Effort |
|---|---|---|
| **"Usual state" for each tile.** Mark a lock or door as "normally unlocked", or only warn at certain hours (a garage door left open after 10 pm). | The first real-world complaint was a door that is always unlocked turning the strip red all day. Warnings should mean something. | S |
| **Tell someone when it breaks.** A push, email or Home Assistant notification when the TV has been offline, a feed has been stale for an hour, or the Maps budget is nearly spent. | Today you find out by looking at the TV. | M |
| **Automatic backups, and restore from the settings page.** A nightly copy of the data folder, plus an upload button on the Backup page. | Backup is download-only now. A restore button turns a bad day into a two-minute fix. | M |
| **Browser tests with screenshots.** Load the demo page in several states (`?busy=1` and friends), compare screenshots, run on every push. | The wrapping status strip was only caught by a person looking. | M |
| **Continuous integration.** Run the server, chaos and Pi tests on every push, and keep dependencies up to date automatically. | Tests only help if they run. | S |
| **Battery backup guidance and a hardware watchdog** for the Pi. | The network watchdog covers a lost router; a hardware watchdog would also cover a frozen Pi. | S |
| **Nightly browser restart on the Pi** and a read-only system partition. | Chromium slowly grows; SD cards wear out. Both fixes are well known. | S |

## The screen itself

| Idea | Why | Effort |
|---|---|---|
| **Rotating pages.** Calendar, then cameras, then photos, each for a set time, or switched by an event such as the doorbell. | One screen can only hold so much. | M |
| **Wake on presence.** Use a Home Assistant motion or occupancy sensor to wake the TV, and let it sleep when the room is empty. | Better than fixed sleep hours. | M |
| **Photo frame mode** when nothing needs attention, with the day's next event in a corner. | Makes the screen pleasant, not just useful. | M |
| **Ambient dimming** from a light sensor, not just a clock. | | S |
| **Smooth live video** for the motion pop-up (WebRTC or HLS through Home Assistant) instead of a still every second. | Still pictures miss the moment. | L |
| **Quiet hours for pop-ups,** and "person or vehicle detected" only. | Fewer false alarms. | S |
| **Large-text mode** for easy reading across a room, and more languages and units. | | M |

## New widgets

Each is a small box in the layout editor with its own settings page.

- **Countdowns** (birthdays, trips, school breaks). **S**
- **Family notes board.** Type from a phone, shows on the TV: shopping list, reminders, "dinner at 6". **M**
- **Chores and allowance** per person, checked off from a phone. **L**
- **Meal plan** for the week, from a simple list or a recipe app. **M**
- **Trash and recycling day,** and other repeating reminders. **S**
- **Real departures** for a bus or train stop, next to the drive times. **M**
- **Air quality, pollen, sunrise and sunset, school lunch menu.** **S** each
- **Sports scores** for a few chosen teams. **M**
- **Weather-aware calendar:** "rain at 3 pm pickup" on the event itself. **M**
- **Add an event from the phone,** written back to Google Calendar. **M**
- **Per-person view,** filtering calendars and tiles for one family member. **M**

## People and access

| Idea | Effort |
|---|---|
| Passkeys or a second step for the settings login | M |
| A **view-only role** for kids and guests (no keys, no layout), alongside the full settings | M |
| A **change log** on the About page: who changed what, and when | S |
| **More than one TV,** each with its own key, layout and sleep schedule | L |
| A reminder to rotate the TV key and Home Assistant token | S |

## Packaging

- **A Home Assistant add-on,** so the whole thing installs from inside Home Assistant. **L**
- **One-command install** for the server (a script that checks Docker, makes the data folder, starts it). **S**
- **Built-in HTTPS** with an optional certificate, so it doesn't need a separate proxy. **M**
- **Update check** on the About page, with the changelog. **S**
- **Export and import** the layout and look, to share a setup between two families. **S**
- **Split `server.py`** (about 2,400 lines) into modules: sources, Home Assistant, settings, web. This is housekeeping with no visible change, but it makes everything above cheaper. **M**

## Already done

Two-week calendar with merged duplicate events, drive times with a monthly Maps budget, weather, scrolling stock bar, Home Assistant tiles, cameras, a four-camera wall and motion pop-ups, free layout editor with fullscreen, backgrounds and appearance, sleep schedules, Simple and Expert settings, backup page, off-site server with a display-only Pi.

## How it was tested

`server/test_chaos.py` throws hostile settings, malformed web requests, lying or hanging Home Assistant and calendar services, and damaged data folders at the server. `pi/test_kiosk_setup.py` also checks that the TV launcher ignores a captive-portal page. Run them with the commands in the README.
