import os
import re
import imaplib
import email
import time

from pathlib import Path
from datetime import date
from email.header import decode_header

from dotenv import load_dotenv
from PIL import Image
import google.generativeai as genai
from groq import Groq
import pywhatkit

load_dotenv()

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
APP_PASSWORD = os.getenv("APP_PASSWORD")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WHATSAPP_NUMBER = os.getenv("WHATSAPP_NUMBER", "")

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

DOWNLOAD_FOLDER = Path("DownloadedAttachments")
MAX_EMAILS_PER_RUN = 5
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}

FOLDER_MAP = {
    "aadhar": "Aadhar",
    "pan": "PAN",
    "driving license": "DrivingLicense",
}

if not all([EMAIL_ADDRESS, APP_PASSWORD, GEMINI_API_KEY, GROQ_API_KEY]):
    raise SystemExit(
        "Configure EMAIL_ADDRESS, APP_PASSWORD, GEMINI_API_KEY, "
        "and GROQ_API_KEY in your .env file."
    )

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(GEMINI_MODEL)
groq_client = Groq(api_key=GROQ_API_KEY)

DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
for folder in FOLDER_MAP.values():
    Path(folder).mkdir(parents=True, exist_ok=True)


def decode_filename(filename):
    if not filename:
        return None

    result = []
    for content, encoding in decode_header(filename):
        if isinstance(content, bytes):
            result.append(content.decode(encoding or "utf-8", errors="replace"))
        else:
            result.append(content)

    return "".join(result)


def unique_path(directory, filename):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    safe_name = Path(filename).name
    destination = directory / safe_name
    counter = 1

    while destination.exists():
        destination = directory / (
            f"{Path(safe_name).stem}_{counter}{Path(safe_name).suffix}"
        )
        counter += 1

    return destination


def fetch_image_attachments():
    """Download supported attachments from today's unread emails."""
    mail = None
    attachments = []

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL_ADDRESS, APP_PASSWORD)
        status, _ = mail.select("INBOX")

        if status != "OK":
            raise RuntimeError("Could not open Gmail inbox.")

        today = date.today().strftime("%d-%b-%Y")
        status, data = mail.search(None, f"(UNSEEN SINCE {today})")

        if status != "OK" or not data or not data[0]:
            print("No unread emails found for today.")
            return []

        message_ids = data[0].split()[-MAX_EMAILS_PER_RUN:]

        for message_id in message_ids:
            status, response = mail.fetch(message_id, "(RFC822)")
            if status != "OK":
                continue

            raw_message = next(
                (item[1] for item in response if isinstance(item, tuple)),
                None,
            )
            if not raw_message:
                continue

            message = email.message_from_bytes(raw_message)

            for part in message.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                if part.get_content_disposition() != "attachment":
                    continue

                filename = decode_filename(part.get_filename())
                if not filename:
                    continue

                if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
                    continue

                content = part.get_payload(decode=True)
                if not content:
                    continue

                destination = unique_path(DOWNLOAD_FOLDER, filename)
                destination.write_bytes(content)
                attachments.append(str(destination))
                print(f"Downloaded: {destination.name}")

        return attachments

    except imaplib.IMAP4.error as exc:
        raise RuntimeError(
            "Gmail login/search failed. Check your app password and IMAP access."
        ) from exc

    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass


def extract_text(image_path):
    with Image.open(image_path) as source:
        source.verify()

    with Image.open(image_path) as source:
        image = source.convert("RGB")
        response = gemini_model.generate_content(
            [
                image,
                (
                    "Extract visible text from this document image accurately. "
                    "Do not guess unreadable text. Return only the text."
                ),
            ],
            request_options={"timeout": 60},
        )

    text = getattr(response, "text", None)
    if not text or not text.strip():
        raise RuntimeError("Gemini returned no extracted text.")

    return text.strip()


def classify_document(text):
    if not text.strip():
        return "unknown"

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify Indian identity-document OCR text as exactly "
                    "Aadhar, PAN, DrivingLicense, or Unknown. "
                    "Choose Unknown if unsure. Return only the category. "
                    "Never repeat personal information."
                ),
            },
            {
                "role": "user",
                "content": f"Classify this text:\n{text[:8000]}",
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


def move_file(filepath, doc_type):
    folder = FOLDER_MAP.get(doc_type.lower())

    if not folder:
        print(f"Unclassified file retained: {Path(filepath).name}")
        return str(filepath)

    source = Path(filepath)
    destination = unique_path(folder, source.name)

    if source.resolve() != destination.resolve():
        source.replace(destination)

    print(f"Classified as {doc_type}; saved to {destination}")
    return str(destination)


def send_image(filepath, number, caption=""):
    if not number:
        print("WhatsApp number is missing; image not sent.")
        return False

    try:
        pywhatkit.sendwhats_image(
            receiver=number,
            img_path=str(Path(filepath).resolve()),
            caption=caption,
            wait_time=20,
            tab_close=True,
        )
        print(f"WhatsApp image send requested: {Path(filepath).name}")
        return True
    except Exception as exc:
        print(f"Could not send image {Path(filepath).name}: {exc}")
        return False


def send_text_report(report_lines, number):
    if not number or not report_lines:
        return False

    message = "Document Classification Summary\n\n" + "\n".join(report_lines)

    try:
        pywhatkit.sendwhatmsg_instantly(
            phone_no=number,
            message=message,
            wait_time=20,
            tab_close=True,
        )
        print("WhatsApp summary send requested.")
        return True
    except Exception as exc:
        print(f"Could not send WhatsApp summary: {exc}")
        return False


def main():
    print("Email Document Processing")
    print("Gemini: OCR | Groq: document classification\n")

    if input("Fetch today's unread image attachments? Type YES: ").strip() != "YES":
        print("Cancelled.")
        return

    files = fetch_image_attachments()
    if not files:
        print("No eligible image attachments found.")
        return

    print(f"\nFound {len(files)} image attachment(s).")
    if input("Process these documents? Type YES: ").strip() != "YES":
        print("Processing cancelled.")
        return

    report = []
    classified_images = []

    for filepath in files:
        print(f"\nProcessing: {Path(filepath).name}")

        try:
            text = extract_text(filepath)
            doc_type = classify_document(text)
            final_path = move_file(filepath, doc_type)

            # Keep sensitive OCR text out of logs and WhatsApp reports.
            report.append(
                f"File: {Path(final_path).name} | Type: {doc_type}"
            )

            if doc_type != "unknown":
                classified_images.append((final_path, doc_type))

        except Exception as exc:
            print(f"Processing failed for {Path(filepath).name}: {exc}")
            report.append(
                f"File: {Path(filepath).name} | Processing failed"
            )

    print("\nSummary:")
    for line in report:
        print(line)

    if not report:
        return

    print(
        "\nWarning: Aadhaar, PAN, and driving-license images contain "
        "sensitive personal information."
    )
    confirm = input(
        "Share classified images and the summary on WhatsApp? "
        "Type YES to confirm: "
    ).strip()

    if confirm != "YES":
        print("WhatsApp sharing skipped.")
        return

    if not WHATSAPP_NUMBER:
        print("Set WHATSAPP_NUMBER in .env before sharing.")
        return

    for filepath, doc_type in classified_images:
        send_image(
            filepath,
            WHATSAPP_NUMBER,
            caption=f"{Path(filepath).name} - {doc_type}",
        )
        time.sleep(3)

    send_text_report(report, WHATSAPP_NUMBER)
    print("\nWorkflow completed.")


if __name__ == "__main__":
    main()