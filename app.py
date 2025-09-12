from flask import Flask, request, jsonify
import os
import fitz  # PyMuPDF
import pikepdf

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

    result = {"color_mode": [], "fonts_enclosed": None, "layers": None}

    # Detect fonts using PyMuPDF
    try:
        doc = fitz.open(pdf_path)
        fonts = set()
        for page in doc:
            blocks = page.get_text("dict")["blocks"]
            for b in blocks:
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("font"):
                            fonts.add(span.get("font"))
        result["fonts_enclosed"] = True if fonts else False
    except Exception as e:
        result["fonts_enclosed"] = False

    # Detect color mode and layers using pikepdf
    try:
        pdf = pikepdf.Pdf.open(pdf_path)
        color_spaces = set()
        # Check ColorSpace in each page
        for page in pdf.pages:
            resources = page.get('/Resources', {})
            color_space = resources.get('/ColorSpace', {})
            for cs in color_space.values():
                cs_name = str(cs)
                if 'DeviceCMYK' in cs_name:
                    color_spaces.add('CMYK')
                elif 'DeviceRGB' in cs_name:
                    color_spaces.add('RGB')
        result['color_mode'] = list(color_spaces)

        # Detect layers (OCG)
        result['layers'] = True if '/OCProperties' in pdf.root else False
    except Exception as e:
        result['color_mode'] = []
        result['layers'] = False

    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
