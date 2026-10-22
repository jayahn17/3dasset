# Spatial VR: a learning topic as a room

`spatial.html` turns any topic spec into a room you can stand in: the variables are cities on a
globe you turn with your hands, the 2D figures stand around you, and the simulation runs on the
table. This note covers where the idea came from, how it maps onto learning, and how to open the
room in a headset.

## Where it came from

A LinkedIn post by Ferhan Özkan (October 2026, tagged #SpatialComputing #PICO) shows an agent-built
"Jarvis-style" Earth interface on PICO's new headset. The post reports that:

- the app shows live air traffic, earthquakes and satellites (including the ISS) on a globe;
- you turn the globe with your hands;
- tapping a place raises a 3D city on the table, streamed from Google Photorealistic 3D Tiles
  through Cesium;
- four data panels sit side by side in one app;
- an agent wrote it as a native Kotlin app through the PICO CLI. It was inspired by Bilawal Sidhu's
  open-source *God's Eye View*, a CesiumJS browser globe that layers public flight, ship,
  satellite, quake and camera feeds.

These are the post's claims. The PICO tooling named in it is not covered here; PICO OS 6 and its
Spatial SDK were announced in March 2026.

## The four patterns, applied to learning

| In the Earth app | In the learning room | Why it helps a learner |
|---|---|---|
| A globe you turn with your hands | **Concept globe.** Every variable is a city, coloured by kind; arcs are relations (blue +, red −, grey ?); pulses run from cause to effect. Grab and turn it, or resize it with both hands. | The whole model stays in view at once, and turning it by hand makes you follow the causal flow around the sphere. |
| Tap a place, a 3D city rises | **Tap a variable.** Its tower rises, unrelated arcs fade, and a card unfolds on the table: value and unit, equation, what pushes it (with each mechanism) and its curve over time with a "now" marker. | Drill-down without losing the overview: the question "what drives this?" is answered where you are already looking. |
| Four panels side by side | **Figure arc.** The variable graph, influence matrix, timeline and tree stand on an arc at eye height. Tap one to bring it close. | The same model in four notations at once, the way the printed page offers them in sequence. |
| Live feeds (ISS, quakes) | **Live test.** Optional `live:` feeds put a real measurement beside the model value it tests, after a unit check. | The model is held against reality. In the orbit chapter, the real ISS speed sits next to the model's circular speed. |

Two more pieces fit on the table:

- **Path plate:** the first two states (or the pair you name) trace their path as the simulation
  plays: the orbit, or the rabbit-fox loop. **Unroll time** lifts that path into a helix with time
  as height. A central body (`origin_radius`) rides up the time axis with the satellite circling it.
- **Event flashes:** events fire above the plate as they happen (Apoapsis, Fox peak).

## Spec

```yaml
figures:
  - type: spatial                # included by default when a spec has no figures list
    path: [x, y]                 # optional: which two simulated variables trace the plate
    origin_radius: 6.371e6       # optional, same unit as the path: draws a central body to scale
    live:                        # optional: public JSON feeds beside model values
      - label: ISS orbital speed, right now
        url: https://api.wheretheiss.at/v1/satellites/25544
        field: velocity          # dotted path into the JSON
        unit: km/h               # converted to the compared variable's unit
        compare: v_circ          # must have compatible dimensions, or the feed is dropped with a note
        every: 5                 # seconds between polls (at least 2)
```

Feeds must be `https`, because a headset page cannot fetch plain http, and must send CORS headers.
Unit mismatches, unknown variables and bad path names are reported in the build notes and in the
room's legend; they never fail silently.

## Opening it in a headset

WebXR runs only in a secure context: `https://` or `localhost`.

1. `make examples && make serve` serves `build/` on port 8731.
2. **PICO or Quest over USB** (developer mode on): run `adb reverse tcp:8731 tcp:8731`, then open
   `http://localhost:8731/zohdi_drone_book/ch9_satellite_orbit/spatial.html` in the headset browser.
3. Press **Enter VR** (a dark room) or **Enter AR** (the room over passthrough, so the globe sits on
   your real desk).
4. Pinch, or pull the trigger, to grab and tap. With hand tracking, reach into the globe to turn it
   directly. Table buttons (play, unroll, reset) appear in the headset.

On a desktop, the mouse does the same: drag the globe, click cities and panels, and drag empty
space to orbit the camera. To test XR input without a headset, use a WebXR emulator browser
extension.

Status: desktop interaction is verified in Chrome. The XR paths (pinch-select, direct grab,
two-hand resize, AR passthrough) use standard WebXR and three.js r160 APIs but have not been run
on a headset yet.

## Where to take it next

- **Real places for geo topics:** load CesiumJS with Google Photorealistic 3D Tiles (an API key is
  required) and swap the concept globe for Earth when a spec has latitude/longitude variables.
- **Native build:** the same JSON payload could drive a PICO Spatial SDK or Unity scene. Keep the
  spec, checker and payload; replace only the view.
- **Voice:** God's Eye View adds a realtime voice agent. Here, "why did ε stay constant?" could
  route to the reader → checker → writer agents and select the cities it names.
