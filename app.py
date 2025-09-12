from flask import Flask, request, jsonify
import os
import fitz  # PyMuPDF

app = Flask(__name__)

@app.route("/analyze_pdf", methods=["POST"])
def analyze_pdf():
    result = {"color_mode": None, "fonts_enclosed": None, "layers": None}

    if "file" in request.files:
        file = request.files["file"]
        pdf_path = f"/tmp/{file.filename}"
        file.save(pdf_path)
    elif request.json and "file_url" in request.json:
        import requests
        url = request.json["file_url"]
        pdf_path = "/tmp/temp.pdf"
        r = requests.get(url)
        with open(pdf_path, "wb") as f:
            f.write(r.content)
    else:
        return jsonify({"error": "No file provided"}), 400

    try:
        doc = fitz.open(pdf_path)
        colors = set()
        fonts = set()
        has_layers = False

        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                pix = fitz.Pixmap(doc, xref)
                if pix.n in (3, 4):
                    colors.add("RGB" if pix.n == 3 else "CMYK")
            fonts.update([x[0] for x in page.get_text("dict")["blocks"] if x.get("font")])
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
