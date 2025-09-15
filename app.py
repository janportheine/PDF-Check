from flask import Flask, request, jsonify
import os
import fitz  # PyMuPDF

app = Flask(__name__)

def get_document_color_mode(doc):
    """
    Detect document-level declared color mode from OutputIntents (Illustrator PDFs).
    Returns "RGB", "CMYK", or "Unknown".
    """
    try:
        catalog = doc.pdf_catalog()
        if "OutputIntents" in catalog:
            intents = catalog["OutputIntents"]
            if isinstance(intents, list) and intents:
                intent = intents[0]
                output_condition = intent.get("OutputConditionIdentifier", "")
                if output_condition:
                    oc = output_condition.upper()
                    if "RGB" in oc:
                        return "RGB"
                    elif "CMYK" in oc:
                        return "CMYK"
                dest_profile = intent.get("DestOutputProfile")
                if dest_profile:
                    profile_desc = doc.xref_object(dest_profile, compressed=True)
                    if profile_desc:
                        pd = profile_desc.upper()
                        if "CMYK" in pd:
                            return "CMYK"
                        elif "RGB" in pd:
                            return "RGB"
        return "Unknown"
    except Exception:
        return "Unknown"


def detect_page_color_spaces(page):
    """
    Try to inspect the page's Resource → ColorSpace dictionary for declared color spaces.
    Returns a set of modes found: e.g. {"DeviceCMYK", "DeviceRGB", "ICCBased"} etc.
    """
    modes = set()
    try:
        # The page resources can be fetched via page._get_contents() or more formally via page.get_xref, but
        # for PyMuPDF newer versions, there's a convenience for resolving PDF dictionary resources.
        # We'll try page._get_pdf_object and dictionary lookups.
        resources = page.get_pdf_object(page._get_xref("Resources"))  # get the Resources dict
        if not isinstance(resources, dict):
            return modes

        cs = resources.get("ColorSpace")
        if cs and isinstance(cs, dict):
            for name, cs_value in cs.items():
                # cs_value can be a name object or a reference
                cs_str = str(cs_value)
                if "DeviceCMYK" in cs_str:
                    modes.add("DeviceCMYK")
                if "DeviceRGB" in cs_str:
                    modes.add("DeviceRGB")
                if "ICCBased" in cs_str:
                    modes.add("ICCBased")
        # Also colorspaces may be defined in Patterns, etc., or via extended graphics, but this catches the common cases.
    except Exception:
        pass
    return modes


@app.route("/predict", methods=["POST"])
def predict():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    pdf_path = f"/tmp/{file.filename}"
    file.save(pdf_path)

    result = {
        "document_color_mode": "Unknown",     # from OutputIntents
        "declared_color_spaces": [],           # from page resources
        "content_color_modes": [],             # from images/vectors
        "fonts_enclosed": None,
        "layers": False,
        "images_embedded": 0,
        "images_linked": 0,
        "images_low_dpi": 0,
        "has_cut_contour_layer": False,
        "mode_conflict": False,                # NEW flag
        "warnings": []                         # list of strings explaining conflicts
    }

    try:
        doc = fitz.open(pdf_path)

        # Document-level (OutputIntent)
        doc_mode = get_document_color_mode(doc)
        result["document_color_mode"] = doc_mode

        fonts = set()
        content_modes = set()
        declared_spaces = set()
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
                        dpi_x = pix.width / (page.rect.width / 72)
                        dpi_y = pix.height / (page.rect.height / 72)
                        if dpi_x < 100 or dpi_y < 100:
                            low_dpi_count += 1
                    else:  # linked
                        linked_count += 1

                    # Image color mode
                    if pix.n == 3:
                        content_modes.add("RGB")
                    elif pix.n == 4:
                        content_modes.add("CMYK")
                except Exception:
                    linked_count += 1
                finally:
                    try:
                        pix = None
                    except:
                        pass

            # Vector colors (fill/stroke)
            for item in page.get_drawings():
                for path in item["items"]:
                    if path[0] in ("fill", "stroke"):
                        color = path[1]
                        if isinstance(color, (list, tuple)):
                            if len(color) == 3:
                                content_modes.add("RGB")
                            elif len(color) == 4:
                                content_modes.add("CMYK")

            # Declared color spaces in resources
            declared_spaces.update(detect_page_color_spaces(page))

            # Layers / optional content groups
            if hasattr(page, "get_ocgs") and page.get_ocgs():
                has_layers = True
                ocgs = page.get_ocgs()
                for ocg in ocgs:
                    name = ocg.get("name", "")
                    if "cut-contour" in name.lower():
                        has_cut_contour = True

        result["fonts_enclosed"] = True if fonts else False
        result["content_color_modes"] = list(content_modes)
        result["declared_color_spaces"] = list(declared_spaces)
        result["layers"] = has_layers
        result["images_embedded"] = embedded_count
        result["images_linked"] = linked_count
        result["images_low_dpi"] = low_dpi_count
        result["has_cut_contour_layer"] = has_cut_contour

        # Now detect conflict
        if doc_mode in ("RGB", "CMYK"):
            # If declared document mode is e.g. CMYK, but content has any RGB → conflict
            if content_modes:
                # e.g. doc_mode = "CMYK" and content_modes contains "RGB"
                if doc_mode == "CMYK" and "RGB" in content_modes:
                    result["mode_conflict"] = True
                    result["warnings"].append(
                        "Document declared as CMYK, but content uses RGB"
                    )
                elif doc_mode == "RGB" and "CMYK" in content_modes:
                    result["mode_conflict"] = True
                    result["warnings"].append(
                        "Document declared as RGB, but content uses CMYK"
                    )
            # Also check declared_color_spaces vs doc_mode
            if declared_spaces:
                # Example: doc_mode CMYK, but declared_color_spaces includes DeviceRGB
                if doc_mode == "CMYK" and any(ds for ds in declared_spaces if "DeviceRGB" in ds):
                    result["mode_conflict"] = True
                    result["warnings"].append(
                        "Declared color spaces include DeviceRGB, conflicting with document CMYK"
                    )
                elif doc_mode == "RGB" and any(ds for ds in declared_spaces if "DeviceCMYK" in ds):
                    result["mode_conflict"] = True
                    result["warnings"].append(
                        "Declared color spaces include DeviceCMYK, conflicting with document RGB"
                    )

        # If document has Unknown declared mode, maybe warn if content uses a mix
        if doc_mode == "Unknown":
            if content_modes:
                # if both RGB and CMYK appear
                if "RGB" in content_modes and "CMYK" in content_modes:
                    result["mode_conflict"] = True
                    result["warnings"].append(
                        "Document has no declared mode, but content uses both RGB and CMYK"
                    )

        # If no warnings, maybe note consistency
        if not result["mode_conflict"]:
            result["warnings"].append("No color mode conflicts detected")

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
