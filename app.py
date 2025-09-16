from flask import Flask, request, jsonify
from PyPDF2 import PdfReader
from PIL import Image
import fitz  # PyMuPDF
import os
import io

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
        "layers": False,
        "mode_conflict": False,
        "warnings": []
    }

    # PyPDF2 analysis for fonts and layers
    try:
        reader = PdfReader(file_path)
        
        # Check for embedded fonts
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
        
        # Check for layers (Optional Content Properties)
        result["layers"] = any(page.get("/OCProperties") for page in reader.pages) if reader.pages else False
    except Exception as e:
        result["warnings"].append(f"PyPDF2 analysis failed: {str(e)}")

    # PyMuPDF analysis for images and color modes
    try:
        doc = fitz.open(file_path)
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    
                    # Check color space using PyMuPDF's constants
                    if pix.colorspace.n == 1:
                        result["content_color_modes"].append("Grayscale")
                    elif pix.colorspace.n == 3:
                        result["content_color_modes"].append("RGB")
                    elif pix.colorspace.n == 4:
                        result["content_color_modes"].append("CMYK")
                    else:
                        result["content_color_modes"].append("Other")
                    
                    result["images_embedded"] += 1
                    
                    # DPI check (approximated)
                    dpi = pix.xres
                    if dpi < 150:
                        result["images_low_dpi"] += 1
                    
                    pix = None  # Release the pixmap to free memory
                except Exception as img_e:
                    result["warnings"].append(f"Failed to analyze image with xref {xref}: {str(img_e)}")
        
        doc.close()
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

    # Save the file temporarily to a secure location
    temp_path = os.path.join("/tmp", file.filename)
    file.save(temp_path)

    try:
        analysis_result = analyze_pdf(temp_path)
    finally:
        # Ensure the temporary file is deleted
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return jsonify(analysis_result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
