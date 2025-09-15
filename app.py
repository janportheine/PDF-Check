import fitz  # PyMuPDF
import re
import xml.etree.ElementTree as ET

def get_xmp_metadata(doc):
    """
    Extract XMP metadata from the PDF document.
    """
    xmp_data = doc.metadata.get("xmp", None)
    if xmp_data:
        try:
            # Parse the XMP XML data
            root = ET.fromstring(xmp_data)
            # Define the XML namespace
            ns = {'xmp': 'http://www.w3.org/2001/XMLSchema-instance'}
            # Search for the color mode element
            mode_element = root.find('.//xmp:mode', ns)
            if mode_element is not None:
                return mode_element.text
        except ET.ParseError:
            pass
    return None

def get_document_color_mode(doc):
    """
    Detect the document's declared color mode from OutputIntents.
    """
    try:
        catalog = doc.pdf_catalog()
        if "/OutputIntents" in catalog:
            output_intent = catalog["/OutputIntents"]
            if isinstance(output_intent, list):
                for oi in output_intent:
                    if isinstance(oi, dict):
                        if "/OutputCondition" in oi:
                            condition = oi["/OutputCondition"]
                            if isinstance(condition, str):
                                if "CMYK" in condition:
                                    return "CMYK"
                                elif "RGB" in condition:
                                    return "RGB"
    except Exception:
        pass
    return "Unknown"

def detect_page_color_spaces(doc, page):
    """
    Detect declared color spaces on a page by inspecting its resources.
    """
    color_spaces = set()
    try:
        resources = page.get_resources()
        if "/ColorSpace" in resources:
            for key, value in resources["/ColorSpace"].items():
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            if "DeviceCMYK" in item:
                                color_spaces.add("DeviceCMYK")
                            elif "DeviceRGB" in item:
                                color_spaces.add("DeviceRGB")
    except Exception:
        pass
    return color_spaces

def detect_content_color_modes(page):
    """
    Detect the color modes of images and vector graphics on a page.
    """
    content_modes = set()
    try:
        # Check images
        for img in page.get_images(full=True):
            xref = img[0]
            pix = fitz.Pixmap(doc, xref)
            if pix.n == 3:
                content_modes.add("RGB")
            elif pix.n == 4:
                content_modes.add("CMYK")
            pix = None

        # Check vector graphics
        for item in page.get_drawings():
            for path in item["items"]:
                if path[0] in ("fill", "stroke"):
                    color = path[1]
                    if len(color) == 3:
                        content_modes.add("RGB")
                    elif len(color) == 4:
                        content_modes.add("CMYK")
    except Exception:
        pass
    return content_modes

def analyze_pdf(pdf_path):
    """
    Analyze the PDF document for color mode information.
    """
    result = {
        "document_color_mode": "Unknown",
        "xmp_color_mode": None,
        "declared_color_spaces": [],
        "content_color_modes": [],
        "mode_conflict": False,
        "warnings": []
    }

    try:
        doc = fitz.open(pdf_path)

        # Extract XMP metadata
        xmp_mode = get_xmp_metadata(doc)
        result["xmp_color_mode"] = xmp_mode

        # Determine the document's declared color mode
        doc_mode = get_document_color_mode(doc)
        result["document_color_mode"] = doc_mode

        # Analyze each page for declared color spaces and content color modes
        declared_spaces = set()
        content_modes = set()
        for page in doc:
            declared_spaces.update(detect_page_color_spaces(doc, page))
            content_modes.update(detect_content_color_modes(page))

        result["declared_color_spaces"] = list(declared_spaces)
        result["content_color_modes"] = list(content_modes)

        # Check for conflicts between declared and content color modes
        if doc_mode in ("RGB", "CMYK"):
            if doc_mode == "CMYK" and "RGB" in content_modes:
                result["mode_conflict"] = True
                result["warnings"].append("Declared CMYK, but content uses RGB")
            elif doc_mode == "RGB" and "CMYK" in content_modes:
                result["mode_conflict"] = True
                result["warnings"].append("Declared RGB, but content uses CMYK")
        elif "RGB" in content_modes and "CMYK" in content_modes:
            result["mode_conflict"] = True
            result["warnings"].append("Mixed RGB and CMYK content with no declared mode")

        if not result["mode_conflict"]:
            result["warnings"].append("No color mode conflicts detected")

    except Exception as e:
        result["error"] = str(e)

    return result
