from flask import Flask, request, jsonify
import os
import fitz  # PyMuPDF

app = Flask(__name__)

@app.route("/predict", methods=["POST"])
def predict():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    pdf_path = f"/tmp/{file.filename}"
    file.save(pdf_path)

    result = {
        "color_mode": [],
        "fonts_enclosed": None,
        "layers": False,
        "images_embedded": 0,
        "images_linked": 0
    }

    try:
        doc = fitz.open(pdf_path)
        fonts = set()
        colors = set()
        has_layers = False
        embedded_count = 0
        linked_count = 0

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
                    if pix.samples:  # actual image data → embedded
                        embedded_count += 1
                    else:           # no image data → linked
                        linked_count += 1
                except Exception:
                    linked_count += 1  # fallback
                pix = None

                # Detect image color
                if pix and pix.n in (3, 4):
                    colors.add("RGB" if pix.n == 3 else "CMYK")

            # Vector colors (fill/stroke)
            for item in page.get_drawings():
                for path in item["items"]:
                    if path[0] in ("fill", "stroke"):
                        color = path[1]
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
        result["images_embedded"] = embedded_count
        result["images_linked"] = linked_count

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
