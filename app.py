from flask import Flask, request, jsonify
from PyPDF2 import PdfReader
import fitz  # PyMuPDF
import xml.etree.ElementTree as ET
import os

app = Flask(__name__)

# ---- Helper for color detection ----
def detect_color_mode(color_tuple):
    """Detects color mode (RGB/CMYK/Grayscale) from a PyMuPDF tuple or None."""
    if color_tuple is None:
        return "Unknown"
    if isinstance(color_tuple, tuple):
        if len(color_tuple) == 4:
            return "CMYK"
        elif len(color_tuple) == 3:
            return "RGB"
        elif len(color_tuple) == 1:
            return "Grayscale"
    return "Unknown"

# ---- Extract XMP color mode ----
def extract_xmp_color_mode(file_path):
    try:
        reader = PdfReader(file_path)
        if "/Metadata" in reader.trailer["/Root"]:
            metadata_obj = reader.trailer["/Root"]["/Metadata"].get_object()
            xmp_xml = metadata_obj.get_data()
            root = ET.fromstring(xmp_xml)
            for elem in root.iter():
                if elem.tag.lower().endswith("mode") and elem.text:
                    return elem.text.strip()
    except Exception as e:
        print("XMP parsing error:", e)
    return None

# ---- PDF Analyzer Function ----
def analyze_pdf(file_path):
    result = {
        "content_color_modes": [],
        "declared_color_spaces": [],
        "document_color_mode": "Unknown",
        "fonts_enclosed": False,
        "fonts_list": [],  # List of fonts
        "has_cut_contour_layer": False,
        "images_embedded": 0,
        "images_linked": 0,
        "images_low_dpi": 0,
        "image_list": [],
        "layers": False,
        "mode_conflict": False,
        "vector_list": [],
        "text_colors": [],
        "warnings": []
    }

    # --- XMP metadata first ---
    xmp_mode = extract_xmp_color_mode(file_path)
    if xmp_mode:
        result["document_color_mode"] = xmp_mode.upper()
        result["content_color_modes"].append(xmp_mode.upper())

    # --- PyPDF2 fonts/layers ---
    try:
        reader = PdfReader(file_path)

        is_all_fonts_embedded = True
        fonts_found = set()

        for page in reader.pages:
            resources = page.get("/Resources", {})
            fonts = resources.get("/Font", {})
            if fonts:
                for font_name, font_ref in fonts.items():
                    fonts_found.add(str(font_name))
                    try:
                        font_obj = font_ref.get_object()
                        font_desc = font_obj.get("/FontDescriptor", {})
                        embedded = any(
                            k in font_desc for k in ("/FontFile", "/FontFile2", "/FontFile3")
                        )
                        if not embedded:
                            is_all_fonts_embedded = False
                    except Exception as fe:
                        result["warnings"].append(f"Font check failed: {fe}")

        result["fonts_enclosed"] = is_all_fonts_embedded
        result["fonts_list"] = list(fonts_found)

        catalog = reader.trailer["/Root"]
        result["layers"] = "/OCProperties" in catalog

    except Exception as e:
        result["warnings"].append(f"PyPDF2 analysis failed: {str(e)}")

    # --- PyMuPDF fallback for images, vectors, text colors ---
    try:
        doc = fitz.open(file_path)

        found_color_modes = set(result["content_color_modes"])
        image_details = []
        vector_details = []
        text_color_details = []

        for page_index, page in enumerate(doc):
            # Images
            for img in page.get_images(full=True):
                try:
                    colorspace = img[5]
                    if colorspace == "/DeviceCMYK":
                        color_mode = "CMYK"
                    elif colorspace == "/DeviceRGB":
                        color_mode = "RGB"
                    elif colorspace == "/DeviceGray":
                        color_mode = "Grayscale"
                    else:
                        color_mode = "Unknown"

                    found_color_modes.add(color_mode)
                    result["images_embedded"] += 1

                    dpi_x, dpi_y = img[8], img[9]
                    dpi = min(dpi_x, dpi_y)
                    if dpi < 150:
                        result["images_low_dpi"] += 1

                    image_details.append({
                        "page": page_index + 1,
                        "color_mode": color_mode,
                        "dpi": dpi,
                        "is_low_dpi": dpi < 150
                    })
                except Exception as img_e:
                    result["warnings"].append(f"Failed to analyze image: {str(img_e)}")

            # Vectors
            try:
                for drawing in page.get_cdrawings():
                    line_color_mode = detect_color_mode(drawing.get("color"))
                    fill_color_mode = detect_color_mode(drawing.get("fill"))

                    if line_color_mode != "Unknown":
                        found_color_modes.add(line_color_mode)
                    if fill_color_mode != "Unknown":
                        found_color_modes.add(fill_color_mode)

                    vector_details.append({
                        "page": page_index + 1,
                        "type": drawing["type"],
                        "line_color_mode": line_color_mode,
                        "fill_color_mode": fill_color_mode
                    })
            except Exception as ve:
                result["warnings"].append(f"Vector analysis failed on page {page_index+1}: {str(ve)}")

            # Text colors
            try:
                rawdict = page.get_text("rawdict")
                for block in rawdict["blocks"]:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            col = span.get("color")
                            color_mode = detect_color_mode(col)
                            if color_mode != "Unknown":
                                found_color_modes.add(color_mode)
                                text_color_details.append({
                                    "page": page_index + 1,
                                    "text": span.get("text", "")[:30],
                                    "color_mode": color_mode
                                })
            except Exception as te:
                result["warnings"].append(f"Text analysis failed on page {page_index+1}: {str(te)}")

        doc.close()

        result["image_list"] = image_details
        result["vector_list"] = vector_details
        result["text_colors"] = text_color_details
        result["content_color_modes"] = list(found_color_modes)

    except Exception as e:
        result["warnings"].append(f"PyMuPDF analysis failed: {str(e)}")

    return result


# ---- Flask Routes ----
@app.route("/analyze", methods=["POST"])
def analyze():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    temp_path = os.path.join("/tmp", file.filename)
    file.save(temp_path)

    try:
        analysis_result = analyze_pdf(temp_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return jsonify(analysis_result)


if __name__ == "__main__":
    # Render requires host 0.0.0.0
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
