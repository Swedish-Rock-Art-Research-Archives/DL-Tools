import arcpy
from glob import glob
import os

import pandas as pd
import shutil

from arcpy.management import SplitRaster
from arcpy.analysis import Clip

arcpy.env.addOutputsToMap = False
arcpy.CheckOutExtension("ImageAnalyst")

# Create any missing directories for the first processing stage and padded images
def create_dirs(output_dir,padded_dir,split_data):
    splits = ['images','labels','shapes','tile_extents','bounding_geom']
    subdirs = ['train','val','test']

    for split in splits:
        if split_data: 
            for subdir in subdirs:
                if not os.path.exists(os.path.join(output_dir,split,subdir)):
                    os.makedirs(os.path.join(output_dir,split,subdir))
                if not os.path.exists(os.path.join(padded_dir,split,subdir)):
                    os.makedirs(os.path.join(padded_dir,split,subdir))
                if not os.path.exists(os.path.join(padded_dir,'tiffs',subdir)):
                        os.makedirs(os.path.join(padded_dir,'tiffs',subdir))
                if not os.path.exists(os.path.join(padded_dir,'blank_imgs',subdir)):
                    os.makedirs(os.path.join(padded_dir,'blank_imgs',subdir))
        else:
            if not os.path.exists(os.path.join(output_dir,split)):
                os.makedirs(os.path.join(output_dir,split))
            if not os.path.exists(os.path.join(padded_dir,split)):
                os.makedirs(os.path.join(padded_dir,split))
            if not os.path.exists(os.path.join(padded_dir,'tiffs')):
                    os.makedirs(os.path.join(padded_dir,'tiffs'))
            if not os.path.exists(os.path.join(padded_dir,'blank_imgs')):
                    os.makedirs(os.path.join(padded_dir,'blank_imgs'))

# Tile large rasters to user-input dimensions
def tile_raster(img_file,out_img_dir,tile_size,overlap,base_name):
    SplitRaster(img_file,out_img_dir,base_name,"SIZE_OF_TILE",
        "PNG","BILINEAR","#",f"{tile_size} {tile_size}",overlap
        )
# Clip the label polygons for the full image to the extent of each tile, then make them rectangular again to comply with YOLO's OBB standard
def clip_shapes(shp_file,out_img_dir,tile_img,ext_dir,out_bound):
    out_extent = tile_img.replace(out_img_dir,ext_dir).replace('.png','_EXTENT.shp')
    tile_extent = arcpy.ddd.RasterDomain(tile_img, out_extent, "POLYGON")
    Clip(
        in_features=shp_file,
        clip_features=tile_extent,
        out_feature_class=shp_file.replace(shape_dir,out_shp_dir),
        cluster_tolerance=None
    )
    
    arcpy.management.MinimumBoundingGeometry(
        in_features=shp_file.replace(shape_dir,out_shp_dir),
        out_feature_class=out_bound,
        geometry_type="RECTANGLE_BY_AREA",
        group_option="NONE",
        group_field=None,
        mbg_fields_option="NO_MBG_FIELDS"
    )

# Pad the tiled image to the extent of the recalculated polygons using data from the original image
def pad_tile(full_img,tile_file,base_name,shp_bound_dir,out_file,pad_dir):
    try:
        geom_file=f"{shp_bound_dir}/_{base_name}.shp"
        geom_layer = arcpy.Describe(geom_file)
    except:
        geom_file=f"{shp_bound_dir}/{base_name}.shp"
        geom_layer = arcpy.Describe(geom_file)

    raster_layer = arcpy.Describe(tile_file)
    
    rast_ext = raster_layer.extent
    geom_ext = geom_layer.extent
    
    xmin = min([rast_ext.XMin,geom_ext.XMin])
    xmax = max([rast_ext.XMax,geom_ext.XMax])
    
    ymin = min([rast_ext.YMin,geom_ext.YMin])
    ymax = max([rast_ext.YMax,geom_ext.YMax])
    
    
    with arcpy.EnvManager(extent=f'{xmin} {ymin} {xmax} {ymax} PROJCS["WGS_1984_Web_Mercator_Auxiliary_Sphere",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],PROJECTION["Mercator_Auxiliary_Sphere"],PARAMETER["False_Easting",0.0],PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",0.0],PARAMETER["Standard_Parallel_1",0.0],PARAMETER["Auxiliary_Sphere_Type",0.0],UNIT["Meter",1.0]]'):
        arcpy.ia.Con(
            in_conditional_raster=tile_file,
            in_true_raster_or_constant=tile_file,
            in_false_raster_or_constant=full_img,
            where_clause="VALUE IS NOT NULL"
        ).save(f"{out_file}.tif")
    
        arcpy.conversion.RasterToOtherFormat(
            Input_Rasters=f"{out_file}.tif",
            Output_Workspace=pad_dir,
            Raster_Format="PNG"
        )

#  Convert the polygons to YOLO OBB text label files
def create_labels(img_file,min_area,padded_img_dir,blank_img_dir,geom_file,out_lbl_dir,base_name):
    raster_layer = arcpy.Describe(img_file)
    label_file = f"{out_lbl_dir}/{base_name}.txt"

    raster_layer = arcpy.Describe(img_file)
    rast_ext = raster_layer.extent
    
    xmin = rast_ext.XMin
    xmax = rast_ext.XMax
    ymin = rast_ext.YMin
    ymax = rast_ext.YMax

    width = (xmax-xmin)
    height = (ymax-ymin)

    rast_ext.XMin = 0
    rast_ext.XMax = width
    rast_ext.YMin = 0
    rast_ext.YMax = height

    new_xmin = rast_ext.XMin
    new_ymin = rast_ext.YMin

    width = (xmax-xmin)
    height = (ymax-ymin)

    x_shift = new_xmin-xmin
    y_shift = new_ymin-ymin

    new_data=[]
    if int(arcpy.management.GetCount(geom_file)[0]) > 0:
        for row in arcpy.da.SearchCursor(geom_file, ["OID@", "SHAPE@", "SHAPE@AREA", "class_id"]):
            if row[2]>int(min_area):
                for part in reversed(row[1]):
                    new_coords =[]
                    for pnt in part[:-1]:
                        x=(pnt.X+x_shift)/width
                        y=1-((pnt.Y+y_shift)/height)
                        new_coords.extend([x,y])

                out_data=[int(row[3])]+new_coords
                new_data.append(out_data)
        if len(new_data)>0:
            df=pd.DataFrame.from_dict(new_data)
            df.to_csv(label_file,header=None, index=False, sep=' ')
        else:
            shutil.move(img_file,img_file.replace(padded_img_dir,blank_img_dir))

    else:
        shutil.move(img_file,img_file.replace(padded_img_dir,blank_img_dir)) 


if __name__ == "__main__":
    working_dir = arcpy.GetParameterAsText(0)
    main_img_dir = arcpy.GetParameterAsText(1)
    shape_dir = arcpy.GetParameterAsText(2)
    tile_size = arcpy.GetParameterAsText(3)
    overlap = arcpy.GetParameterAsText(4)
    split = arcpy.GetParameterAsText(5)
    min_area = arcpy.GetParameterAsText(6)

    arcpy.AddMessage(split)
 

    output_dir = f"{working_dir}/yolo_obb_tiled_{tile_size}sz_{overlap}ov/"
    out_img_dir = f"{output_dir}/images"
    out_label_dir = f"{output_dir}/labels"
    out_shp_dir = f"{output_dir}/shapes"
    out_ext_dir = f"{output_dir}/tile_extents" 
    out_bound_dir = f"{output_dir}/bounding_geom"

    padded_dir = f"{working_dir}/yolo_obb_tiled_{tile_size}sz_{overlap}ov_PADDED/"
    padded_img_dir = f"{padded_dir}/images"
    padded_tif_dir =f"{padded_dir}/tiffs"
    padded_label_dir = f"{padded_dir}/labels"

    blank_img_dir = f"{padded_dir}/blank_imgs"
    create_dirs(output_dir,padded_dir,split)
    arcpy.AddMessage('Running script tool...')
    img_dir = f"{main_img_dir}/*/*" if split else f"{main_img_dir}/*"
    for file in glob(img_dir):
        if str(os.path.splitext(file)[1]).lower() in ['.png','.jpg','.jpeg','.tif','.tiff']:
            # arcpy.AddMessage(file)
            if split == 'true':
                split_val = file.split('//')[-1].split('\\')[-2]
            else:
                split_val=''
            base_img_dir = f"{out_img_dir}/{split_val}"

            arcpy.AddMessage(base_img_dir)

            base_name = os.path.splitext(os.path.split(file)[-1])[0]
            tile_raster(file,base_img_dir,tile_size,overlap,base_name)
            arcpy.AddMessage(base_name)
            for tile in glob(f"{base_img_dir}/{base_name}*.png"):
                    
                tile_name = os.path.splitext(os.path.split(tile)[0])[0]
                shp_file = f"{shape_dir}/{split_val}/{base_name}.shp"
                shp_bound_dir = f"{out_bound_dir}/{split_val}"
                out_file=f"{padded_tif_dir}/{split_val}/{base_name}"
                ext_dir=f"{out_ext_dir}/{split_val}"
                pad_dir = f"{padded_img_dir}/{split_val}"
                lbl_dir= f"{padded_label_dir}/{split_val}"
                
                

                arcpy.AddMessage(tile_name)
                arcpy.AddMessage(shp_file)
                
                clip_shapes(shp_file,base_img_dir,tile,ext_dir,out_bound=f"{shp_bound_dir}/{base_name}.shp")
                pad_tile(file,tile,base_name,shp_bound_dir,out_file,pad_dir)
                create_labels(tile,min_area,padded_img_dir,blank_img_dir,shp_file,lbl_dir,base_name)