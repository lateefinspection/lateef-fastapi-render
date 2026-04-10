def classify_report(pages):
    """
    Determines which adapter to use based on extracted PDF text.
    """

    # Safety check
    if not isinstance(pages, list):
        return "section_based"

    # Combine first 10 pages for detection
    text = "\n".join(p.get("text", "") for p in pages[:10]).lower()

    # --- Spectora detection ---
    spectora_signals = [
        "summary",
        "inspection report",
        "page 1 of",
        "deficient",
        "maintenance",
        "rj home inspections",
        "spectora",
    ]

    spectora_score = sum(1 for k in spectora_signals if k in text)

    if spectora_score >= 3:
        return "spectora"

    # --- InterNACHI / BigBen ---
    if "internachi" in text or "standards of practice" in text:
        return "bigben_internachi"

    # --- AmeriSpec ---
    if "amerispec" in text:
        return "amerispec"

    # --- Default fallback ---
    return "section_based"
