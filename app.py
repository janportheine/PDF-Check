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
        "vector_list": [],
        "warnings": []
    }

    # -----------------------------
    # PyPDF2 analysis (fonts, layers)
    # -----------------------------
    try:
        reader = PdfReader(file_path)

        is_all_fonts_embedded = True
        for page in reader.pages:
            resources = page.get("/Resources", {})
            fonts = resources.get("/Font", {})
            if fonts:
                for _, font_ref in fonts.items():
                    try:
                        font_obj = font_ref.get_object()
                        # Check for embedded font file streams
                        font_desc = font_obj.get("/FontDescriptor", {})
                        embedded = any(
                            k in font_desc for k in ("/FontFile", "/FontFile2", "/FontFile3")
                        )
                        if not embedded:
                            is_all_fonts_embedded = False
                            break
                    except Exception as fe:
                        result["warnings"].append(f"Font check failed: {fe}")
            if not is_all_fonts_embedded:
                break
        result["fonts_enclosed"] = is_all_fonts_embedded

        # Layers (Optional Content groups)
        catalog = reader.trailer["/Root"]
        result["layers"] = "/OCProperties" in catalog

    except Exception as e:
        result["warnings"].append(f"PyPDF2 analysis failed: {str(e)}")

    # -----------------------------
    # PyMuPDF analysis (images, vectors, colors)
    # -----------------------------
    try:
        doc = fitz.open(file_path)

        found_color_modes = set()
        image_details = []
        vector_details = []

        for page_index, page in enumerate(doc):
            # --- Images ---
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.colorspace is not None:
                        cs_channels = pix.colorspace.n
                        if cs_channels == 4:   # CMYK
                            color_mode = "CMYK"
                        elif cs_channels == 3: # RGB
                            color_mode = "RGB"
                        elif cs_channels == 1: # Grayscale
                            color_mode = "Grayscale"
                        else:
                            color_mode = "Other"
                    else:
                        color_mode = "Unknown"

                    found_color_modes.add(color_mode)
                    result["images_embedded"] += 1

                    dpi_x, dpi_y = pix.xres, pix.yres
                    dpi = min(dpi_x, dpi_y)
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
                    result["warnings"].append(f"Failed to analyze image (xref {xref}): {str(img_e)}")

            # --- Vectors (drawings) ---
            try:
                for drawing in page.get_cdrawings():  # new API
                    line_color_mode = "Unknown"
                    fill_color_mode = "Unknown"

                    if "color" in drawing:
                        if drawing["color"].is_cmyk:
                            line_color_mode = "CMYK"
                        elif drawing["color"].is_rgb:
                            line_color_mode = "RGB"
                        elif drawing["color"].is_gray:
                            line_color_mode = "Grayscale"

                    if "fill" in drawing:
                        if drawing["fill"].is_cmyk:
                            fill_color_mode = "CMYK"
                        elif drawing["fill"].is_rgb:
                            fill_color_mode = "RGB"
                        elif drawing["fill"].is_gray:
                            fill_color_mode = "Grayscale"

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

        doc.close()

        result["image_list"] = image_details
        result["vector_list"] = vector_details
        result["content_color_modes"] = list(found_color_modes)

    except Exception as e:
        result["warnings"].append(f"PyMuPDF analysis failed: {str(e)}")

    # -----------------------------
    # Final: determine document color mode
    # -----------------------------
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


# -----------------------------
# Flask endpoint
# -----------------------------
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
