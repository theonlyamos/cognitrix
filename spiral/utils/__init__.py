from PIL import Image
import base64
import io

def image_to_base64(image: Image.Image) -> str:
    """Converts a PIL Image to base64"""

    screenshot_bytes = io.BytesIO()
    image.save(screenshot_bytes, format='JPEG')

    # Convert the BytesIO object to base64
    return base64.b64encode(screenshot_bytes.getvalue()).decode('utf-8')