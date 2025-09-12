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

    # Check if file uploaded
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    pdf_path = f"/tmp/{file.filename}"
    file.save(pdf_path)

    result = {"color_mode": None, "fonts_enclosed": None, "layers": None}

    try:
        doc = fitz.open(pdf_path)
        colors = set()
        fonts = set()
        has_layers = False

        for page in doc:
            # Check images for color mode
            for img in page.get_images(full=True):
                xref = img[0]
                pix = fitz.Pixmap(doc, xref)
                if pix.n in (3, 4):
                    colors.add("RGB" if pix.n == 3 else "CMYK")
            # Collect fonts used
            blocks = page.get_text("dict")["blocks"]
            for b in blocks:
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("font"):
                            fonts.add(span.get("font"))
            # Check if page has layers (optional, depends on PDF)
            if hasattr(page, 'get_layers') and len(page.get_layers()) > 0:
                has_layers = True

        result["color_mode"] = list(colors)
        result["fonts_enclosed"] = True if fonts else False
        result["layers"] = has_layers

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
