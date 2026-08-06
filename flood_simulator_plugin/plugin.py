from PyQt5.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QComboBox,
    QPushButton,
    QWidget,
    QDockWidget,
    QAction,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QScrollArea,
)
from PyQt5.QtCore import Qt
from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsGeometry,
    QgsWkbTypes,
    QgsFeature,
    QgsProcessingFeedback,
    QgsCoordinateTransform,
)
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand
from qgis.PyQt.QtGui import QColor
import json
import os
import math
import processing
import rasterio
import numpy

# Same Canada land cover classification as the wildfire plugin's FUELS table,
# but mapped to Manning's roughness and a steady-state infiltration rate
# instead of a fuel model. These are generic values from the literature, not
# calibrated for a specific site.

ROUGHNESS_BY_LANDCOVER = {
    1: 0.10,   # temperate/sub-polar needleleaf forest
    2: 0.08,   # sub-polar taiga
    5: 0.12,   # temperate/sub-polar broadleaf deciduous forest
    6: 0.10,   # mixed forest
    8: 0.07,   # temperate/sub-polar shrubland
    10: 0.035, # temperate/sub-polar grassland
    11: 0.05,  # sub-polar/polar shrubland-lichen-moss
    12: 0.035, # sub-polar/polar grassland-lichen-moss
    13: 0.03,  # sub-polar/polar barren-lichen-moss
    14: 0.06,  # wetland
    15: 0.04,  # cropland
    16: 0.025, # barren lands
    17: 0.02,  # urban
    18: 0.03,  # water
    19: 0.03,  # snow and ice
}

# mm/h, steady-state infiltration/loss rate
INFILTRATION_MM_H = {
    1: 12.0,
    2: 8.0,
    5: 12.0,
    6: 10.0,
    8: 8.0,
    10: 7.0,
    11: 6.0,
    12: 6.0,
    13: 4.0,
    14: 1.0,
    15: 5.0,
    16: 3.0,
    17: 1.0,
    18: 0.0,
    19: 0.0,
}

# Used when a pixel's land cover code is not one of the classes above (gaps
# or mismatch between the land cover and DEM rasters, edge pixels, etc). A
# cell or neighbour with an unknown code still gets built with these generic
# values instead of being dropped. Dropping it used to break the grid into
# disconnected islands wherever the land cover data had a gap. See the
# "unclassified" count printed after generation.
DEFAULT_ROUGHNESS = 0.035
DEFAULT_INFILTRATION_MM_H = 5.0

#########################
# PLUGIN CLASSES
#########################

class FloodPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock_widget = None
        self.selected_region = None
        self.river_region = None

    def initGui(self):
        self.action = QAction('Flood Simulator', self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)

    def run(self):
        if not self.dock_widget:
            self.dock_widget = FloodDockWidget(self)
            self.iface.addDockWidget(Qt.LeftDockWidgetArea, self.dock_widget)
        self.dock_widget.show()


class FloodDockWidget(QDockWidget):
    def __init__(self, plugin):
        super(FloodDockWidget, self).__init__(plugin.iface.mainWindow())
        self.plugin = plugin
        self.setWindowTitle("Flood Simulator")
        self.iface = plugin.iface

        self.resolution = 50

        # Rubber bands for the two regions the user draws: the simulated
        # area, and the river/breach source area (the flood version of the
        # wildfire plugin's "ignited region").
        self.plugin.rubber_band = QgsRubberBand(self.plugin.iface.mapCanvas(), QgsWkbTypes.PolygonGeometry)
        self.plugin.rubber_band.setColor(QColor(Qt.green))
        self.plugin.rubber_band.setWidth(2)

        self.plugin.river_rubber_band = QgsRubberBand(self.plugin.iface.mapCanvas(), QgsWkbTypes.PolygonGeometry)
        self.plugin.river_rubber_band.setColor(QColor(Qt.blue))
        self.plugin.river_rubber_band.setWidth(2)

        self.map_tool = RegionSelectionTool(self.iface, self, self.plugin.rubber_band, is_river_region=False)
        self.river_map_tool = RegionSelectionTool(self.iface, self, self.plugin.river_rubber_band, is_river_region=True)

        self.layout = QVBoxLayout()
        self.layout.setSpacing(4)

        self.refresh_button = QPushButton("Refresh Layers")
        self.refresh_button.clicked.connect(self.refresh)
        self.layout.addWidget(self.refresh_button)

        # --- DEM layer dropdown ---
        self.layout.addWidget(QLabel("Elevation Layer (DEM):"))
        self.dtm_selector = QComboBox()
        self.populate_raster_layers(self.dtm_selector)
        self.layout.addWidget(self.dtm_selector)

        # --- Landcover layer dropdown ---
        self.layout.addWidget(QLabel("Landcover Layer:"))
        self.landcover_selector = QComboBox()
        self.populate_raster_layers(self.landcover_selector)
        self.layout.addWidget(self.landcover_selector)

        # --- Region selection: Select/Confirm side by side to save vertical space ---
        self.select_button = QPushButton("Select Area")
        self.select_button.clicked.connect(self.activate_selection)
        self.confirm_selection_button = QPushButton("Confirm Area")
        self.confirm_selection_button.clicked.connect(self.confirm_drawn_area)
        area_row = QHBoxLayout()
        area_row.addWidget(self.select_button)
        area_row.addWidget(self.confirm_selection_button)
        self.layout.addLayout(area_row)

        self.river_button = QPushButton("Select River/Breach")
        self.river_button.clicked.connect(self.activate_river_selection)
        self.confirm_river_button = QPushButton("Confirm River/Breach")
        self.confirm_river_button.clicked.connect(self.confirm_river_region)
        river_row = QHBoxLayout()
        river_row.addWidget(self.river_button)
        river_row.addWidget(self.confirm_river_button)
        self.layout.addLayout(river_row)

        self.clear_button = QPushButton("Clear Selected Areas")
        self.clear_button.clicked.connect(self.clear_selection)
        self.layout.addWidget(self.clear_button)

        # --- Resolution ---
        self.resolution_label = QLabel(f"Resolution: {self.resolution}m")
        self.layout.addWidget(self.resolution_label)
        self.resolution_slider = QSlider(Qt.Horizontal)
        self.resolution_slider.setMinimum(1)
        self.resolution_slider.setMaximum(1000)
        self.resolution_slider.setValue(self.resolution)
        self.resolution_slider.valueChanged.connect(self.update_resolution)
        self.layout.addWidget(self.resolution_slider)

        # --- River level over time (forced depth at the source cells) ---
        self.layout.addWidget(QLabel("River level over time (h, depth m):"))
        self.river_level_table = self._build_series_table([
            (0.0, 0.0), (1.0, 2.0), (3.0, 2.0), (8.0, 0.0)
        ])
        self.layout.addWidget(self.river_level_table)
        hydro_btns = QHBoxLayout()
        add_hydro = QPushButton("+ row")
        add_hydro.clicked.connect(lambda: self.river_level_table.insertRow(self.river_level_table.rowCount()))
        rm_hydro = QPushButton("- row")
        rm_hydro.clicked.connect(lambda: self.river_level_table.removeRow(self.river_level_table.currentRow()))
        hydro_btns.addWidget(add_hydro)
        hydro_btns.addWidget(rm_hydro)
        self.layout.addLayout(hydro_btns)

        # --- Rainfall over time (applied uniformly over the whole domain) ---
        self.layout.addWidget(QLabel("Rainfall over time (h, mm/h):"))
        self.rainfall_table = self._build_series_table([
            (0.0, 0.0), (0.5, 15.0), (2.0, 25.0), (4.0, 5.0), (6.0, 0.0)
        ])
        self.layout.addWidget(self.rainfall_table)
        rain_btns = QHBoxLayout()
        add_rain = QPushButton("+ row")
        add_rain.clicked.connect(lambda: self.rainfall_table.insertRow(self.rainfall_table.rowCount()))
        rm_rain = QPushButton("- row")
        rm_rain.clicked.connect(lambda: self.rainfall_table.removeRow(self.rainfall_table.currentRow()))
        rain_btns.addWidget(add_rain)
        rain_btns.addWidget(rm_rain)
        self.layout.addLayout(rain_btns)

        self.convert_button = QPushButton("Prepare Simulation Scenario")
        self.convert_button.clicked.connect(self.convert_to_json)
        self.layout.addWidget(self.convert_button)

        # The two series tables can grow tall when rows are added, so cap
        # their height and put the whole dock in a scroll area. This way it
        # never forces the QGIS main window to resize or glitch.
        self.river_level_table.setMaximumHeight(110)
        self.rainfall_table.setMaximumHeight(110)

        container = QWidget()
        container.setLayout(self.layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setMinimumWidth(280)
        self.setWidget(scroll)

    @staticmethod
    def _build_series_table(default_rows):
        table = QTableWidget(len(default_rows), 2)
        table.setHorizontalHeaderLabels(["Time (h)", "Value"])
        for row, (t, v) in enumerate(default_rows):
            table.setItem(row, 0, QTableWidgetItem(str(t)))
            table.setItem(row, 1, QTableWidgetItem(str(v)))
        return table

    @staticmethod
    def _read_series(table):
        """Read a (time_hours, value) table into a sorted list of [time_s, value] pairs."""
        series = []
        for row in range(table.rowCount()):
            t_item = table.item(row, 0)
            v_item = table.item(row, 1)
            if t_item is None or v_item is None:
                continue
            try:
                t_hours = float(t_item.text())
                value = float(v_item.text())
            except ValueError:
                continue
            series.append([t_hours * 3600.0, value])
        series.sort(key=lambda pt: pt[0])
        return series

    def refresh(self):
        self.dtm_selector.clear()
        self.landcover_selector.clear()
        self.populate_raster_layers(self.dtm_selector)
        self.populate_raster_layers(self.landcover_selector)

    def populate_raster_layers(self, selector):
        selector.clear()
        layers = QgsProject.instance().mapLayers().values()
        for layer in layers:
            if isinstance(layer, QgsRasterLayer):
                ds = layer.dataProvider().dataSourceUri().lower()
                if ds.endswith('.tif') or ds.endswith('.tiff'):
                    selector.addItem(layer.name(), layer)

    def update_resolution(self, value):
        self.resolution = value
        self.resolution_label.setText(f"Resolution: {self.resolution}m")

    def activate_selection(self):
        self.plugin.iface.mapCanvas().setMapTool(self.map_tool)

    def activate_river_selection(self):
        if self.plugin.selected_region:
            self.plugin.iface.mapCanvas().setMapTool(self.river_map_tool)
        else:
            QMessageBox.warning(
                None, "No simulation area yet",
                "Draw and confirm a Simulation Area first (Select Area -> Confirm Area) -- "
                "the river/breach region has to be drawn inside it."
            )

    def confirm_drawn_area(self):
        if self.map_tool.points:
            self.plugin.selected_region = QgsGeometry.fromPolygonXY([self.map_tool.points])
            print("Selected region confirmed.")

    def confirm_river_region(self):
        if self.river_map_tool.points:
            self.plugin.river_region = QgsGeometry.fromPolygonXY([self.river_map_tool.points])
            print(f"River/breach source region confirmed ({len(self.river_map_tool.points)} points).")
        else:
            QMessageBox.warning(
                None, "No river/breach region drawn",
                "No points were recorded for the river/breach region. Click 'Select River/Breach', "
                "then click at least 3 points *inside* your green Simulation Area outline, then "
                "click this Confirm button (or close the polygon by clicking back near your first point)."
            )

    def clear_selection(self):
        if self.plugin.rubber_band:
            self.plugin.selected_region = None
            self.plugin.rubber_band.reset()
        if self.plugin.river_rubber_band:
            self.plugin.river_region = None
            self.plugin.river_rubber_band.reset()
        self.map_tool.points = []
        self.river_map_tool.points = []
        self.plugin.iface.mapCanvas().setMapTool(None)
        self.plugin.iface.mapCanvas().refresh()
        print("Selection cleared!")

    def clip_river_region(self, json_file_path, dtm_layer):
        """Rasterize the drawn river/breach polygon into a binary 0/1 raster aligned with the DEM grid."""
        if not self.plugin.river_region:
            return None

        # The polygon was drawn in the QGIS project's CRS (often lat/lon),
        # which is almost never the DEM's own CRS (a projected, metric one).
        # Reproject the geometry to the DEM's CRS before rasterizing, so the
        # pixel size we ask for below (in the DEM's units, metres) actually
        # matches the mask layer's units. Otherwise gdal:rasterize silently
        # makes one giant 1-degree pixel instead of a real raster (this is
        # exactly what happened: the debug output showed a (1, 1) raster).
        river_geom = QgsGeometry(self.plugin.river_region)
        dem_crs = dtm_layer.crs()
        project_crs = QgsProject.instance().crs()
        if dem_crs != project_crs:
            transform = QgsCoordinateTransform(project_crs, dem_crs, QgsProject.instance())
            river_geom.transform(transform)

        mask_layer = createTemporaryPolygonLayer(river_geom, dem_crs.authid())
        river_raster_path_temp = os.path.splitext(json_file_path)[0] + "_river_temp.tif"
        river_raster_path = os.path.splitext(json_file_path)[0] + "_river.tif"

        with rasterio.open(dtm_layer.source()) as dem_src:
            dem_height = dem_src.height
            dem_width = dem_src.width
            dem_transform = dem_src.transform
            dem_crs = dem_src.crs
            pixel_size_x = abs(dem_transform.a)
            pixel_size_y = abs(dem_transform.e)

        # Burn the polygon itself into a raster at roughly the DEM's own
        # resolution. This is a direct "is this pixel inside the drawn
        # region" mask, independent of the DEM's own values. The previous
        # approach clipped the DEM's elevation with this mask and tested "is
        # it non-zero", which was fragile. Extent is left as the algorithm's
        # default (the mask layer's own bounds), not a hand-built string,
        # because a mismatched string was a likely reason this kept coming
        # out empty.
        processing.run("gdal:rasterize", {
            "INPUT": mask_layer,
            "FIELD": None,
            "BURN": 1,
            "UNITS": 1,  # georeferenced units: WIDTH/HEIGHT below are a pixel size, not a pixel count
            "WIDTH": pixel_size_x,
            "HEIGHT": pixel_size_y,
            "NODATA": 0,
            "DATA_TYPE": 1,  # Byte
            "OUTPUT": river_raster_path_temp
        })

        # Explicitly resample that onto the DEM's exact pixel grid, same
        # approach already used for the land cover layer. This way it does
        # not depend on the rasterize step's extent matching the DEM by
        # chance.
        with rasterio.open(river_raster_path_temp) as river_src:
            river_data = river_src.read(1)
            river_transform = river_src.transform
            river_crs = river_src.crs

            print(f"DEBUG river rasterize: temp raster shape={river_data.shape}, "
                  f"crs={river_crs}, bounds={river_src.bounds}, "
                  f"pixels burned before resample={int((river_data != 0).sum())}")
            print(f"DEBUG DEM grid: shape=({dem_height}, {dem_width}), crs={dem_crs}, "
                  f"bounds={rasterio.transform.array_bounds(dem_height, dem_width, dem_transform)}")

            resampled = numpy.zeros((dem_height, dem_width), dtype=numpy.uint8)
            rasterio._warp._reproject(
                source=river_data,
                destination=resampled,
                src_transform=river_transform,
                src_crs=river_crs,
                dst_transform=dem_transform,
                dst_crs=dem_crs,
                resampling=rasterio._warp.Resampling.nearest
            )
            with rasterio.open(river_raster_path, 'w', driver='GTiff', height=dem_height, width=dem_width,
                            count=1, dtype=resampled.dtype, crs=dem_crs, transform=dem_transform) as dst:
                dst.write(resampled, 1)

        # Sanity check: report right away whether anything got burned and
        # survived the resample onto the DEM grid, instead of only finding
        # out after the JSON comes out empty.
        burned_pixels = int((resampled != 0).sum())
        if burned_pixels == 0:
            QMessageBox.warning(
                None, "River/breach region is empty",
                "The river/breach polygon was rasterized but no pixels came out marked once "
                "aligned to the DEM grid -- double check the polygon is fully inside your "
                "Simulation Area, and that the DEM and river polygon are in a sane, matching CRS."
            )
        else:
            print(f"River/breach raster: {burned_pixels} pixel(s) marked (DEM grid).")

        return river_raster_path

    def convert_to_json(self):
        dtm_layer = self.dtm_selector.currentData()
        landcover_layer = self.landcover_selector.currentData()

        if not (dtm_layer and landcover_layer and self.plugin.selected_region and self.plugin.selected_region.isGeosValid()):
            print("No valid layers or selected region. Cannot convert to JSON.")
            return

        mask_layer = createTemporaryPolygonLayer(self.plugin.selected_region)

        root = os.path.dirname(os.path.abspath(__file__))
        json_file_path = os.path.join(root, "flood_map.json")

        clipped_dtm_path = os.path.splitext(json_file_path)[0] + "_dtm.tif"
        clipped_land_path = os.path.splitext(json_file_path)[0] + "_landcover.tif"

        processing.run("gdal:cliprasterbymasklayer", {
            "INPUT": dtm_layer.source(), "MASK": mask_layer, "CROP_TO_CUTLINE": True, "OUTPUT": clipped_dtm_path
        })
        processing.run("gdal:cliprasterbymasklayer", {
            "INPUT": landcover_layer.source(), "MASK": mask_layer, "CROP_TO_CUTLINE": True, "OUTPUT": clipped_land_path
        })

        clipped_dtm_layer = QgsRasterLayer(clipped_dtm_path, "flood_dtm_clip")
        if not clipped_dtm_layer.isValid():
            print("Failed to load clipped DEM!")
            return

        # Align the river/breach raster to the CROPPED DEM, not the original
        # full layer. dump_flood_json's sampling loop reads elevation and
        # land cover from the cropped grid, and indexes river_data with the
        # same (row, col) pairs. Aligning to the uncropped DEM instead gave
        # the river raster a different shape and origin, so those indices
        # pointed at the wrong location. The raster had real burned pixels,
        # just never at the (row, col) positions the sampling loop checked.
        river_raster_path = self.clip_river_region(json_file_path, clipped_dtm_layer)

        paths = {
            "elevation": clipped_dtm_path,
            "land": clipped_land_path,
            "river": river_raster_path,
            "json": json_file_path,
        }

        # Resample landcover onto the DEM grid (same approach as the wildfire plugin).
        with rasterio.open(paths["elevation"]) as dem_src:
            dem_transform = dem_src.transform
            dem_crs = dem_src.crs
            dem_width = dem_src.width
            dem_height = dem_src.height

        with rasterio.open(paths["land"]) as land_src:
            land_data = land_src.read(1)
            land_transform = land_src.transform
            land_crs = land_src.crs

            # Fill with -1 (not a valid land cover code) instead of leaving
            # this uninitialised. Any pixel the reprojection does not cover
            # (source/DEM extent mismatch) must read as "unclassified", not
            # whatever was already in memory.
            resampled_land_data = numpy.full((dem_height, dem_width), -1, dtype=numpy.float32)
            rasterio._warp._reproject(
                source=land_data,
                destination=resampled_land_data,
                src_transform=land_transform,
                src_crs=land_crs,
                dst_transform=dem_transform,
                dst_crs=dem_crs,
                resampling=rasterio._warp.Resampling.nearest
            )
            resampled_land_path = os.path.splitext(json_file_path)[0] + "_landcover_resampled.tif"
            with rasterio.open(resampled_land_path, 'w', driver='GTiff', height=dem_height, width=dem_width,
                            count=1, dtype=resampled_land_data.dtype, crs=dem_crs, transform=dem_transform) as dst:
                dst.write(resampled_land_data, 1)
            paths["land"] = resampled_land_path

        river_levels = self._read_series(self.river_level_table)
        rainfall = self._read_series(self.rainfall_table)

        dump_flood_json(paths, self.resolution, river_levels, rainfall)
        print(f"JSON conversion completed: {json_file_path}")


class RegionSelectionTool(QgsMapToolEmitPoint):
    def __init__(self, iface, plugin, rubber_band, is_river_region=False):
        super(RegionSelectionTool, self).__init__(iface.mapCanvas())
        self.points = []
        self.plugin = plugin
        self.rubber_band = rubber_band
        self.is_river_region = is_river_region

    def canvasPressEvent(self, event):
        point = self.toMapCoordinates(event.pos())

        if self.is_river_region:
            if not self.plugin.selected_region or not self.plugin.selected_region.contains(point):
                QMessageBox.warning(
                    None, "Point outside simulation area",
                    "That click is outside your confirmed Simulation Area, so it was ignored -- "
                    "the river/breach region must be fully inside it. Click somewhere inside the "
                    "green outline instead."
                )
                return

        if len(self.points) > 2 and self.is_near_first_point(point):
            self.points.append(self.points[0])
            if self.is_river_region:
                self.plugin.river_region = QgsGeometry.fromPolygonXY([self.points])
                print(f"Polygon closed and river/breach source region set ({len(self.points)} points).")
            else:
                self.plugin.selected_region = QgsGeometry.fromPolygonXY([self.points])
                print(f"Polygon closed and selected region set ({len(self.points)} points).")
            self.highlight_region()
            return

        self.points.append(point)
        print(f"{'River/breach' if self.is_river_region else 'Simulation area'} point added "
              f"({len(self.points)} so far). Click near the first point to close the polygon, "
              f"or use the Confirm button.")
        self.highlight_region()

    def is_near_first_point(self, point, tolerance=50):
        first_point = self.points[0]
        dist = math.sqrt((first_point.x() - point.x()) ** 2 + (first_point.y() - point.y()) ** 2)
        return dist <= tolerance

    def highlight_region(self):
        self.rubber_band.reset()
        for p in self.points:
            self.rubber_band.addPoint(p)


#########################
# HELPER FUNCTIONS
#########################

def createTemporaryPolygonLayer(geometry, crs=None):
    crs = crs or QgsProject.instance().crs().authid()
    mem_layer = QgsVectorLayer(f"Polygon?crs={crs}", "mask", "memory")
    prov = mem_layer.dataProvider()
    feat = QgsFeature()
    feat.setGeometry(geometry)
    prov.addFeatures([feat])
    mem_layer.updateExtents()
    return mem_layer


def read_raster(path):
    with rasterio.open(path) as src:
        data = src.read(1)
        transform = src.transform
        crs = src.crs
        return data, transform, crs


def dump_flood_json(paths, resolution, river_levels, rainfall):
    """Read raster data and populate the JSON scenario for the flood Cell-DEVS model."""
    # The hex-offset grid below places a diagonal neighbour at +/- half of
    # resolution. For an odd resolution that half is not a whole number, and
    # rounding it makes the +half and -half offsets land on different
    # columns instead of mirroring each other. One side then points at a
    # cell that was never generated, which crashes the simulator with
    # "component not found". Snapping to an even resolution keeps the offset
    # symmetric and avoids this bug.
    if resolution % 2:
        resolution += 1
        print(f"Resolution must be even for the grid to stay fully connected; using {resolution}m.")

    elevation_data, elev_transform, _ = read_raster(paths['elevation'])
    landcover_data, _, _ = read_raster(paths['land'])
    river_data, _, _ = read_raster(paths['river']) if paths.get('river') else (None, None, None)

    height, width = elevation_data.shape

    data = {
        "cells": {
            "default": {
                "delay": "inertial"
            }
        }
    }

    total_cells = 0
    unclassified_cells = 0
    river_cells = 0

    shift = 0
    for row in range(0, height, resolution):
        for col in range(int(-0.5 * resolution), int(width + 0.5 * resolution), resolution):
            if shift % 2:
                col = int(col + (0.5 * resolution))
            if col >= width or col < 0:
                continue

            elevation_value = elevation_data[row][col]
            if elevation_value <= -9999.0:
                continue

            # A pixel with an unknown land cover code still becomes a real
            # cell (with generic default values) instead of being dropped.
            # Dropping it here used to silently break the grid into
            # disconnected islands wherever the land cover data had a gap or
            # did not line up with the DEM.
            landcover_key = int(landcover_data[row][col])
            if landcover_key in ROUGHNESS_BY_LANDCOVER:
                roughness = ROUGHNESS_BY_LANDCOVER[landcover_key]
                infiltration_mm_h = INFILTRATION_MM_H[landcover_key]
            else:
                roughness = DEFAULT_ROUGHNESS
                infiltration_mm_h = DEFAULT_INFILTRATION_MM_H
                unclassified_cells += 1
            total_cells += 1

            # A single sampled pixel can easily miss a thin, linear feature
            # like a river, even when it clearly passes through this cell's
            # area. So check the whole block of pixels this cell represents
            # (roughly resolution x resolution), not just the one pixel at
            # (row, col).
            if river_data is not None:
                half = max(1, resolution // 2)
                row_lo, row_hi = max(0, row - half), min(height, row + half + 1)
                col_lo, col_hi = max(0, col - half), min(width, col + half + 1)
                is_river = bool(river_data[row_lo:row_hi, col_lo:col_hi].any())
            else:
                is_river = False
            if is_river:
                river_cells += 1

            x, y = elev_transform * (col, row)
            cell_name = f"{int(x)}_{int(y)}"

            state = {
                "x": float(x),
                "y": float(y),
                "elevation": float(elevation_value),
                "roughness": roughness,
                # mm/h -> m/s
                "infiltrationRate": infiltration_mm_h / 1000.0 / 3600.0,
                "isRiverSource": is_river,
            }
            if is_river:
                state["riverLevelOverTime"] = river_levels
            else:
                state["rainfallOverTime"] = rainfall

            data["cells"][cell_name] = {
                "neighborhood": {},
                "state": state,
            }
            data["cells"][cell_name]["neighborhood"][cell_name] = 0

            neighborhood = [(1, 0.5), (1, -0.5), (-1, 0.5), (-1, -0.5), (0, 1), (0, -1)]
            for neighbor in neighborhood:
                r = row + int(neighbor[0] * resolution)
                c = col + int(neighbor[1] * resolution)
                if c < 0 or r < 0 or c >= width or r >= height:
                    continue

                neighbor_elevation_value = elevation_data[r][c]
                if neighbor_elevation_value <= -9999.0:
                    continue
                # Land cover is not re-checked here: that neighbour gets its
                # own cell entry (with a default land cover value if needed)
                # when the outer loop reaches its (row, col) directly.

                neighbor_x, neighbor_y = elev_transform * (c, r)
                neighbor_name = f"{int(neighbor_x)}_{int(neighbor_y)}"

                if neighbor in ((0, 1), (0, -1)):
                    data["cells"][cell_name]["neighborhood"][neighbor_name] = resolution
                else:
                    data["cells"][cell_name]["neighborhood"][neighbor_name] = 1.118 * resolution

        shift += 1

    with open(paths['json'], "w") as f:
        json.dump(data, f, indent=4)
    print(f"JSON file saved to: {paths['json']}")
    print(f"{total_cells} cells created, {unclassified_cells} used the default land cover value "
          f"({DEFAULT_ROUGHNESS} roughness / {DEFAULT_INFILTRATION_MM_H}mm/h infiltration).")
    if total_cells and unclassified_cells / total_cells > 0.3:
        print("WARNING: more than 30% of cells have no recognised land cover code. "
              "Check that your Landcover layer actually covers your simulation area and "
              "lines up with your DEM (same 'map canvas extent' when you exported both).")

    print(f"{river_cells} cell(s) marked isRiverSource=true.")
    if paths.get('river') and river_cells == 0:
        print("WARNING: a river/breach region was drawn but zero cells ended up marked as "
              "isRiverSource. The polygon likely fell between simulation grid points at this "
              "resolution -- try a coarser Resolution setting or a slightly larger polygon.")
