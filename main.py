import sys
import re
import warnings
import osmnx as ox
import geopandas as gpd
from unidecode import unidecode
from shapely.errors import ShapelyDeprecationWarning

# Suppress minor geometry warnings from dependencies
warnings.filterwarnings("ignore", category=ShapelyDeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

def generate_svg(osm_id):
    print(f"Fetching bounding geometry for {osm_id}...")
    try:
        # Fetch the main city boundary
        city_gdf = ox.geocode_to_gdf([osm_id], by_osmid=True)
        city_geom = city_gdf.geometry.iloc[0]
    except Exception as e:
        print(f"Error fetching city boundary: {e}")
        sys.exit(1)

    print("Fetching subareas (admin_level=8)...")
    try:
        # Fetch subareas strictly bounded by the city geometry
        wards_gdf = ox.features_from_polygon(city_geom, tags={'admin_level': '8'})
    except Exception as e:
        print(f"Error fetching subareas: {e}")
        sys.exit(1)

    # Filter out Nodes and Points. Keep only Polygons and MultiPolygons.
    wards_gdf = wards_gdf[wards_gdf.geometry.type.isin(['Polygon', 'MultiPolygon'])]

    # Spatial filter: Ensure the subarea truly belongs to this city.
    wards_gdf = wards_gdf[wards_gdf.geometry.representative_point().within(city_geom)]

    # Spatial filter: Ensure the subarea truly belongs to this city.
    # Checks if the representative point of the ward falls inside the city boundary, 
    # filtering out neighboring city wards that just overlap the bounding box.
    wards_gdf = wards_gdf[wards_gdf.geometry.representative_point().within(city_geom)]

    print("Fetching water areas for clipping...")
    
#    water_tags = {
#        'natural' : ['bay','strait', 'coastline'],
#        'place' : ['sea']
#    }
#    try:
#        water_gdf = ox.features_from_polygon(city_geom, tags=water_tags)
#        water_geom = water_gdf.union_all()
        
        # Subtract the water bodies from the ward polygons
#        wards_gdf.geometry = wards_gdf.geometry.difference(water_geom)
#    except Exception as e:
#        print(f"Note: Could not perfectly process water areas ({e}). Proceeding with unclipped bounds.")
    land_tags = {'landuse': True, 'natural': 'wood', 'place': 'island'}
    try:
        land_features = ox.features_from_polygon(city_geom, tags=land_tags)
        # We also add the city boundary itself, but we need to be careful.
        # Most reliable: Intersect with a global land polygon or the 'boundary=postal_code'
        land_geom = land_features.union_all()
        
        # Intersect wards with the land (Keep only the parts of wards that are on land)
        wards_gdf.geometry = wards_gdf.geometry.intersection(land_geom)
    except Exception as e:
        print(f"Land mask failed, falling back to basic clip: {e}")

# Project geometries to Web Mercator (EPSG:3857) to ensure accurate aspect ratios for drawing
    wards_gdf = wards_gdf.to_crs(epsg=3857)
    minx, miny, maxx, maxy = wards_gdf.total_bounds
    
    # Calculate dimensions
    svg_width = 1000
    svg_height = svg_width * ((maxy - miny) / (maxx - minx))

    def polygon_to_svg_path(poly):
        """Converts Shapely polygons into SVG path 'd' strings."""
        def coords_to_path(coords):
            path = []

            for i, (x, y) in enumerate(coords):
                px = (x - minx) / (maxx - minx) * svg_width
                py = svg_height - (y - miny) / (maxy - miny) * svg_height
                prefix = "M" if i == 0 else "L"
                path.append(f"{prefix} {px:.2f} {py:.2f}")
            path.append("Z")
            return " ".join(path)

        if poly.is_empty:
            return ""
        if poly.geom_type == 'Polygon':
            d = coords_to_path(poly.exterior.coords)
            for interior in poly.interiors:
                d += " " + coords_to_path(interior.coords)
            return d
        elif poly.geom_type == 'MultiPolygon':
            return " ".join(polygon_to_svg_path(p) for p in poly.geoms)
        return ""

    print("Constructing SVG elements...")
    svg_elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {svg_width:.2f} {svg_height:.2f}">',
        '''<style>
            .ward { fill: #ffffff; stroke: #000000; stroke-width: 2px; }
            .label { font-family: sans-serif; font-size: 14px; text-anchor: middle; fill: #000000; pointer-events: none; }
        </style>''',
        '<g id="wards">'
    ]

    labels_elements = ['<g id="labels">']

    for idx, row in wards_gdf.iterrows():
        geom = row.geometry
        if geom.is_empty: 
            continue

        # Extract names from OpenStreetMap data
        name_jp = row.get('name', 'Unknown')
        name_en = row.get('name:en', str(name_jp))
        if not isinstance(name_en, str) or not name_en:
            name_en = name_jp
		# Generate a clean ID: drop diacritics (Kōhoku -> Kohoku), remove 'Ward', keep alphanumeric
        clean_id = unidecode(str(name_en))
        clean_id = re.sub(r'(?i)\bward\b', '', clean_id)
        clean_id = re.sub(r'[^a-zA-Z0-9]', '', clean_id)
        if not clean_id: 
            clean_id = f"ward_{idx}"
        path_d = polygon_to_svg_path(geom)
        svg_elements.append(f'    <path id="{clean_id}" class="ward" d="{path_d}" />')

        # Place the label precisely at the visual center of the subarea
        centroid = geom.centroid
        cx = (centroid.x - minx) / (maxx - minx) * svg_width
        cy = svg_height - (centroid.y - miny) / (maxy - miny) * svg_height
        labels_elements.append(f'    <text x="{cx:.2f}" y="{cy:.2f}" class="label">{name_jp}</text>')

    svg_elements.append('</g>')
    labels_elements.append('</g>')
    
    svg_elements.extend(labels_elements)
    svg_elements.append('</svg>')

    filename = f"{osm_id}.svg"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write("\n".join(svg_elements))
    
    print(f"Complete. SVG exported to {filename}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python main.py <OSM_ID>")
        print("Example: python main.py R2689482")
        sys.exit(1)
    
    # Accept standard relations with or without 'R' prefix
    osm_id = sys.argv[1].upper()
    if not osm_id.startswith('R'):
        osm_id = f"R{osm_id}"
        
    generate_svg(osm_id)
