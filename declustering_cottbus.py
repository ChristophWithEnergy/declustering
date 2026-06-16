"""
single_spatial_allocation_module.py

General spatial allocation / declustering pipeline.

What it does:
1. Loads source geometries (e.g. clustered points).
2. Loads target geometries (e.g. buildings).
3. Loads cluster-level values (e.g. energy, population, etc.).
4. Matches source → target (nearest/intersects/within).
5. Computes weights per cluster.
6. Distributes cluster-level values to source features.
7. Performs quality control.
8. Exports CSV + GeoPackage.

Designed to stay:
- Single file
- Simple
- Flexible
- Dataset-agnostic
"""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import wkt


# ---------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------


def detect_delimiter(path: str | Path) -> str:
    text = Path(path).read_text(encoding="utf-8-sig")
    first_line = text.splitlines()[0]
    return ";" if first_line.count(";") > first_line.count(",") else ","


def read_csv_flexible(path: str | Path, sep: str | None = None) -> pd.DataFrame:
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    delimiter = sep or detect_delimiter(path)

    return pd.read_csv(
        StringIO(text),
        sep=delimiter,
        engine="python",
    )


def normalize_id(series: pd.Series) -> pd.Series:
    s = series.astype("string").str.strip()
    s = s.str.replace('"', "", regex=False)
    s = s.str.replace(r"\.0$", "", regex=True)
    return s


def to_number(series: pd.Series) -> pd.Series:
    s = series.astype("string").str.strip()
    s = s.str.replace('"', "", regex=False)
    s = s.str.replace(" ", "", regex=False)
    s = s.str.replace("\u00a0", "", regex=False)
    s = s.str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="raise")


# ---------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------


def load_geometries(
    path: str | Path,
    geometry_column: str,
    crs: str,
    target_crs: str,
    sep: str | None = None,
) -> gpd.GeoDataFrame:

    df = read_csv_flexible(path, sep=sep)
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    if geometry_column not in df.columns:
        raise ValueError(f"Missing geometry column '{geometry_column}'")

    if isinstance(df[geometry_column].iloc[0], str):
        df[geometry_column] = df[geometry_column].apply(wkt.loads)

    gdf = gpd.GeoDataFrame(df, geometry=geometry_column, crs=crs)
    return gdf.to_crs(target_crs)


def load_cluster_values(
    path: str | Path,
    cluster_id_column: str,
    value_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:

    df = read_csv_flexible(path)
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    if cluster_id_column not in df.columns:
        raise ValueError(f"Missing cluster id column '{cluster_id_column}'")

    df[cluster_id_column] = normalize_id(df[cluster_id_column])

    if value_columns is None:
        value_columns = [c for c in df.columns if c != cluster_id_column]

    for col in value_columns:
        df[col] = to_number(df[col])

    return df[[cluster_id_column, *value_columns]], value_columns


# ---------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------


def match_geometries(
    source: gpd.GeoDataFrame,
    target: gpd.GeoDataFrame,
    matching_strategy: str = "nearest",
    max_distance_m: float | None = None,
) -> gpd.GeoDataFrame:

    if matching_strategy == "nearest":
        joined = gpd.sjoin_nearest(
            source,
            target,
            how="left",
            distance_col="distance",
            max_distance=max_distance_m,
        )

    elif matching_strategy == "intersects":
        joined = gpd.sjoin(source, target, how="left", predicate="intersects")

    elif matching_strategy == "within":
        joined = gpd.sjoin(source, target, how="left", predicate="within")

    else:
        raise ValueError(f"Unknown matching_strategy: {matching_strategy}")

    return joined


# ---------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------


def distribute_values(
    gdf: gpd.GeoDataFrame,
    cluster_df: pd.DataFrame,
    cluster_id_column: str,
    value_columns: list[str],
    weight_column: str,
    weight_strategy: str = "proportional",
) -> gpd.GeoDataFrame:

    gdf = gdf.copy()

    gdf[cluster_id_column] = normalize_id(gdf[cluster_id_column])
    cluster_df[cluster_id_column] = normalize_id(cluster_df[cluster_id_column])

    gdf = gdf.merge(
        cluster_df,
        on=cluster_id_column,
        how="left",
        validate="many_to_one",
    )

    gdf[weight_column] = to_number(gdf[weight_column])

    cluster_sum = gdf.groupby(cluster_id_column)[weight_column].transform("sum")
    cluster_count = gdf.groupby(cluster_id_column)[weight_column].transform("count")

    if weight_strategy == "proportional":
        gdf["weight"] = gdf[weight_column] / cluster_sum

    elif weight_strategy == "equal":
        gdf["weight"] = 1.0 / cluster_count

    elif weight_strategy == "none":
        gdf["weight"] = 1.0

    else:
        raise ValueError(f"Unknown weight_strategy: {weight_strategy}")

    # Handle zero sums safely
    zero_mask = cluster_sum <= 0
    if zero_mask.any():
        gdf.loc[zero_mask, "weight"] = 1.0 / cluster_count[zero_mask]

    distributed_columns = []

    for col in value_columns:
        new_col = f"allocated_{col}"
        gdf[new_col] = gdf[col] * gdf["weight"]
        distributed_columns.append(new_col)

    gdf["allocated_mean"] = gdf[distributed_columns].mean(axis=1)

    return gdf


# ---------------------------------------------------------------------
# Quality control
# ---------------------------------------------------------------------


def check_totals(
    result: gpd.GeoDataFrame,
    cluster_id_column: str,
    value_columns: list[str],
    tolerance: float = 1e-6,
) -> None:

    print("Running quality control...")

    for col in value_columns:
        allocated_col = f"allocated_{col}"

        check = (
            result.groupby(cluster_id_column)
            .agg(
                input_value=(col, "first"),
                output_value=(allocated_col, "sum"),
            )
            .reset_index()
        )

        diff = (check["input_value"] - check["output_value"]).abs()

        if (diff > tolerance).any():
            raise ValueError(f"QC failed for column {col}")

        print(f"QC OK for {col}")

    print("Quality control passed.")


# ---------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------


def export_csv(result: gpd.GeoDataFrame, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    df = result.copy()
    df["geometry"] = df.geometry.to_wkt()

    df.to_csv(path, sep=";", decimal=",", index=False, encoding="utf-8")

    print(f"Saved CSV: {path}")


def export_gpkg(
    result: gpd.GeoDataFrame,
    path: str | Path,
    energy_column: str = "allocated_mean",
    layer: str = "allocated",
    crs: str = "EPSG:4326",
) -> None:

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    gdf = result.to_crs(crs).copy()
    gdf["energy_demand"] = gdf[energy_column]

    gdf[["energy_demand", "geometry"]].to_file(
        path,
        layer=layer,
        driver="GPKG",
    )

    print(f"Saved GeoPackage: {path}")


# ---------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------


def run_spatial_allocation(
    source_path: str | Path,
    target_path: str | Path,
    cluster_values_path: str | Path,

    source_geometry_column: str = "geometry",
    target_geometry_column: str = "geometry",

    source_cluster_id_column: str = "cluster_id",
    cluster_id_column: str = "cluster_id",

    weight_column: str = "initial_heat_demand",

    demand_columns: list[str] | None = None,

    source_crs: str = "EPSG:25833",
    target_crs: str = "EPSG:4326",
    working_crs: str = "EPSG:25833",

    matching_strategy: str = "nearest",
    weight_strategy: str = "proportional",
    max_distance_m: float | None = None,

    output_csv: str | Path = "output/result.csv",
    output_gpkg: str | Path = "output/result.gpkg",
) -> gpd.GeoDataFrame:

    print("Loading source geometries...")
    source = load_geometries(
        source_path,
        source_geometry_column,
        source_crs,
        working_crs,
    )

    print("Loading target geometries...")
    target = load_geometries(
        target_path,
        target_geometry_column,
        target_crs,
        working_crs,
    )

    print("Loading cluster values...")
    cluster_df, demand_columns = load_cluster_values(
        cluster_values_path,
        cluster_id_column,
        demand_columns,
    )

    print("Matching geometries...")
    joined = match_geometries(
        source,
        target,
        matching_strategy,
        max_distance_m,
    )

    print("Distributing values...")
    result = distribute_values(
        joined,
        cluster_df,
        source_cluster_id_column,
        demand_columns,
        weight_column,
        weight_strategy,
    )

    print("Checking totals...")
    check_totals(
        result,
        source_cluster_id_column,
        demand_columns,
    )

    print("Exporting results...")
    export_csv(result, output_csv)
    export_gpkg(result, output_gpkg)

    print("Done.")

    return result


# ---------------------------------------------------------------------
# Example execution
# ---------------------------------------------------------------------

if __name__ == "__main__":

    run_spatial_allocation(
        source_path="./input/cluster_points.csv",
        target_path="./input/building.csv",
        cluster_values_path="./input/cluster_demand.csv",

        source_crs="EPSG:25833",
        target_crs="EPSG:4326",
        working_crs="EPSG:25833",

        demand_columns=["VERBR_23", "VERBR_24", "VERBR_25"],

        matching_strategy="nearest",
        weight_strategy="proportional",
    )