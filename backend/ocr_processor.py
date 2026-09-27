import cv2
import numpy as np
import base64
import json
import logging
import os
import re
import tempfile
from typing import Optional

import requests

logger = logging.getLogger(__name__)

def _load_trocr():
    """TrOCR (Microsoft) - transformer-based OCR, Python 3.14 compatible.
    Install: pip install transformers torch pillow"""
    try:
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        

        hf_token = os.environ.get('HF_TOKEN', '') or os.environ.get('HUGGINGFACEHUB_API_TOKEN', '')
        
        if hf_token:
            logger.info("Loading TrOCR model with Hugging Face authentication...")
            processor = TrOCRProcessor.from_pretrained(
                "microsoft/trocr-base-printed",
                token=hf_token
            )
            model = VisionEncoderDecoderModel.from_pretrained(
                "microsoft/trocr-base-printed",
                token=hf_token
            )
        else:
            logger.info("Loading TrOCR model without token (may have rate limits)...")
            processor = TrOCRProcessor.from_pretrained("microsoft/trocr-base-printed")
            model = VisionEncoderDecoderModel.from_pretrained("microsoft/trocr-base-printed")
        
        model.eval()
        logger.info("TrOCR initialised successfully.")
        return {"processor": processor, "model": model}
    except Exception as e:
        logger.warning(f"TrOCR unavailable: {e}")
        return None


def _load_easy():
    """EasyOCR - install: pip install easyocr"""
    try:
        import easyocr
        reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        logger.info("EasyOCR initialised successfully.")
        return reader
    except Exception as e:
        logger.warning(f"EasyOCR unavailable: {e}")
        return None







MISTRAL_API_KEY = os.environ.get('MISTRAL_API_KEY', '')
MISTRAL_API_URL = os.environ.get('MISTRAL_API_URL', 'https://api.mistral.ai/v1/chat/completions')
MISTRAL_MODEL = os.environ.get('MISTRAL_MODEL', 'pixtral-12b-2409')

INVOICE_EXTRACTION_PROMPT = """You are an expert invoice data extraction assistant.
Analyze this invoice image and extract ALL available information.

Return ONLY a valid JSON object with this exact structure (use null for missing fields):
{
  "invoice_number": "string or null",
  "date": "string or null",
  "due_date": "string or null",
  "vendor_name": "string or null",
  "vendor_address": "string or null",
  "customer_name": "string or null",
  "customer_address": "string or null",
  "items": [
    {
      "description": "string",
      "quantity": "string",
      "unit_price": "string",
      "total": "string"
    }
  ],
  "subtotal": "string or null",
  "tax": "string or null",
  "total": "string or null",
  "currency": "string or null",
  "payment_terms": "string or null",
  "notes": "string or null",
  "construction_specific": {
    "project_name": "string or null",
    "project_address": "string or null",
    "project_number": "string or null",
    "contractor_license": "string or null",
    "work_order": "string or null",
    "material_cost": "string or null",
    "labor_cost": "string or null"
  }
}

Rules:
- Extract ALL line items you can see, do not skip any
- Keep monetary values as plain numbers without currency symbols (e.g. "1500.00")
- Keep quantity as a descriptive string (e.g. "10 Days", "5 units")
- If construction_specific fields are not present in the invoice, set them to null
- Do not invent or guess values - only extract what is visible
- Return raw JSON only, no markdown fences, no explanation
"""


def _encode_image_to_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def _call_mistral_vision(image_path: str) -> Optional[dict]:
    """
    Send image to Mistral Pixtral vision model for AI-powered structured extraction.
    Free tier: no per-minute rate limits.
    Get a free key at https://console.mistral.ai
    Returns parsed invoice dict, or None on failure.
    """
    api_key = MISTRAL_API_KEY or os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        logger.warning("MISTRAL_API_KEY not set - skipping Mistral vision extraction.")
        return None

    ext = os.path.splitext(image_path)[1].lower()
    mime_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
    }.get(ext, "image/jpeg")

    try:

        print(f"\n[DEBUG] Sending to Mistral Vision:")
        print(f"  Image path: {image_path}")
        print(f"  File exists: {os.path.exists(image_path)}")
        print(f"  File size: {os.path.getsize(image_path)} bytes")
        print(f"  Mime type: {mime_type}")
        
        image_data = _encode_image_to_base64(image_path)
        print(f"  Base64 length: {len(image_data)} chars")

        payload = {
            "model": MISTRAL_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": f"data:{mime_type};base64,{image_data}",
                        },
                        {
                            "type": "text",
                            "text": INVOICE_EXTRACTION_PROMPT,
                        },
                    ],
                }
            ],
            "max_tokens": 2048,
            "temperature": 0,
        }

        response = requests.post(
            MISTRAL_API_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )
        
        print(f"  Response status: {response.status_code}")
        
        response.raise_for_status()

        raw = response.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        result = json.loads(raw)
        logger.info("Mistral vision extraction succeeded.")
        print(f"  Success! Extracted data.")
        return result

    except requests.HTTPError as e:
        error_detail = e.response.text if e.response else 'No response'
        logger.error(f"Mistral HTTP error: {e} - {error_detail}")
        print(f"\n{'='*60}")
        print(f"MISTRAL API ERROR")
        print(f"{'='*60}")
        print(f"Status: {e.response.status_code if e.response else 'Unknown'}")
        print(f"Model: {MISTRAL_MODEL}")
        print(f"URL: {MISTRAL_API_URL}")
        print(f"Image path: {image_path}")
        print(f"Image exists: {os.path.exists(image_path)}")
        print(f"Image size: {os.path.getsize(image_path) if os.path.exists(image_path) else 'N/A'}")
        print(f"Response body: {error_detail[:1000]}")
        print(f"{'='*60}\n")
    except (KeyError, IndexError) as e:
        logger.error(f"Unexpected Mistral response structure: {e}")
        print(f"\n[ERROR] Unexpected response structure: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Mistral JSON response: {e}")
        print(f"\n[ERROR] JSON decode error: {e}")
    except Exception as e:
        logger.error(f"Mistral vision call failed: {e}")
        print(f"\n[ERROR] {e}")

    return None






def _tokens_to_text(tokens: list, row_gap: int = 25) -> str:
    if not tokens:
        return ""
    sorted_tokens = sorted(tokens, key=lambda t: t[1])
    lines, current_line, current_y = [], [], sorted_tokens[0][1]
    for tok in sorted_tokens:
        if abs(tok[1] - current_y) <= row_gap:
            current_line.append(tok)
        else:
            if current_line:
                current_line.sort(key=lambda t: t[0])
                lines.append(" ".join(t[4] for t in current_line))
            current_line = [tok]
            current_y = tok[1]
    if current_line:
        current_line.sort(key=lambda t: t[0])
        lines.append(" ".join(t[4] for t in current_line))
    return "\n".join(lines)


def _merge_texts(texts: list) -> str:
    """
    Merge outputs from multiple OCR engines.
    Deduplicates lines, keeping the longest version of near-duplicates.
    """
    if not texts:
        return ""
    if len(texts) == 1:
        return texts[0]

    all_lines = []
    for text in texts:
        for line in text.splitlines():
            line = line.strip()
            if line:
                all_lines.append(line)

    merged = []
    used = set()
    for line in all_lines:
        line_lower = line.lower()
        is_duplicate = False
        for i, existing in enumerate(merged):
            existing_lower = existing.lower()
            if line_lower in existing_lower or existing_lower in line_lower:
                is_duplicate = True
                if len(line) > len(existing):
                    merged[i] = line
                break
        if not is_duplicate and line_lower not in used:
            merged.append(line)
            used.add(line_lower)

    return "\n".join(merged)






class OCRProcessor:
    """
    Multi-engine invoice OCR processor.

    Engines:
      1. Mistral Pixtral     - AI vision, free tier, no rate limits
      2. EasyOCR             - deep learning OCR, Python 3.14 compatible
      3. TrOCR               - Microsoft transformer OCR, Python 3.14 compatible

    When Mistral is available: reads image directly, returns structured JSON.
    When Mistral fails/unavailable: EasyOCR + TrOCR both run, outputs merged,
    then a generic regex parser extracts invoice fields.
    No invoice data is ever hardcoded.
    """

    def __init__(self):
        self.trocr = _load_trocr()
        self.easy = _load_easy()

        has_api = bool(MISTRAL_API_KEY or os.environ.get("MISTRAL_API_KEY"))
        has_local = self.trocr is not None or self.easy is not None

        if not has_api and not has_local:
            raise RuntimeError(
                "No OCR engine available.\n"
                "Option A: Set MISTRAL_API_KEY (free at https://console.mistral.ai)\n"
                "Option B: pip install transformers torch pillow  (TrOCR)\n"
                "Option C: pip install easyocr"
            )





    def preprocess_image(self, image_path: str) -> np.ndarray:
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Cannot read image: {image_path}")

        h, w = img.shape[:2]
        if max(h, w) < 2000:
            scale = 2000 / max(h, w)
            img = cv2.resize(img, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_CUBIC)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
        sharpened = cv2.filter2D(gray, -1, kernel)
        _, binary = cv2.threshold(sharpened, 0, 255,
                                  cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)





    def _run_easy(self, image_path: str) -> str:
        if self.easy is None:
            return ""
        try:
            results = self.easy.readtext(image_path, detail=1, paragraph=False,
                                         width_ths=0.5, height_ths=0.5)
            tokens = []
            for bbox_pts, text, score in results:
                if text.strip() and score > 0.2:
                    ys = [pt[1] for pt in bbox_pts]
                    xs = [pt[0] for pt in bbox_pts]
                    tokens.append((min(xs), min(ys), max(xs), max(ys),
                                   text.strip(), float(score)))
            text = _tokens_to_text(tokens)
            logger.info(f"EasyOCR extracted {len(tokens)} tokens.")
            return text
        except Exception as e:
            logger.error(f"EasyOCR error: {e}")
            return ""

    def _run_trocr(self, image_path: str) -> str:
        """
        TrOCR processes the image line by line (line-level OCR model).
        Slices image into text rows and runs each through the model.
        """
        if self.trocr is None:
            return ""
        try:
            import torch
            from PIL import Image

            processor = self.trocr["processor"]
            model = self.trocr["model"]

            pil_img = Image.open(image_path).convert("RGB")
            img_array = np.array(pil_img)
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)

            _, binary = cv2.threshold(gray, 0, 255,
                                      cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            row_sums = np.sum(binary, axis=1)
            threshold = np.max(row_sums) * 0.05

            in_line = False
            line_starts, line_ends = [], []
            for i, s in enumerate(row_sums):
                if not in_line and s > threshold:
                    in_line = True
                    line_starts.append(i)
                elif in_line and s <= threshold:
                    in_line = False
                    line_ends.append(i)
            if in_line:
                line_ends.append(len(row_sums))

            merged_starts, merged_ends = [], []
            for s, e in zip(line_starts, line_ends):
                if merged_ends and s - merged_ends[-1] < 8:
                    merged_ends[-1] = e
                else:
                    merged_starts.append(s)
                    merged_ends.append(e)

            lines_text = []
            w = pil_img.width
            for s, e in zip(merged_starts, merged_ends):
                crop = pil_img.crop((0, max(0, s - 4), w, min(pil_img.height, e + 4)))
                if crop.height < 8:
                    continue
                pixel_values = processor(images=crop, return_tensors="pt").pixel_values
                with torch.no_grad():
                    generated_ids = model.generate(pixel_values)
                line_text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
                if line_text.strip():
                    lines_text.append(line_text.strip())

            result = "\n".join(lines_text)
            logger.info(f"TrOCR extracted {len(lines_text)} lines.")
            return result

        except Exception as e:
            logger.error(f"TrOCR error: {e}")
            return ""





    def extract_text(self, image_path: str) -> str:
        """Run ALL available local OCR engines and merge their outputs."""
        preprocessed = self.preprocess_image(image_path)
        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        cv2.imwrite(tmp.name, preprocessed)
        tmp_path = tmp.name
        tmp.close()

        texts = []
        engines_used = []
        try:
            easy_text = self._run_easy(tmp_path)
            if easy_text:
                texts.append(easy_text)
                engines_used.append("EasyOCR")

            trocr_text = self._run_trocr(tmp_path)
            if trocr_text:
                texts.append(trocr_text)
                engines_used.append("TrOCR")
        finally:
            os.unlink(tmp_path)

        logger.info(f"Local OCR engines used: {', '.join(engines_used) if engines_used else 'none'}")
        return _merge_texts(texts)





    def _regex_parse(self, text: str) -> dict:
        logger.info("Parsing with regex fallback (no hardcoded values).")

        invoice_data = {
            "invoice_number": None,
            "date": None,
            "due_date": None,
            "vendor_name": None,
            "vendor_address": None,
            "customer_name": None,
            "customer_address": None,
            "items": [],
            "subtotal": None,
            "tax": None,
            "total": None,
            "currency": None,
            "payment_terms": None,
            "notes": None,
            "construction_specific": {},
            "ocr_engine": self._active_engines(),
            "parser": "regex-fallback",
        }


        for pattern in [
            r'(?:invoice\s*(?:no|number|#)[:\s#]*)\s*([A-Z0-9\-/]+)',
            r'#\s*([A-Z0-9\-]{4,})',
            r'([A-Z]{2,}-\d{3,}-\d{2,})',
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                invoice_data["invoice_number"] = m.group(1).strip()
                break


        for pattern in [
            r'(?:invoice\s+date|date\s+issued|date)[:\s]+([A-Za-z]+\s+\d{1,2},?\s*\d{4})',
            r'(?:invoice\s+date|date\s+issued|date)[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
            r'([A-Za-z]+\s+\d{1,2},?\s*\d{4})',
            r'(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                invoice_data["date"] = m.group(1).strip()
                break


        for pattern in [
            r'(?:due\s+date|payment\s+due)[:\s]+([A-Za-z]+\s+\d{1,2},?\s*\d{4})',
            r'(?:due\s+date|payment\s+due)[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                invoice_data["due_date"] = m.group(1).strip()
                break


        m = re.search(
            r'(?:from|seller|vendor|bill\s+from)[:\s]+([A-Za-z][A-Za-z0-9\s&.,\'-]{2,40}?)(?=\s+bill\s+to|\s+address|\n|$)',
            text, re.IGNORECASE)
        if m:
            invoice_data["vendor_name"] = m.group(1).strip()


        m = re.search(r'address\s*[:\s]+([^\n]{5,80})', text, re.IGNORECASE)
        if m:
            invoice_data["vendor_address"] = m.group(1).strip()


        m = re.search(
            r'(?:bill\s+to|sold\s+to|customer|client)[:\s]+([A-Za-z][A-Za-z0-9\s&.,\'-]{2,40}?)(?=\s+address|\n|$)',
            text, re.IGNORECASE)
        if m:
            invoice_data["customer_name"] = m.group(1).strip()


        for label, key in [
            (r'subtotal', 'subtotal'),
            (r'tax(?:\s*\(?\d*\.?\d*%?\)?)?', 'tax'),
            (r'grand\s+total', 'total'),
        ]:
            m = re.search(rf'{label}\s*[:\s]*[S$]?\s*([\d,]+\.?\d*)', text, re.IGNORECASE)
            if m:
                invoice_data[key] = m.group(1).replace(',', '')


        for key in ('subtotal', 'total'):
            v = invoice_data.get(key)
            if v and v.startswith('5') and len(v) >= 5 and re.match(r'^\d+$', v):
                invoice_data[key] = v[1:]


        m = re.search(r'payment\s+is\s+due\s+(.+?)(?:\.|$)', text, re.IGNORECASE)
        if m:
            invoice_data["payment_terms"] = m.group(1).strip()


        skip = r'(?:description|item|service|qty|quantity|price|amount|no\.|subtotal|tax|total|notes|payment|bank|thank)'
        numbered = re.compile(
            r'^\s*\d+\s+([A-Za-z][A-Za-z0-9 \-/&]{3,40}?)\s+'
            r'(\d+[\.,]?\d*\s*(?:days?|weeks?|hrs?|hours?|units?|pcs?|tons?|kg|m2|cubics?))\s+'
            r'[S$]?\s*([\d,]+\.?\d*)\s+[S$]?\s*([\d,]+\.?\d*)',
            re.IGNORECASE | re.MULTILINE,
        )
        unnumbered = re.compile(
            r'^([A-Za-z][A-Za-z0-9 \-/&]{3,40}?)\s+'
            r'(\d+[\.,]?\d*\s*(?:days?|weeks?|hrs?|hours?|units?|pcs?|tons?|kg|m2|cubics?))\s+'
            r'[S$]?\s*([\d,]+\.?\d*)\s+[S$]?\s*([\d,]+\.?\d*)',
            re.IGNORECASE | re.MULTILINE,
        )

        seen = set()
        for pattern in (numbered, unnumbered):
            for m in pattern.finditer(text):
                desc = m.group(1).strip()
                if re.match(skip, desc, re.IGNORECASE):
                    continue
                if desc.lower() in seen:
                    continue
                seen.add(desc.lower())
                invoice_data["items"].append({
                    "description": desc,
                    "quantity":    m.group(2).strip(),
                    "unit_price":  m.group(3).replace(',', ''),
                    "total":       m.group(4).replace(',', ''),
                })

        return invoice_data





    def process_invoice(self, image_path: str) -> dict:
        """
        Process invoice using all available engines.

        Flow:
          1. Mistral Pixtral -> structured JSON directly from image (best accuracy)
          2. If Mistral fails -> EasyOCR + TrOCR both run, outputs merged -> regex parse
        """
        logger.info(f"Processing invoice: {image_path}")


        mistral_result = _call_mistral_vision(image_path)
        if mistral_result is not None:
            mistral_result.setdefault("construction_specific", {})
            mistral_result.setdefault("items", [])
            mistral_result["ocr_engine"] = "mistral-vision"
            mistral_result["parser"] = "mistral-vision"
            mistral_result["raw_text"] = ""
            return mistral_result


        logger.warning("Mistral unavailable - merging all local OCR engines.")
        print("[INFO] Mistral failed, falling back to local OCR (TrOCR + EasyOCR)")
        raw_text = self.extract_text(image_path)
        result = self._regex_parse(raw_text)
        result["raw_text"] = raw_text
        return result

    def _active_engines(self) -> str:
        engines = []
        if MISTRAL_API_KEY or os.environ.get("MISTRAL_API_KEY"):
            engines.append("Mistral Vision")
        if self.easy is not None:
            engines.append("EasyOCR")
        if self.trocr is not None:
            engines.append("TrOCR")
        return " + ".join(engines) if engines else "none"