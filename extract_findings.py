import fitz
import os
import json
import hashlib
import sys


def extract_pdf(pdf_path, output_dir="output"):
    os.makedirs(output_dir, exist_ok=True)

    doc = fitz.open(pdf_path)

    result = {
        "pdf_path": pdf_path,
        "page_count": len(doc),
        "pages": [],
        "images": []
    }

    images_dir = os.path.join(output_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    for page_index in range(len(doc)):
        page = doc[page_index]
        page_number = page_index + 1
        page_rect = page.rect

        text = page.get_text("text")
        page_dict = page.get_text("dict")

        text_blocks = []
        image_blocks = []

        for block in page_dict.get("blocks", []):
            block_type = block.get("type")
            bbox = block.get("bbox", [0, 0, 0, 0])

            if block_type == 0:
                block_text_parts = []
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        block_text_parts.append(span.get("text", ""))
                block_text = "".join(block_text_parts).strip()

                if block_text:
                    text_blocks.append({
                        "bbox": bbox,
                        "text": block_text
                    })

            elif block_type == 1:
                image_blocks.append({
                    "bbox": bbox
                })

        result["pages"].append({
            "page_number": page_number,
            "width": page_rect.width,
            "height": page_rect.height,
            "text": text,
            "text_blocks": text_blocks,
            "image_blocks": image_blocks
        })

        image_list = page.get_images(full=True)

        for img_index, img in enumerate(image_list):
            xref = img[0]
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            image_ext = base_image["ext"]

            image_hash = hashlib.sha256(image_bytes).hexdigest()
            image_filename = f"page_{page_number}_img_{img_index + 1}_{image_hash[:12]}.{image_ext}"
            image_path = os.path.join(images_dir, image_filename)

            with open(image_path, "wb") as f:
                f.write(image_bytes)

            bbox = None
            if img_index < len(image_blocks):
                bbox = image_blocks[img_index]["bbox"]

            result["images"].append({
                "page_number": page_number,
                "image_index": img_index + 1,
                "xref": xref,
                "filename": image_filename,
                "sha256": image_hash,
                "path": image_path,
                "bbox": bbox
            })

    json_path = os.path.join(output_dir, "extracted.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print("Extraction complete")
    print(f"Pages: {len(result['pages'])}")
    print(f"Images: {len(result['images'])}")
    print(f"JSON saved to: {json_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract_pdf.py /path/to/file.pdf")
        sys.exit(1)

    extract_pdf(sys.argv[1])
