import fitz  # PyMuPDF

def extract_findings(pdf_bytes):
    findings = []

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        for page_num, page in enumerate(doc):
            text = page.get_text()

            # VERY BASIC parsing (we will improve later)
            lines = text.split("\n")

            for line in lines:
                if len(line.strip()) > 20:
                    findings.append({
                        "page": page_num + 1,
                        "text": line.strip()
                    })

        return findings

    except Exception as e:
        return [{"error": str(e)}]
