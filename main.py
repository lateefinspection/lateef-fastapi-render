from fastapi import FastAPI, UploadFile, File
import pdfplumber
import tempfile
import os

from image_matcher import match_image

app = FastAPI()


@app.get("/")
def root():
    return {"status": "ok"}


@app.post("/analyze-report/")
async def analyze_report(file: UploadFile = File(...)):
    # Save uploaded PDF
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    # Extract text (not used yet, but ready)
    page_texts = []
    with pdfplumber.open(tmp_path) as pdf:
        for page in pdf.pages:
            page_texts.append(page.extract_text() or "")

    # Example sections (your inspection categories)
    sections = ["roof", "plumbing", "electrical", "hvac", "foundation"]

    record_id = "test-record-123"

    # 🔥 USE YOUR EXISTING FUNCTION CORRECTLY
    results = {}
    for section in sections:
        results[section] = match_image(section, record_id)

    os.remove(tmp_path)

    return {
        "images": results,
        "pages": len(page_texts)
    }
