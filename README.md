**Asynchronous Cell-DEVS Model of a flood based on a Manning diffusive-wave approximation**

**Introduction**

This repository contains a Cell-DEVS model of a flood inundation event (riverine/breach and/or pluvial). It is the hydrological companion to the [wildfire Cell-DEVS model](https://github.com/murf85/wildfire): same Cadmium v2 asymmetric Cell-DEVS engine and the same QGIS-driven grid-building convention, but the local physics is a diffusive approximation of the 2D water equations.

Each cell exchanges water with its neighbours based on the difference in the terrain elevation (ground elevation + depth), which is what lets ponding and backwater behave correctly instead of just draining straight down the DEM. A river source region is a forced boundary condition (a river level over time). the flood equivalent of the wildfire model's ignited region and can be combined with rainfall over time and land cover infiltration losses.

**Dependencies**

Cadmium_v2 - https://github.com/SimulationEverywhere/cadmium_v2/ (included as a git submodule)

QGIS - https://qgis.org/download/

Python rasterio library - https://rasterio.readthedocs.io/en/stable/

**QGIS setup**

Copy the `flood_simulator_plugin` folder into the QGIS plugins folder:

C:\Users\user\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\flood_simulator_plugin

In the QGIS Plugins tab access the Python Console and type "import rasterio"
Under "Plugins -> Manage and Install Plugins" search for the Flood Simulator plugin, check to add it and then restart QGIS.
There will now be a button for the Flood Simulator.

**Maps**

For real Canadian scenarios:
- Elevation (DEM): https://ftp.maps.canada.ca/pub/elevation/dem_mne/highresolution_hauteresolution/dtm_mnt/
- Land cover (same classification used to derive roughness and infiltration below): https://open.canada.ca/data/en/dataset/ee1580ab-a23d-4f86-a09b-79763677eb47/resource/81252d30-5102-46db-a9c5-6ab1ccd5dcd7
- River level / streamflow records: Water Survey of Canada historical & real-time hydrometric data (HYDAT), https://wateroffice.ec.gc.ca/
- Design-storm rainfall intensities: ECCC Engineering Climate Datasets / IDF curves, https://climate.weather.gc.ca/prods_servs/engineering_e.html

**Build**

Clone with submodules:

git clone --recurse-submodules <this-repo-url>

(if already cloned without `--recurse-submodules`, run `git submodule update --init --recursive` from inside `main_flood/`)

Build:

source build_sim.sh

NOTE: Every time you run build_sim.sh, the contents of build/ and bin/ will be replaced.

**Execute**

./bin/flood config/flood_example.json output.csv "2025-04-28 10:30:00" [duration_seconds]

`duration_seconds` is optional and defaults to 86400 (24h); pass a larger value for scenarios who takes longer than a day.

**Viewer**

To view the output in QGIS go to Layer -> Add Layer -> Add Delimited Text Layer then choose the output file.
Once the layer is available right click and choose Properties.
In the Temporal menu click the box Dynamic Temporal Control and then set the Configuration to Single Field with Date/Time, and click the box Accumulate features of time.
On the Temporal controller click the blue arrows and select Full Range, and set the step to seconds or minutes. The slide bar will reveal the flood progression.

**Modelling notes / limitations**

This is a diffusive-wave approximation (Manning's equation driven by water-surface-elevation gradients), the same simplification used by fast/large-scale inundation models not a full dynamic model. Infiltration uses a simplified constant rate method rather than a variable rate. 
