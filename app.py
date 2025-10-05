from flask import Flask, request, jsonify
from flask_cors import CORS
from PyPDF2 import PdfReader
import fitz  # PyMuPDF
import xml.etree.ElementTree as ET
import os

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend integration

# Configuration
ALLOWED_EXTENSIONS = {'.pdf'}

# ---- Helper for color detection ----
def detect_color_mode(color_value):
    """Detects color mode from PyMuPDF color value (int or tuple)."""
    if color_value is None:
        return "Unknown"
    
    # Handle integer color values (common in PyMuPDF)
    if isinstance(color_value, int):
        r = (color_value >> 16) & 0xFF
        g = (color_value >> 8) & 0xFF
        b = color_value & 0xFF
        
        if r == g == b:
            return "Grayscale"
        return "RGB"
    
    # Handle tuple color values
    if isinstance(color_value, tuple):
        if len(color_value) == 4:
            return "CMYK"
        elif len(color_value) == 3:
            return "RGB"
        elif len(color_value) == 1:
            return "Grayscale"
    
    return "Unknown"

# ---- Extract XMP color mode ----
def extract_xmp_color_mode(file_path):
    """Extract color mode from XMP metadata if available."""
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
        print(f"XMP parsing error: {e}")
    return None

# ---- PDF Analyzer Function ----
def analyze_pdf(file_path):
    """Comprehensive PDF analysis for print readiness."""
    result = {
        "content_color_modes": [],
        "declared_color_spaces": [],
        "document_color_mode": "Unknown",
        "fonts_enclosed": False,
        "fonts_list": [],
        "has_cut_contour_layer": False,
        "images_embedded": 0,
        "images_linked": 0,
        "images_low_dpi": 0,
        "image_list": [],
        "layers": False,
        "mode_conflict": False,
        "vector_list": [],
        "text_colors": [],
        "warnings": [],
        "page_count": 0
    }

    # --- XMP metadata first ---
    xmp_mode = extract_xmp_color_mode(file_path)
    if xmp_mode:
        result["document_color_mode"] = xmp_mode.upper()

    # --- PyPDF2 fonts/layers analysis ---
    try:
        reader = PdfReader(file_path)
        result["page_count"] = len(reader.pages)

        is_all_fonts_embedded = True
        fonts_found = set()

        for page in reader.pages:
            resources = page.get("/Resources", {})
            fonts = resources.get("/Font", {})
            if fonts:
                for font_name, font_ref in fonts.items():
                    try:
                        font_obj = font_ref.get_object()
                        
                        # Get actual font name
                        actual_font_name = None
                        if "/BaseFont" in font_obj:
                            actual_font_name = str(font_obj["/BaseFont"]).replace("/", "")
                        elif "/Name" in font_obj:
                            actual_font_name = str(font_obj["/Name"]).replace("/", "")
                        else:
                            actual_font_name = str(font_name)
                        
                        fonts_found.add(actual_font_name)
                        
                        # Check if embedded
                        font_desc = font_obj.get("/FontDescriptor", {})
                        if font_desc:
                            font_desc_obj = font_desc.get_object() if hasattr(font_desc, 'get_object') else font_desc
                            embedded = any(
                                k in font_desc_obj for k in ("/FontFile", "/FontFile2", "/FontFile3")
                            )
                            if not embedded:
                                is_all_fonts_embedded = False
                                result["warnings"].append(f"Font '{actual_font_name}' is not embedded")
                        else:
                            # No font descriptor usually means not embedded
                            is_all_fonts_embedded = False
                            result["warnings"].append(f"Font '{actual_font_name}' may not be embedded (no descriptor)")
                            
                    except Exception as fe:
                        result["warnings"].append(f"Font check failed for {font_name}: {fe}")

        result["fonts_enclosed"] = is_all_fonts_embedded
        result["fonts_list"] = list(fonts_found)

        # Check for layers
        catalog = reader.trailer["/Root"]
        result["layers"] = "/OCProperties" in catalog

    except Exception as e:
        result["warnings"].append(f"PyPDF2 analysis failed: {str(e)}")

    # --- PyMuPDF for images, vectors, text colors ---
    try:
        doc = fitz.open(file_path)

        found_color_modes = set()
        if result["document_color_mode"] != "Unknown":
            found_color_modes.add(result["document_color_mode"])

        image_details = []
        vector_details = []
        text_color_details = []

        for page_index, page in enumerate(doc):
            # Image analysis
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

                    # DPI check
                    dpi_x, dpi_y = img[8] if img[8] else 0, img[9] if img[9] else 0
                    dpi = min(dpi_x, dpi_y) if dpi_x and dpi_y else 0
                    
                    if 0 < dpi < 150:
                        result["images_low_dpi"] += 1

                    image_details.append({
                        "page": page_index + 1,
                        "color_mode": color_mode,
                        "dpi": dpi,
                        "is_low_dpi": 0 < dpi < 150
                    })
                except Exception as img_e:
                    result["warnings"].append(f"Failed to analyze image on page {page_index + 1}: {str(img_e)}")

            # Vector analysis
            try:
                drawings = page.get_cdrawings()
                for drawing in drawings:
                    line_color_mode = detect_color_mode(drawing.get("color"))
                    fill_color_mode = detect_color_mode(drawing.get("fill"))

                    if line_color_mode != "Unknown":
                        found_color_modes.add(line_color_mode)
                    if fill_color_mode != "Unknown":
                        found_color_modes.add(fill_color_mode)

                    vector_details.append({
                        "page": page_index + 1,
                        "type": drawing.get("type", "unknown"),
                        "line_color_mode": line_color_mode,
                        "fill_color_mode": fill_color_mode
                    })
            except Exception as ve:
                result["warnings"].append(f"Vector analysis failed on page {page_index + 1}: {str(ve)}")

            # Text color analysis
            try:
                rawdict = page.get_text("rawdict")
                for block in rawdict.get("blocks", []):
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
                result["warnings"].append(f"Text analysis failed on page {page_index + 1}: {str(te)}")

        doc.close()

        result["image_list"] = image_details
        result["vector_list"] = vector_details
        result["text_colors"] = text_color_details
        result["content_color_modes"] = list(found_color_modes)

        # Check for mode conflicts
        if len(found_color_modes) > 1:
            result["mode_conflict"] = True
            result["warnings"].append(
                f"Multiple color modes detected: {', '.join(sorted(found_color_modes))}. This may cause printing issues."
            )

    except Exception as e:
        result["warnings"].append(f"PyMuPDF analysis failed: {str(e)}")

    return result


# ---- Validation Functions ----
def allowed_file(filename):
    """Check if file has allowed extension."""
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXTENSIONS

# ---- Flask Routes ----
@app.route("/", methods=["GET"])
def index():
    """API information endpoint."""
    return jsonify({
        "name": "PDF Analyzer API",
        "version": "1.0",
        "endpoints": {
            "/analyze": "POST - Upload PDF for analysis",
            "/health": "GET - Health check"
        }
    })

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint for monitoring."""
    return jsonify({"status": "healthy"}), 200

@app.route("/analyze", methods=["POST"])
def analyze():
    """Main PDF analysis endpoint."""
    # Check if file is in request
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    
    # Check for empty filename
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    # Validate file extension
    if not allowed_file(file.filename):
        return jsonify({"error": "Only PDF files are allowed"}), 400

    # Get file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    
    if file_size == 0:
        return jsonify({"error": "Empty file"}), 400

    # Save file temporarily
    temp_path = os.path.join("/tmp", file.filename)
    
    try:
        file.save(temp_path)
        analysis_result = analyze_pdf(temp_path)
        analysis_result["file_name"] = file.filename
        analysis_result["file_size_kb"] = round(file_size / 1024, 2)
        
        return jsonify(analysis_result), 200
        
    except Exception as e:
        return jsonify({"error": f"Analysis failed: {str(e)}"}), 500
        
    finally:
        # Clean up temporary file
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as cleanup_error:
                print(f"Failed to remove temp file: {cleanup_error}")


if __name__ == "__main__":
    # Render requires host 0.0.0.0
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
