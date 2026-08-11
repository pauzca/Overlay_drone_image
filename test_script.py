import os
import xml.etree.ElementTree as ET

from drone_raw_utils.align_aux import align_to_ortho
from drone_raw_utils.orthorectify import orthorectify_image
from raw_metadata_reader import CustomMetadataReader, DJImetadata, TrinityMetadata

drone_model = "Trinity"  #"DJI Mavic 3"
mission_folder = r"overlay_drone_resources/images/test_trinity_10_02_2026"
image_folder = os.path.join(mission_folder, "images_raw")
target_x, target_y = 0, 0 
epsg = 32721


dsm_path = r"BCI_50ha_2024_11_12_dsm.tif"
ortho_path =  r"BCI_50ha_2024_11_12_orthomosaic_render.tif"


# set the reader of the raw images metadata based on the drone/camera model
#reader = get_metadata_reader(drone_model)

reader = TrinityMetadata()#CustomMetadataReader() 

img1 = os.listdir(image_folder)[0]
print(img1)
metadata_img1 = reader._read_exif(os.path.join(image_folder, img1))

print(metadata_img1)


metadata_img1 = reader._read_xmp(os.path.join(image_folder, img1))

print(metadata_img1)

metadata_img1 = reader.read(os.path.join(image_folder, img1))

print(metadata_img1)
raise
print("Metadata read successfully for image:", img1)
print(metadata_img1)

# Steep 0 ── find or create the lookup csv file for the raw image coordinates ──────────────────────────────────────────────
csv_coordinate_path = os.path.join(mission_folder, "coordinates",
                                    "all_images_center_coordinates.csv")

if os.path.exists(csv_coordinate_path):
    print("Loading precomputed coordinates")
else:
    print("Creating coordinates file... The first time this can take a couple of minutes")
    csv_coordinate_path = reader.extract_gps_to_csv(image_folder=image_folder, output_csv=csv_coordinate_path)

images_do = os.listdir(image_folder)
# orthoproject
for img_path in images_do:
    img_path = os.path.join(image_folder, img_path)

    projected_path = os.path.join(mission_folder, "orthoprojected", os.path.splitext(os.path.basename(img_path))[0] + "_orthoprojected.tif")
    os.makedirs(os.path.dirname(projected_path), exist_ok=True)
    try:
        orthorectify_image(
            image_path=img_path,
            dsm_path=dsm_path,
            geotiff_path=projected_path,
            metadata_reader=reader
        )
    except Exception as e:
        print(f"Error orthorectifying image {img_path}: {e}")

    output_path = os.path.join(mission_folder, "aligned", os.path.splitext(os.path.basename(img_path))[0] + "_aligned.tif")
    try:
        align_to_ortho(
            orthoprojected_path=projected_path,
            ortho_path=ortho_path,
            output_path=output_path,
            crop_size=25,
        )
    except Exception as e:
        print(f"Error aligning image {projected_path}: {e}")



