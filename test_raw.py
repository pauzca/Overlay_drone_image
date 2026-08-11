from dialog import get_metadata_reader

from .drone_raw_utils.find_drone_image import find_best_raw_drone_image
from .drone_raw_utils.orthorectify import orthorectify_image
from .drone_raw_utils.align_aux import align_to_ortho

import os

# PARAMETERS YOU NEED TO SET UP, YOU WOULD DO THIS FROM THE UI IN THE PLUGUN 

tmp_dir = ""
drone_model = "DJI Mavic 3"  
image_folder = r"/home/paula/Documentos/test_trinity"
target_x = 0
target_y = 0
epsg = 32721
output_path = r"/home/paula/Documentos/test_trinity/output.tif"
dsm_path = r"/home/paula/Documentos/test_trinity/dsm.tif"
geotiff_path = r"/home/paula/Documentos/test_trinity/geotiff.tif"


# set the reader of the raw images metadata based on the drone/camera model
reader = get_metadata_reader(drone_model)

# Steep 0 ── find or create the lookup csv file for the raw image coordinates ──────────────────────────────────────────────
csv_coordinate_path = os.path.join(image_folder, "coordinates",
                                    "all_images_center_coordinates.csv")

if os.path.exists(csv_coordinate_path):
    print("Loading precomputed coordinates")
else:
    print("Creating coordinates file... The first time this can take a couple of minutes")
    csv_coordinate_path = reader.extract_gps_to_csv(image_folder=image_folder, output_csv=csv_coordinate_path)



# Step 1 – find the best image
img_path = find_best_raw_drone_image(
    csv_file=csv_coordinate_path,
    image_folder=image_folder,
    output_folder=tmp_dir,
    target_x=target_x,
    target_y=target_y,
    epsg=epsg,
    metadata_reader=reader,
    n_images=1
)

img_name = os.path.splitext(os.path.basename(str(img_path)))
print(f"Drone image {img_name}")

# Step 2 – orthoproject

geotiff_path = output_path.replace(".tif", "_temp.tif") #os.path.join(tmp_dir, img_name + ".tif")
orthorectify_image(
    image_path=img_path,
    dsm_path=dsm_path,
    geotiff_path=geotiff_path,
    metadata_reader=reader
)

# Step 3 – align
align_to_ortho(
    orthoprojected_path=geotiff_path,
    ortho_path=output_path,
    output_path=output_path,
    crop_size=25,
)
