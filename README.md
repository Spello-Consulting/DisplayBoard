# DisplayBoard 

Read me to follow. For now, take a look at the config_example.yaml file. 

Using the scripts to crop SVG images:

```bash
uv run scripts/crop_svg_viewbox.py meteocons_copy meteocons_copy_cropped --target-fill 0.9
```

## PowerController UPS status

The PowerController topic also polls the DataAPI `/ups` endpoint. Each poll adds two keys to the `powercontroller` topic data that boards receive: `ups`, a list of all UPS units, and `ups_selected`, the UPS named by `TopicPowerController.UPSName` (or the first UPS if that is blank). Each UPS dict contains `name`, `timestamp`, `time`, `charge_percent`, `runtime_sec`, `runtime` (e.g. `18m`), `battery_state` and `is_healthy`. The `primary.html` board shows the selected UPS under Internet Status. If the PowerController has no `/ups` endpoint (older versions), the UPS data stays empty and the rest of the topic works as before.

Run the tests with:

```bash
uv run pytest
```
