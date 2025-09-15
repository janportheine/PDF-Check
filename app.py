from flask import Flask, request, jsonify
import os
import fitz  # PyMuPDF

app = Flask(__name__)

def get_document_color_mode(doc):
    """
    Tries to detect the declared document color mode
    using OutputIntents (common in Illustrator PDFs).
    """
    try:
        catalog = doc.pdf_catalog()
        if "OutputIntents" in catalog:
            intents = catalog["OutputIntents"]
            if isinstance(intents, list) and intents:
                intent = intents[0]  # usually the main one
                output_condition = intent.get("OutputConditionIdentifier", "")
                if output_condition:
                    if "RGB" in output_condition.upper():
                        return "RGB"
                    elif "CMYK" in output_condition.upper():
                        return "CMYK"

                # Check ICC profile description if available
                dest_profile = intent.get("DestOutputProfile")
                if dest_profile:
                    profile_desc = doc.xref_object(dest_profile, compressed=True)
                    if "CMYK" in profile_desc.upper():
                        return "CMYK"
                    elif "RGB" in profile_desc.upper():
                        return "RGB"
        return "Unknown"
    except Exception:
        return "Unknown"


@app.route("/predict", methods=["POST"])
def predict():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    pdf_path = f"/tmp/{file.filename}"
    file.save(pdf_path)

    result = {
        "document_color_mode": "Unknown",  # NEW
        "content_color_modes": [],         # Renamed from color_mode
        "fonts_enclosed": None,
        "layers": False,
        "images_embedded": 0,
        "images_linked": 0,
        "images_low_dpi": 0,
        "has_cut_contour_layer": False
    }

    try:
        doc = fitz.open(pdf_path)

        # Document-level mode
        result["document_color_mode"] = get_document_color_mode(doc)

        fonts = set()
        colors = set()
        has_layers = False
        embedded_count = 0
        linked_count = 0
        low_dpi_count = 0
        has_cut_contour = False

        for page in doc:
            # Fonts
            blocks = page.get_text("dict")["blocks"]
            for b in blocks:
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("font"):
                            fonts.add(span.get("font"))

            # Images
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.samples:  # embedded
                        embedded_count += 1
                        # DPI calculation
                        dpi_x = pix.width / (page.rect.width / 72)
                        dpi_y = pix.height / (page.rect.height / 72)
                        if dpi_x < 100 or dpi_y < 100:
                            low_dpi_count += 1
                    else:  # linked
                        linked_count += 1

                    # Image color mode
                    if pix.n == 3:
                        colors.add("RGB")
                    elif pix.n == 4:
                        colors.add("CMYK")

                except Exception:
                    linked_count += 1
                pix = None

            # Vector colors (fill/stroke)
            for item in page.get_drawings():
                for path in item["items"]:
                    if path[0] in ("fill", "stroke"):
                        color = path[1]
                        if len(color) == 3:
                            colors.add("RGB")
                        elif len(color) == 4:
                            colors.add("CMYK")

            # Layers (Optional Content Groups)
            if hasattr(page, "get_ocgs") and page.get_ocgs():
                has_layers = True
                ocgs = page.get_ocgs()
                for ocg in ocgs:
                    name = ocg.get("name", "")
                    if "cut-contour" in name.lower():
                        has_cut_contour = True

        # Fill result
        result["fonts_enclosed"] = True if fonts else False
        result["content_color_modes"] = list(colors)
        result["layers"] = has_layers
        result["images_embedded"] = embedded_count
        result["images_linked"] = linked_count
        result["images_low_dpi"] = low_dpi_count
        result["has_cut_contour_layer"] = has_cut_contour

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
