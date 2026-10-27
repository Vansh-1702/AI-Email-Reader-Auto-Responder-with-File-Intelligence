import os
import re
import argparse
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image
import google.generativeai as genai
from groq import Groq

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

FOLDER_MAP = {
    "aadhar": "Aadhar",
    "pan": "PAN",
    "driving license": "DrivingLicense",
}

if not GEMINI_API_KEY or not GROQ_API_KEY:
    raise SystemExit("Configure GEMINI_API_KEY and GROQ_API_KEY in .env.")

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(GEMINI_MODEL)
groq_client = Groq(api_key=GROQ_API_KEY)


def extract_text_from_image(image_path):
    """Extract visible text from a document image."""
    image_path = Path(image_path)

    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")

    with Image.open(image_path) as source:
        source.verify()

    with Image.open(image_path) as source:
        image = source.convert("RGB")

        response = gemini_model.generate_content(
            [
                image,
                (
                    "Extract the visible text from this document image "
                    "as accurately as possible. Do not guess unreadable text. "
                    "Return only the extracted text."
                ),
            ],
            request_options={"timeout": 60},
        )

    text = getattr(response, "text", None)
    if not text or not text.strip():
        raise RuntimeError("Gemini returned no extracted text.")

    return text.strip()


def classify_document(text):
    """Return one normalized document category."""
    if not text.strip():
        return "unknown"

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify the supplied OCR text as exactly one of these "
                    "categories: Aadhar, PAN, DrivingLicense, Unknown. "
                    "Use Unknown if the evidence is insufficient. "
                    "Return only the category. Do not repeat personal data."
                ),
            },
            {
                "role": "user",
                "content": f"Classify this document text:\n{text[:8000]}",
            },
        ],
        temperature=0,
        max_completion_tokens=30,
    )

    result = (response.choices[0].message.content or "").strip().lower()

    if "drivinglicense" in result or "driving license" in result:
        return "driving license"
    if re.search(r"\baadhaar\b|\baadhar\b", result):
        return "aadhar"
    if re.search(r"\bpan\b", result):
        return "pan"

    return "unknown"


def unique_path(folder, filename):
    """Choose a destination filename without overwriting an existing file."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    original = Path(filename).name
    destination = folder / original
    counter = 1

    while destination.exists():
        destination = folder / f"{Path(original).stem}_{counter}{Path(original).suffix}"
        counter += 1

    return destination


def save_file_to_folder(file_path, doc_type):
    """Move a classified image into its category folder."""
    folder = FOLDER_MAP.get(doc_type.lower())

    if not folder:
        print("Document could not be classified; file was not moved.")
        return str(file_path)

    source = Path(file_path)
    destination = unique_path(folder, source.name)

    if source.resolve() != destination.resolve():
        source.replace(destination)

    print(f"Document classified as: {doc_type}")
    print(f"Saved to: {destination}")
    return str(destination)


def process_document(image_path):
    print(f"Processing image: {Path(image_path).name}")

    extracted_text = extract_text_from_image(image_path)
    doc_type = classify_document(extracted_text)
    destination = save_file_to_folder(image_path, doc_type)

    # Do not display OCR text, which may contain sensitive identity details.
    return {
        "document_type": doc_type,
        "saved_path": destination,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract and classify an Indian identity document image."
    )
    parser.add_argument("image", help="Path to a JPG, JPEG, or PNG image")
    args = parser.parse_args()

    try:
        result = process_document(args.image)
        print("Result:", result["document_type"])
    except Exception as exc:
        print(f"Processing failed: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()