from flask import Flask, request, jsonify
from PyPDF2 import PdfReader
import fitz  # PyMuPDF
import os

app = Flask(__name__)

def analyze_pdf(file_path):
    result = {
        "content_color_modes": [],
        "declared_color_spaces": [],
        "document_color_mode": "Unknown",
        "fonts_enclosed": False,
        "has_cut_contour_layer": False,
        "images_embedded": 0,
        "images_linked": 0,
        "images_low_dpi": 0,
        "image_list": [],
        "layers": False,
        "mode_conflict": False,
        "vector_list": [],  # New list to store vector details
        "warnings": []
    }

    # PyPDF2 analysis for fonts and layers
    try:
        reader = PdfReader(file_path)
        
        is_all_fonts_embedded = True
        if reader.pages:
            for page in reader.pages:
                for font in page.extract_fonts():
                    if not font.embedded:
                        is_all_fonts_embedded = False
                        break
                if not is_all_fonts_embedded:
                    break
        result["fonts_enclosed"] = is_all_fonts_embedded
        
        result["layers"] = any(page.get("/OCProperties") for page in reader.pages) if reader.pages else False
    except Exception as e:
        result["warnings"].append(f"PyPDF2 analysis failed: {str(e)}")

    # PyMuPDF analysis for all content types
    try:
        doc = fitz.open(file_path)
        
        found_color_modes = set()
        image_details = []
        vector_details = []

        for page_index, page in enumerate(doc):
            # Check for colors in images
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    
                    color_mode = "Unknown"
                    if pix.colorspace.name in ("DeviceCMYK", "CMYK"):
                        color_mode = "CMYK"
                    elif pix.colorspace.name in ("DeviceRGB", "RGB"):
                        color_mode = "RGB"
                    elif pix.colorspace.name in ("DeviceGray", "Gray"):
                        color_mode = "Grayscale"
                    else:
                        color_mode = "Other"
                    
                    found_color_modes.add(color_mode)
                    result["images_embedded"] += 1
                    
                    dpi = pix.xres
                    if dpi < 150:
                        result["images_low_dpi"] += 1
                    
                    image_details.append({
                        "page": page_index + 1,
                        "color_mode": color_mode,
                        "dpi": dpi,
                        "is_low_dpi": dpi < 150
                    })
                    
                    pix = None
                except Exception as img_e:
                    result["warnings"].append(f"Failed to analyze image with xref {xref}: {str(img_e)}")

            # Check for colors in vector graphics and text
            for drawing in page.get_drawings():
                line_color_mode = "Unknown"
                fill_color_mode = "Unknown"

                # Check line color
                if 'line_info' in drawing and 'cs_name' in drawing['line_info']:
                    cs_name = drawing['line_info']['cs_name']
                    if cs_name in ("DeviceCMYK", "CMYK"):
                        line_color_mode = "CMYK"
                    elif cs_name in ("DeviceRGB", "RGB"):
                        line_color_mode = "RGB"
                    elif cs_name in ("DeviceGray", "Gray"):
                        line_color_mode = "Grayscale"
                
                # Check fill color
                if 'fill_info' in drawing and 'cs_name' in drawing['fill_info']:
                    cs_name = drawing['fill_info']['cs_name']
                    if cs_name in ("DeviceCMYK", "CMYK"):
                        fill_color_mode = "CMYK"
                    elif cs_name in ("DeviceRGB", "RGB"):
                        fill_color_mode = "RGB"
                    elif cs_name in ("DeviceGray", "Gray"):
                        fill_color_mode = "Grayscale"
                
                # Add unique modes to the main set
                if line_color_mode != "Unknown": found_color_modes.add(line_color_mode)
                if fill_color_mode != "Unknown": found_color_modes.add(fill_color_mode)
                
                vector_details.append({
                    "page": page_index + 1,
                    "type": drawing['type'],
                    "line_color_mode": line_color_mode,
                    "fill_color_mode": fill_color_mode
                })

        doc.close()
        
        result["image_list"] = image_details
        result["vector_list"] = vector_details
        result["content_color_modes"] = list(found_color_modes)
        
    except Exception as e:
        result["warnings"].append(f"PyMuPDF analysis failed: {str(e)}")

    # Determine overall document color mode
    unique_modes = list(set(result["content_color_modes"]))
    if "CMYK" in unique_modes and "RGB" in unique_modes:
        result["mode_conflict"] = True
        result["document_color_mode"] = "Mixed"
    elif "CMYK" in unique_modes:
        result["document_color_mode"] = "CMYK"
    elif "RGB" in unique_modes:
        result["document_color_mode"] = "RGB"
    elif "Grayscale" in unique_modes:
        result["document_color_mode"] = "Grayscale"
    else:
        result["document_color_mode"] = "Unknown"

    return result

@app.route("/analyze", methods=["POST"])
def analyze():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    temp_path = os.path.join("/tmp", file.filename)
    file.save(temp_path)

    try:
        analysis_result = analyze_pdf(temp_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return jsonify(analysis_result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
