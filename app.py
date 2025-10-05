from flask import Flask, request, jsonify
from flask_cors import CORS
from PyPDF2 import PdfReader
import fitz  # PyMuPDF
import xml.etree.ElementTree as ET
import re
import os
import requests
import tempfile

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

# ---- Extract linked image paths from XMP ----
def extract_linked_image_paths(file_path):
    """Extract linked image file paths from XMP metadata."""
    linked_paths = []
    try:
        reader = PdfReader(file_path)
        if "/Metadata" in reader.trailer["/Root"]:
            metadata_obj = reader.trailer["/Root"]["/Metadata"].get_object()
            xmp_xml = metadata_obj.get_data()
            
            # Parse as string to find file paths
            xmp_str = xmp_xml.decode('utf-8', errors='ignore')
            
            # Look for stRef:filePath tags
            file_path_pattern = r'<stRef:filePath>([^<]+)</stRef:filePath>'
            matches = re.findall(file_path_pattern, xmp_str)
            
            for match in matches:
                linked_paths.append(match.strip())
            
            # Also try parsing as XML for more structured approach
            try:
                root = ET.fromstring(xmp_xml)
                # Search for filePath elements in any namespace
                for elem in root.iter():
                    if 'filePath' in elem.tag or elem.tag.endswith('filePath'):
                        if elem.text:
                            linked_paths.append(elem.text.strip())
            except:
                pass  # Regex method above should catch most cases
                
    except Exception as e:
        print(f"XMP linked image path extraction error: {e}")
    
    return list(set(linked_paths))  # Remove duplicates

# ---- Check for CutContour/Thru-cut in XMP ----
def check_cut_layers_in_xmp(file_path):
    """Check for CutContour or Thru-cut in XMP metadata swatches."""
    try:
        reader = PdfReader(file_path)
        if "/Metadata" in reader.trailer["/Root"]:
            metadata_obj = reader.trailer["/Root"]["/Metadata"].get_object()
            xmp_xml = metadata_obj.get_data()
            xmp_str = xmp_xml.decode('utf-8', errors='ignore')
            
            # Look for swatchName tags with CutContour or Thru-cut
            swatch_pattern = r'<xmpG:swatchName>(CutContour|Thru-cut)</xmpG:swatchName>'
            matches = re.findall(swatch_pattern, xmp_str)
            
            if matches:
                return True, matches[0]
                
    except Exception as e:
        print(f"XMP cut layer check error: {e}")
    
    return False, None

# ---- Download file from Google Drive ----
def download_from_google_drive(file_id):
    """Download a file from Google Drive using the file ID."""
    try:
        # Try direct download first
        download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        
        session = requests.Session()
        response = session.get(download_url, stream=True)
        
        # Check for virus scan warning (large files)
        token = None
        for key, value in response.cookies.items():
            if key.startswith('download_warning'):
                token = value
                break
        
        if token:
            # Retry with confirmation token for large files
            params = {'id': file_id, 'confirm': token}
            response = session.get(download_url, params=params, stream=True)
        
        # Alternative: Check if response is HTML (error page)
        content_type = response.headers.get('Content-Type', '')
        if 'text/html' in content_type:
            # Try alternative download URL
            download_url = f"https://drive.google.com/u/0/uc?id={file_id}&export=download&confirm=t"
            response = session.get(download_url, stream=True)
        
        if response.status_code != 200:
            return None, f"Failed to download file: HTTP {response.status_code}"
        
        # Create a temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        temp_path = temp_file.name
        
        # Write the content
        with open(temp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=32768):
                if chunk:
                    f.write(chunk)
        
        # Verify it's a valid PDF
        file_size = os.path.getsize(temp_path)
        if file_size == 0:
            os.remove(temp_path)
            return None, "Downloaded file is empty"
        
        # Check PDF header
        with open(temp_path, 'rb') as f:
            header = f.read(5)
            if header != b'%PDF-':
                os.remove(temp_path)
                return None, "Downloaded file is not a valid PDF. Make sure the file is shared with 'Anyone with the link can view'"
        
        return temp_path, None
            
    except Exception as e:
        return None, f"Download error: {str(e)}"

# ---- PDF Analyzer Function ----
def analyze_pdf(file_path):
    """Comprehensive PDF analysis for print readiness."""
    result = {
        "content_color_modes": [],
        "declared_color_spaces": [],
        "document_color_mode": "Unknown",
        "fonts": False,
        "fonts_list": [],
        "has_cut_contour_layer": False,
        "images_embedded": 0,
        "images_linked": 0,
        "images_low_dpi": 0,
        "image_list": [],
        "linked_images_list": [],
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

    # --- Extract linked image paths from XMP ---
    linked_image_paths = extract_linked_image_paths(file_path)
    
    # --- Check for cut layers in XMP ---
    has_cut_in_xmp, cut_name = check_cut_layers_in_xmp(file_path)
    if has_cut_in_xmp:
        result["has_cut_contour_layer"] = True
        result["warnings"].append(f"Cut layer detected in XMP metadata: {cut_name}")

    # --- PyPDF2 fonts/layers analysis ---
    try:
        reader = PdfReader(file_path)
        result["page_count"] = len(reader.pages)

        is_all_fonts_embedded = True
        fonts_found = set()
        linked_images = []

        for page_num, page in enumerate(reader.pages):
            resources = page.get("/Resources", {})
            
            # Font analysis
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

            # Check for linked images in XObjects
            xobjects = resources.get("/XObject", {})
            if xobjects:
                for xobj_name, xobj_ref in xobjects.items():
                    try:
                        xobj = xobj_ref.get_object()
                        if xobj.get("/Subtype") == "/Image":
                            # Check for external file reference
                            if "/F" in xobj or "/FFilter" in xobj or "/FDecodeParms" in xobj:
                                file_spec = xobj.get("/F")
                                if file_spec:
                                    file_path_obj = file_spec.get_object() if hasattr(file_spec, 'get_object') else file_spec
                                    if isinstance(file_path_obj, dict) and "/F" in file_path_obj:
                                        linked_path = str(file_path_obj["/F"])
                                    else:
                                        linked_path = str(file_spec)
                                    
                                    linked_images.append({
                                        "page": page_num + 1,
                                        "name": str(xobj_name),
                                        "path": linked_path
                                    })
                                    result["images_linked"] += 1
                    except Exception as xe:
                        pass  # Silent fail for XObject inspection

        result["fonts"] = len(fonts_found) > 0
        result["fonts_list"] = list(fonts_found)
        result["linked_images_list"] = linked_images
        
        if linked_images:
            result["warnings"].append(f"Found {len(linked_images)} linked (external) images. PDF may not be portable.")

        # Check for layers and CutContour
        catalog = reader.trailer["/Root"]
        result["layers"] = "/OCProperties" in catalog
        
        # Check for CutContour or Thru-cut in OCProperties (Optional Content)
        if "/OCProperties" in catalog:
            try:
                oc_props = catalog["/OCProperties"].get_object()
                if "/OCGs" in oc_props:
                    ocgs = oc_props["/OCGs"]
                    for ocg in ocgs:
                        try:
                            ocg_obj = ocg.get_object()
                            name = ocg_obj.get("/Name", "")
                            if isinstance(name, str):
                                if name == "CutContour" or name == "Thru-cut":
                                    result["has_cut_contour_layer"] = True
                                    result["warnings"].append(f"Cut layer detected: {name}")
                                    break
                        except:
                            pass
            except Exception as oc_e:
                pass  # Silent fail for optional content inspection

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
        linked_img_index = 0
        processed_xrefs = set()  # Track processed images to avoid duplicates

        for page_index, page in enumerate(doc):
            # Image analysis
            for img in page.get_images(full=True):
                try:
                    xref = img[0]  # Image xref number
                    
                    # Skip if already processed (avoid duplicates)
                    if xref in processed_xrefs:
                        continue
                    processed_xrefs.add(xref)
                    
                    # Try to extract the image to check if it's truly embedded
                    is_embedded = True
                    image_name = img[7] if len(img) > 7 else f"img_{xref}"
                    
                    try:
                        # Attempt to extract image bytes
                        pix = fitz.Pixmap(doc, xref)
                        # If we can create a pixmap and it has valid data, it's embedded
                        if pix.size == 0:
                            is_embedded = False
                        pix = None  # Clean up
                    except:
                        # If extraction fails, it might be linked
                        is_embedded = False
                    
                    # Also check the image object for external file references
                    try:
                        img_obj = doc.xref_object(xref)
                        if "/F" in img_obj or "FFilter" in img_obj or "/Type/XObject" in img_obj:
                            # Check if it references an external file
                            if "/F" in img_obj:
                                is_embedded = False
                    except:
                        pass
                    
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

                    # DPI check
                    dpi_x, dpi_y = img[8] if img[8] else 0, img[9] if img[9] else 0
                    dpi = min(dpi_x, dpi_y) if dpi_x and dpi_y else 0
                    
                    if 0 < dpi < 150:
                        result["images_low_dpi"] += 1

                    # Get actual file path from XMP if this is a linked image
                    actual_path = None
                    if not is_embedded and linked_img_index < len(linked_image_paths):
                        actual_path = linked_image_paths[linked_img_index]
                        # Extract just the filename for display
                        image_name = os.path.basename(actual_path)
                        linked_img_index += 1

                    image_info = {
                        "page": page_index + 1,
                        "name": image_name,
                        "color_mode": color_mode,
                        "dpi": dpi,
                        "is_low_dpi": 0 < dpi < 150,
                        "is_embedded": is_embedded
                    }
                    
                    if is_embedded:
                        result["images_embedded"] += 1
                    else:
                        result["images_linked"] += 1
                        linked_info = {
                            "page": page_index + 1,
                            "name": image_name
                        }
                        result["linked_images_list"].append(linked_info)
                        result["warnings"].append(f"Linked image '{image_name}' found on page {page_index + 1}")
                    
                    image_details.append(image_info)
                    
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
                    
                    # Check for CutContour colors (commonly magenta spot color)
                    # CutContour is often RGB(255,0,255) or CMYK(0,100,0,0)
                    color = drawing.get("color")
                    if color:
                        # Check for magenta (common CutContour color)
                        if isinstance(color, tuple):
                            if len(color) == 3 and color == (1.0, 0.0, 1.0):  # RGB magenta
                                result["has_cut_contour_layer"] = True
                            elif len(color) == 4 and color[1] == 1.0 and color[0] == 0.0 and color[2] == 0.0:  # CMYK magenta
                                result["has_cut_contour_layer"] = True
                        elif isinstance(color, int):
                            # RGB magenta as int: 0xFF00FF
                            if color == 0xFF00FF or color == 16711935:
                                result["has_cut_contour_layer"] = True
                                
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

        # Add warning if CutContour detected
        if result["has_cut_contour_layer"]:
            result["warnings"].append("CutContour/Thru-cut layer detected in the PDF")

        # Add final warning if linked images found
        if result["images_linked"] > 0:
            result["warnings"].append(
                f"Found {result['images_linked']} linked (external) images. PDF may not be portable and images may be missing."
            )

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
        "version": "2.0",
        "endpoints": {
            "/analyze": "POST - Upload PDF for analysis",
            "/analyze-drive": "POST - Analyze PDF from Google Drive file ID",
            "/health": "GET - Health check"
        }
    })

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint for monitoring."""
    return jsonify({"status": "healthy"}), 200

@app.route("/analyze", methods=["POST"])
def analyze():
    """Main PDF analysis endpoint - file upload."""
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

@app.route("/analyze-drive", methods=["POST"])
def analyze_drive():
    """Analyze PDF from Google Drive using file ID."""
    data = request.get_json()
    
    if not data or "fileId" not in data:
        return jsonify({"error": "No fileId provided"}), 400
    
    file_id = data["fileId"]
    file_name = data.get("fileName", "unknown.pdf")
    
    # Download file from Google Drive
    temp_path, error = download_from_google_drive(file_id)
    
    if error:
        return jsonify({"error": error}), 400
    
    try:
        # Get file size
        file_size = os.path.getsize(temp_path)
        
        # Analyze the PDF
        analysis_result = analyze_pdf(temp_path)
        analysis_result["file_name"] = file_name
        analysis_result["file_size_kb"] = round(file_size / 1024, 2)
        
        return jsonify(analysis_result), 200
        
    except Exception as e:
        return jsonify({"error": f"Analysis failed: {str(e)}"}), 500
        
    finally:
        # Clean up temporary file
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as cleanup_error:
                print(f"Failed to remove temp file: {cleanup_error}")


if __name__ == "__main__":
    # Render requires host 0.0.0.0
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
