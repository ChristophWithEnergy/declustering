# Spatial Allocation Module

General-purpose spatial allocation / declustering pipeline in a single Python file.

Distributes cluster-level values (e.g. heat demand, electricity, population) to individual geometries (e.g. buildings or points) using configurable spatial matching and weighting strategies.

---

## Features

- ✅ Single-file implementation  
- ✅ Works with CSV + WKT geometries  
- ✅ Supports different CRS  
- ✅ Matching strategies:
  - `nearest`
  - `intersects`
  - `within`
- ✅ Weighting strategies:
  - `proportional`
  - `equal`
  - `none`
- ✅ Automatic quality control (input totals == distributed totals)
- ✅ Exports:
  - CSV (WKT)
  - GeoPackage (GPKG)

---

## Typical Use Cases

- Heat demand declustering  
- Electricity redistribution  
- Population allocation  
- CO₂ distribution  
- Resource balancing  

---

## Input Requirements

### 1️⃣ Source geometries (e.g. cluster points)

CSV containing:
- Geometry column (WKT)
- Cluster ID column

### 2️⃣ Target geometries (e.g. buildings)

CSV containing:
- Geometry column (WKT)
- Weight column (e.g. floor area, initial demand, etc.)

### 3️⃣ Cluster-level values

CSV containing:
- Cluster ID
- One or multiple numeric value columns

---

## Installation

```bash
pip install geopandas pandas shapely
```

---

## Example Usage

```python
from single_spatial_allocation_module import run_spatial_allocation

run_spatial_allocation(
    source_path="input/cluster_points.csv",
    target_path="input/buildings.csv",
    cluster_values_path="input/cluster_demand.csv",

    source_crs="EPSG:25833",
    target_crs="EPSG:4326",
    working_crs="EPSG:25833",

    demand_columns=["VERBR_23", "VERBR_24", "VERBR_25"],

    matching_strategy="nearest",
    weight_strategy="proportional",
)
```

---

## Weighting Strategies

| Strategy        | Description |
|---------------|-------------|
| `proportional` | Distribute proportionally to weight column |
| `equal`        | Equal distribution within cluster |
| `none`         | No weighting (full value per feature) |

---

## Matching Strategies

| Strategy      | Description |
|--------------|------------|
| `nearest`     | Assign to nearest geometry |
| `intersects`  | Spatial intersection |
| `within`      | Containment |

---

## Quality Control

The module verifies that:

- Sum of allocated values per cluster equals input cluster value  
- Errors raise exceptions  

---

## Output

- `result.csv` (semicolon-separated, WKT geometry)
- `result.gpkg` (GeoPackage with allocated values)
