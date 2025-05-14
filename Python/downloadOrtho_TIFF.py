import requests
import os
from urllib.parse import quote_plus
import rasterio
from rasterio.mask import mask
import geopandas as gpd
import json
import numpy as np
import affine

def download_tiff(bbox, image_sr, output_path, layers=None, size="4096,4096", clip_geometry=None, transparent_color=None, wms_url=None):
    """
    Downloads a  image from the NYS orthoimagery ArcGIS REST API export operation,
    with optional clipping and transparency.  Now supports both ArcGIS REST export and WMS.

    Args:
        bbox (str): The bounding box of the area to download (e.g., "-74.25,40.5,-73.75,41.0").
        image_sr (str): The spatial reference system WKID (e.g., "4326" for WGS 84).
        output_path (str): The path to save the downloaded image.
        layers (str, optional):  A comma-separated list of layer IDs to include (e.g., "0,1,2").
            If None, all layers are included.  Defaults to None.
        size (str, optional): The width and height of the image in pixels.
            Defaults to "4096,4096" (the maximum allowed by the service).
            Note:  The service has a maximum image size of 4096x4096 pixels.
        clip_geometry (geojson, optional): A GeoJSON geometry object used to clip the raster.
            If provided, the raster will be clipped to this geometry. Defaults to None.
        transparent_color (tuple, optional):  A tuple of RGB values (e.g., (255, 255, 255) for white)
            to make transparent.  Defaults to None.
        wms_url (str, optional):  The URL of the WMS service.  If provided, the function will use WMS instead of ArcGIS REST.
            Defaults to None.
    """
    if not output_path.lower().endswith(('.png', '.tif', '.tiff')):
        output_path = output_path + '.png'  # Default to PNG if no extension

    try:
        if wms_url:  # Handle WMS request
            width, height = map(int, size.split(','))
            params = {
                'service': 'WMS',
                'version': '1.1.1',  # Or your WMS version
                'request': 'GetMap',
                'layers': ','.join(map(str, layers)) if layers else '',  # MUST have layers
                'styles': '',  # Or specify styles if needed
                'bbox': bbox,
                'width': width,
                'height': height,
                'srs': image_sr,  #  Use the provided image_sr
                'format': 'image/png',  #  PNG is common,  use image/tiff if you want tiff.
                'transparent': 'TRUE' if transparent_color else 'FALSE'
            }
            full_url = f"{wms_url}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
            print(f"Request URL: {full_url}")  # Print the full URL for debugging
            response = requests.get(wms_url, params=params, stream=True)
            response.raise_for_status()

            temp_file = output_path + ".tmp"
            with open(temp_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            try:
                with rasterio.open(temp_file) as src:
                    out_image = src.read()
                    out_transform = src.transform
                    out_crs = src.crs

                    if clip_geometry:
                        try:
                            if not (isinstance(clip_geometry, dict) and "type" in clip_geometry and "coordinates" in clip_geometry):
                                raise ValueError("Invalid clip_geometry format.  Must be a GeoJSON-like dictionary with 'type' and 'coordinates' keys.")
                            gdf = gpd.GeoDataFrame.from_features([clip_geometry], crs=image_sr)  # Use image_sr
                            clip_bounds = gdf.geometry.values[0]
                            out_image, out_transform = mask(src, [clip_bounds], crop=True)
                            out_meta = src.meta.copy()
                            out_meta.update({
                                "driver": "PNG",  # WMS output is often PNG
                                "height": out_image.shape[1],
                                "width": out_image.shape[2],
                                 "transform": out_transform,
                                "crs": out_crs
                            })
                        except Exception as e:
                            raise  # Re-raise the exception to be caught in the outer block
                    else:
                         out_meta = src.meta.copy()
                         out_meta.update({
                            "driver": "PNG",  # WMS output is often PNG
                            "height": out_image.shape[1],
                            "width": out_image.shape[2],
                            "transform": out_transform,
                            "crs": out_crs
                        })

                    if transparent_color:
                        out_image = make_transparent(out_image, transparent_color)
                        out_meta.update({"count": 4})

                    with rasterio.open(output_path, "w", **out_meta) as dest:
                        dest.write(out_image)
                print(f"Image downloaded successfully to: {output_path}")
            except rasterio.errors.RasterioIOError as e:
                print(f"Error processing image: {e}")
                print(f"Error details: {e}")
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                return
            except Exception as e:
                print(f"Error processing image: {e}")
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                return

            finally:
                if os.path.exists(temp_file):
                    os.remove(temp_file)

        else: #  ArcGIS REST API
            base_url = "https://orthos.its.ny.gov/arcgis/rest/services/wms/Latest/MapServer/export"
            params = {
                'bbox': bbox,
                'size': size,
                'imageSR': image_sr,
                'format': 'PNG32',  # Changed to PNG32 for better quality
                'f': 'image'
            }
            if layers:
                params['layers'] = f"show:{layers}"
            full_url = f"{base_url}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
            print(f"Request URL: {full_url}")
            response = requests.get(base_url, params=params, stream=True)
            response.raise_for_status()  # Raise an exception for bad status codes

            # Save the downloaded image to a temporary file
            temp_file = output_path + ".tmp"
            with open(temp_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Process the image with rasterio for clipping and transparency
            try:
                with rasterio.open(temp_file) as src:
                    out_image = src.read()
                    out_transform = src.transform
                    out_crs = src.crs
                    if clip_geometry:
                        try:
                            # Load the clip geometry
                            # Check if the clip_geometry has the required structure.
                            if not (isinstance(clip_geometry, dict) and "type" in clip_geometry and "coordinates" in clip_geometry):
                                raise ValueError("Invalid clip_geometry format.  Must be a GeoJSON-like dictionary with 'type' and 'coordinates' keys.")

                            gdf = gpd.GeoDataFrame.from_features([clip_geometry], crs=image_sr)  # Use image_sr
                            clip_bounds = gdf.geometry.values[0]
                            out_image, out_transform = mask(src, [clip_bounds], crop=True)
                            out_meta = src.meta.copy()
                            out_meta.update({
                                "driver": "PNG",  # Ensure output is PNG for transparency
                                "height": out_image.shape[1],
                                "width": out_image.shape[2],
                                "transform": out_transform,
                                "crs": out_crs
                            })

                            if transparent_color:
                                 out_image = make_transparent(out_image, transparent_color)
                            with rasterio.open(output_path, "w", **out_meta) as dest:
                                dest.write(out_image)

                        except Exception as e:
                            raise # re-raise
                    elif transparent_color:  # Only transparency
                        out_image = make_transparent(src.read(), transparent_color)
                        out_meta = src.meta.copy()
                        out_meta.update({
                            "driver": "PNG",  # Ensure output is PNG for transparency
                            "dtype": 'uint8',
                            "crs": out_crs
                        })
                        with rasterio.open(output_path, "w", **out_meta) as dest:
                            dest.write(out_image)
                    else:
                        #If no clip and no transparency just copy
                        with rasterio.open(output_path, "w", **src.meta) as dest:
                            dest.write(src.read())
                print(f"Image downloaded successfully to: {output_path}")
            except rasterio.errors.RasterioIOError as e:
                print(f"Error processing image: {e}")
                print(f"Error details: {e}")
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                return
            except Exception as e:
                print(f"Error processing image: {e}")
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                return
            finally:
                # Clean up the temporary file
                if os.path.exists(temp_file):
                    os.remove(temp_file)

    except requests.exceptions.RequestException as e:
        print(f"Error downloading image: {e}")
        print(f"URL used: {response.url}")
    except rasterio.RasterioIOError as e:
        print(f"Error opening or writing raster file: {e}")



def make_transparent(image_data, transparent_color=(255, 255, 255)):
    """
    Makes a specified color transparent in a raster image.

    Args:
        image_data (numpy.ndarray):  A 3D numpy array representing the image data (bands x height x width).
        transparent_color (tuple, optional): A tuple of RGB values (e.g., (255, 255, 255) for white)
            to make transparent.  Defaults to (255, 255, 255) (white).

    Returns:
        numpy.ndarray: A 3D numpy array with an alpha channel, where the specified color is transparent.
    """
    import numpy as np

    r, g, b = transparent_color
    alpha = np.where(
        (image_data[0] == r) & (image_data[1] == g) & (image_data[2] == b),
        0,
        255
    ).astype(np.uint8)
    #rgba = np.concatenate((image_data, alpha[np.newaxis, :, :])) # Original
    rgba = np.concatenate((image_data, alpha[np.newaxis, :, :])).astype(np.uint8)
    return rgba

def download_raw_data(bbox, output_path, layers=None):
    """
    Downloads raw vector data from the NYS orthoimagery ArcGIS REST API using the query operation.

    Args:
        bbox (str): The bounding box of the area to download (e.g., "-74.25,40.5,-73.75,41.0").
        output_path (str): The path to save the downloaded data (as a GeoJSON file).
        layers (str, optional): A comma-separated list of layer IDs to include (e.g., "0,1,2").
            If None, all layers are included. Defaults to None.
    """
    base_url = "https://orthos.its.ny.gov/arcgis/rest/services/wms/Latest/MapServer/query"
    # Construct the query URL.  It's a POST request.
    geometry = bbox
    geometry_type = "esriSRSPolygon"
    spatial_rel = "esriSpatialRelIntersects" # Corrected variable name
    out_fields = "*"
    return_geometry = "true"
    out_sr = "4326"  #WGS 84

    params = {
        "where": "1=1",
        "geometry": geometry,
        "geometryType": geometry_type,
        "spatialRel": spatial_rel, # Corrected variable name
        "outFields": out_fields,
        "returnGeometry": return_geometry,
        "outSR": out_sr,
        "f": "geojson",
    }
    if layers:
        params['layer'] = layers

    # Add this check
    if not output_path.lower().endswith('.geojson'):
        output_path = output_path + '.geojson'

    try:
        response = requests.post(base_url, data=params) # Changed to POST
        response.raise_for_status()
        data = response.json()

        # Save the data as GeoJSON
        with open(output_path, 'w') as f:
            json.dump(data, f)
        print(f"Raw data downloaded successfully to {output_path}")

    except requests.exceptions.RequestException as e:
        print(f"Error downloading raw data: {e}")
        print(f"URL used: {response.url}")
    except json.JSONDecodeError:
        print("Error: Invalid JSON response from server.")

def get_layer_info(service_url):
    """
    Retrieves layer information (IDs and names) from an ArcGIS MapServer.

    Args:
        service_url (str): The URL of the ArcGIS MapServer.

    Returns:
        dict: A dictionary where keys are layer IDs (integers) and values are layer names (strings).
              Returns None on error.
    """
    #url = f"{service_url}?f=json" # removed
    url = f"{service_url}/layers?f=json" # added
    try:
        response = requests.get(url)
        response.raise_for_status()
        # Check if the response is valid JSON
        try:
            data = response.json()
        except json.JSONDecodeError:
            print("Error: Server returned non-JSON response.")
            print(f"Response text: {response.text}")  # Print the response text for debugging
            return None

        if "layers" in data:
            layer_info = {layer["id"]: layer["name"] for layer in data["layers"]}
            return layer_info
        else:
            print("Error: 'layers' key not found in the response.")
            return None

    except requests.exceptions.RequestException as e:
        print(f"Error retrieving layer information: {e}")
        return None



if __name__ == "__main__":
    # Example usage
    # Get layer info
    service_url = "https://orthos.its.ny.gov/arcgis/rest/services/wms/Latest/MapServer" # Changed URL
    layer_info = get_layer_info(service_url)
    if layer_info:
        print("Available Layers:")
        for layer_id, layer_name in layer_info.items():
            print(f"  ID: {layer_id}, Name: {layer_name}")

    output_folder = "downloaded_maps"
    os.makedirs(output_folder, exist_ok=True)

    # Example parameters - adjust these based on your requirements
    my_bbox = "-74.01,40.71,-74.00,40.72"  # Example: New York City
    my_image_sr = "EPSG:3857"  #  Use EPSG:3857 for this WMS
    size = "4096,4096"
    clip_geometry = {  # Example: a simple polygon for clipping
        "type": "Polygon",
        "coordinates": [
            [
                [-74.015, 40.715],
                [-74.005, 40.715],
                [-74.005, 40.705],
                [-74.015, 40.705],
                [-74.015, 40.715]
            ]
        ]
    }
    layers_to_download = [0, 1, 2]  # Specify the layers you want

    # 1. Download and clip the image
    output_filename_clipped = os.path.join(output_folder, "wms_map_clipped") # Removed .png
    download_tiff(my_bbox, my_image_sr, output_filename_clipped, layers=layers_to_download, size=size, clip_geometry=clip_geometry, wms_url=service_url)

    # 2. Download the image with a transparent background
    output_filename_transparent = os.path.join(output_folder, "wms_map_transparent") # Removed .png
    download_tiff(my_bbox, my_image_sr, output_filename_transparent, layers=layers_to_download, size=size, transparent_color=(255, 255, 255), wms_url=service_url)  # White background

    # 3. Download the raw data (This part remains the same, as it's a different endpoint)
    output_filename_raw = os.path.join(output_folder, "my_map_data")
    download_raw_data(my_bbox, output_filename_raw, layers=layers_to_download)
