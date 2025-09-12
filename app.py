from flask import Flask, request, jsonify
import os
import fitz  # PyMuPDF

app = Flask(__name__)

@app.route("/predict", methods=["POST"])
def predict():
    """
    Handle PDF uploaded from Google Apps Script as multipart/form-data.
    Returns JSON with:
      - color_mode: RGB/CMYK detected
      - fonts_enclosed: True/False
      - layers: True/False
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    pdf_path = f"/tmp/{file.filename}"
    file.save(pdf_path)

    result = {"color_mode": [], "fonts_enclosed": None, "layers": False}

    try:
        doc = fitz.open(pdf_path)
        fonts = set()
        colors = set()
        has_layers = False

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
                pix = fitz.Pixmap(doc, xref)
                if pix.n in (3, 4):
                    colors.add("RGB" if pix.n == 3 else "CMYK")
                pix = None

            # Vector colors (fill/stroke)
            for item in page.get_drawings():
                for path in item["items"]:
                    if path[0] in ("fill", "stroke"):
                        color = path[1]
                        # color is a tuple of floats 0-1
                        if len(color) == 3:
                            colors.add("RGB")
                        elif len(color) == 4:
                            colors.add("CMYK")

            # Optional content groups (layers)
            if hasattr(page, "get_ocgs") and page.get_ocgs():
                has_layers = True

        result["fonts_enclosed"] = True if fonts else False
        result["color_mode"] = list(colors)
        result["layers"] = has_layers

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
