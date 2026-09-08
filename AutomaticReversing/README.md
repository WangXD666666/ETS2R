# AutomaticReversing

AutomaticReversing is a first-pass standalone reversing assistant for Euro Truck
Simulator 2 / American Truck Simulator. It reuses the ideas and shared-memory
protocols from ETS2LA 1.11.0, but it does not need the ETS2LA executable to run.

## Layout

- `automatic_reversing/` contains the reusable control core and SCS SDK adapters.
- `automatic_reversing_app/` contains the standalone Tkinter desktop app.
- `automatic_reversing_data_viewer/` contains the standalone telemetry data viewer.
- `automatic_reversing_visualizer/` contains the standalone top-down geometry visualizer.
- `ets2la_plugin/AutomaticReversing/` contains the optional ETS2LA plugin wrapper.
- `tests/` contains offline tests for geometry, telemetry parsing and safety.

## Standalone run

The standalone app talks directly to the `Local\SCSTelemetry` and
`Local\SCSControls` shared memory blocks. ETS2/ATS must have the telemetry and
input control DLLs installed in `bin/win_x64/plugins`.

From this folder:

```powershell
python -m automatic_reversing_app
```

or:

```powershell
python run.py
```

To inspect all live vehicle telemetry data without controlling the truck:

```powershell
python -m automatic_reversing_data_viewer
```

or:

```powershell
python run_data_viewer.py
```

To visualize the live truck/trailer geometry, wheel positions and saddle/hook points:

```powershell
python -m automatic_reversing_visualizer
```

or:

```powershell
python run_vehicle_visualizer.py
```

Workflow:

1. Start ETS2/ATS and load into the truck with one attached trailer.
2. Start AutomaticReversing.
3. Place the trailer at the desired final pose and click `Set Target`, or press
   `F9`.
4. Adjust the target with the nudge buttons while watching the top-down vehicle
   visualization.
5. Move to a reasonable starting point and click `Start / Stop`, or press `F8`.
6. Press `F10`, click `Cancel`, or brake in game to stop.

Target placement is strictly planar: only world X, world Z and yaw are used;
height, pitch and roll do not participate in parking or control. In the SCS
coordinate system, yaw zero points toward world `-Z`. Pressing `F9` creates a
straight rectangular bay at the trailer's current X/Z position and aligns it
with the truck's current heading. The `前移`, `后移`, `左移` and `右移` buttons
always move that bay relative to the truck's current front/rear/left/right axes,
even after the bay heading has been adjusted.

At startup the controller projects the target position onto the truck's current
forward axis. It selects Drive when the target is in front and Reverse when the
target is behind, then keeps that direction for the current run so it cannot
oscillate between gears near the target. Pressing either the physical throttle
or brake cancels automatic control and releases steering, throttle, brake and
gear outputs immediately. Inside the target tolerance the controller keeps
braking only until telemetry reports that the vehicle has stopped.

The main window shows:

- A top-down visualization of the truck, trailer and target pose.
- Obstacles around the complete vehicle and the collision-aware translation
  space available while the current truck/trailer articulation is held.
- Live vehicle parameters such as truck/trailer position, heading,
  articulation, speed, gear and user brake.
- Current output command values for steering, throttle, brake and reverse.

## Surroundings input

The reversing window watches `surroundings.json` for obstacle snapshots from a
camera, lidar or other detector. The file must be refreshed continuously;
snapshots older than 1.5 seconds remain visible in grey while the green movement
space is disabled. `surroundings.example.json` documents the input schema.

Use `coordinate_system: "vehicle"` for points relative to the current truck
pose (`x` right, `z` forward), or `coordinate_system: "world"` for telemetry
world coordinates. Obstacles accept either a polygon or a center/size/yaw box.
The `演示环境` checkbox displays a clearly labelled synthetic parking scene and
never enables itself automatically.

The green outline is computed against the resolved collision contours of the
truck and every attached trailer. It represents rigid translation with the
current articulation held; it is a visualization aid and is not fed into the
automatic reversing controller.

If the optional `keyboard` package is installed and Windows allows it, `F8`,
`F9` and `F10` are registered as global hotkeys. Otherwise they still work when
the AutomaticReversing window has focus.

## Vehicle data viewer

The data viewer window is read-only. It shows the full parsed SCS telemetry tree,
including truck configuration, truck input/output state, placement, job data,
special events, substances and up to 10 trailer slots.

Useful controls:

- Search by path or value.
- Pause or resume live refresh.
- Expand or collapse the whole tree.
- Copy selected values.
- Export the current snapshot as JSON.


## Vehicle geometry visualizer

The geometry visualizer is read-only. Its primary view combines live telemetry
with the installed game's own geometry. Wheel positions, axle groups, direction,
the truck saddle and trailer hook come from telemetry. Body outlines come from
the SII `collision` references and PMC collision primitives/convex pieces in the
installed game. Steerable wheels are rotated to their live telemetry angle.

Prepare the local game cache once (the extracted cache uses several GiB):

```powershell
python prepare_game_geometry.py --yes
```

Run it again with `--force` after ETS2/ATS updates. The script detects Steam or
accepts `--game-dir`. It requires the official 1.55+ `scs_extractor.exe` under
`tools/scs_extractor/`.

The resolver matches truck chassis using telemetry axle/powered-wheel data and
matches trailers using ID, body type, point span and axle count. The selected
PMC paths and resulting collision-envelope dimensions are shown in the UI.

Vehicle changes are detected from the complete live wheel signature, including
wheel positions and steerable, powered and liftable axle flags. For PMC files
with multiple collision variants, the resolver selects the variant that covers
the live wheel and coupling points. Freight-market IDs such as
`scs_lowbed.ch_7_3x2esii` are also resolved through the game's legacy trailer
definitions instead of being treated as trailer-owned directory names.

The visualizer checks the truck, chassis, attached trailer, body type, chain
type, wheel layout and coupling points every 120 ms. A changed configuration is
committed after two identical telemetry frames, then the geometry cache is
cleared and rebuilt. This avoids selecting a half-updated model while the game
is switching vehicles.

Definitions for some newer trucks and trailers are stored in separate DLC
archives rather than `def.scs`. When a current vehicle definition is missing,
the visualizer automatically imports a high-confidence matching DLC archive.
For mods or archives whose filenames do not match the telemetry ID, click
`导入当前车型资源` and select the relevant `.scs` file. The imported definitions
and PMC/PMG assets become part of the local cache, so no source-code change is
needed when that vehicle is used again.

The same live game geometry is embedded in the main reversing app. Its top-down
view uses the resolved truck/trailer collision contours, live wheel locations
and steering angles, saddle and hook positions. The saved trailer target is
drawn as a dashed copy of the current real trailer contour. Use `车型轮廓` or
`导入车型资源` directly from the reversing toolbar when a configuration needs
manual selection or a DLC/mod archive has not been cached yet.

Parking targets are session-only world poses. They are deliberately not restored
after restarting the app, and a stable truck/trailer configuration change clears
the target and cancels active control. This prevents a target from an old depot,
job or vehicle combination from being reused accidentally.

If telemetry cannot distinguish configurations that share the same wheel
layout, open `车型配置` in the visualizer. The dialog lists the chassis, cabin
and trailer body definitions available for the current vehicle. Saved choices
are stored in `geometry_overrides.json` under a complete configuration
signature and are applied automatically whenever that combination is used
again. Choosing `恢复自动并保存` removes the override for the displayed
combination.

SCS telemetry does not expose rendered body bounds, so wheel/hook point span is
not vehicle length. If a game collision resource cannot be matched, the program
does not invent an outline. `vehicle_dimensions.json` remains an optional,
lower-priority fallback for explicitly verified entries:

```json
{
  "truck": {
    "truck.id.from.telemetry": {"width": 2.55, "front_z": 4.2, "rear_z": -1.8, "verified": true}
  },
  "trailer": {
    "trailer.id|body|chain|name": {"width": 2.60, "front_z": 3.4, "rear_z": -10.2, "verified": true}
  }
}
```

Only entries with `"verified": true` are accepted. Missing or unverified data
leaves the outline hidden while the live telemetry skeleton remains visible.

## Optional ETS2LA integration

Copy or link `ets2la_plugin/AutomaticReversing` into
`ETS2LA-1.11.0/plugins/AutomaticReversing`, then enable:

- `TruckSimAPI`
- `SDKController`
- `AutomaticReversing`

The plugin writes `data["sdk"]` values consumed by ETS2LA's `SDKController`.
It is intentionally conservative and cancels on user brake, paused game,
unsupported trailer count, missing telemetry or excessive articulation.

## Tests

If pytest is installed:

```powershell
python -m pytest
```

Without pytest:

```powershell
python -B -c "import importlib; mods=['tests.test_geometry','tests.test_controller','tests.test_telemetry','tests.test_scs_sdk','tests.test_full_telemetry']; [getattr(importlib.import_module(m), n)() for m in mods for n in dir(importlib.import_module(m)) if n.startswith('test_')]; print('ok')"
```
