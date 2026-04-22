def classify_report(pages):
    """
    Determine which adapter family to use from extracted PDF pages.
    Returns one of:
    - bigben_internachi
    - spectora
    - amerispec
    - section_based
    """

    if not isinstance(pages, list):
        return "section_based"

    text = "\n".join(p.get("text", "") for p in pages[:10]).lower()

    # --- Spectora / RJ style ---
    spectora_signals = [
        "spectora",
        "rj home inspections",
        "rj residential report",
        "summary",
        "deficient",
        "maintenance",
        "page 1 of",
    ]
    spectora_score = sum(1 for k in spectora_signals if k in text)
    if spectora_score >= 3:
        return "spectora"

    # --- InterNACHI / BigBen ---
    if "internachi" in text or "standards of practice" in text:
        return "bigben_internachi"

    # --- AmeriSpec ---
    amerispec_signals = [
        "amerispec",
        "condition:",
        "recommendation:",
        "defect:",
        "observation:",
    ]
    amerispec_score = sum(1 for k in amerispec_signals if k in text)
    if amerispec_score >= 2:
        return "amerispec"

    if "property inspection report" in text:
        return "generic_narrative"
 
    # --- Fallback ---
    return "section_based"

