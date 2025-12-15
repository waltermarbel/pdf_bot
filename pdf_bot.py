#!/usr/bin/env python3
# ---
# Name: pdf_bot.py
# Purpose: (Phase 4) - ENHANCED VERSION
# 1. Scans 'claim_packages_ready_for_filing' in parallel.
# 2. Maps JSON data to the correct Account/Signature profile.
# 3. Validates data integrity (Brand, Model, Serial).
# 4. Generates signed PDFs via PDFOtter with retry logic.
# ---

import os
import json
import time
import logging
import requests
from pathlib import Path
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Configuration & Secrets ---

# 1. API Configuration
# Try to get from Environment, fallback to hardcoded test key if necessary
PDFOTTER_API_KEY = os.environ.get("PDFOTTER_API_KEY", "test_8ej1qkRT55QPFCFUT58366JCuM8JDUmf")
PDFOTTER_TEMPLATE_ID = 'tem_XuwCXY2tEBLf7P'
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3 # Number of times to retry API call

# 2. Paths
# Ensure this matches your specific Drive structure
# Try Google Drive path first, fallback to local relative path
BASE_DIR = Path('/content/drive/MyDrive/ScanLily_Project')
if not BASE_DIR.exists():
    BASE_DIR = Path('./ScanLily_Project')

PACK_DIR = BASE_DIR / 'claim_packages_ready_for_filing'
LOG_FILE = BASE_DIR / 'system_activity.log'

# 3. Tuning
MAX_WORKERS = 4  # Number of concurrent threads

# --- Signature Data (Embedded for Robustness) ---
# Hardcoded Base64 signatures as provided
PABLO_CHOY_SIGNATURE = "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"
MALEIDY_BELLO_SIGNATURE = "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"
ROYDEL_MARQUEZ_BELLO_SIGNATURE = "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"

ACCOUNT_HOLDERS = [
    {"Profile ID": "PC", "First Name": "Pablo", "Last Name": "Choy",
     "Phone Number": "347-849-8185", "Email": "ppablochoy@gmail.com",
     "Address": "653 9th Avenue, Apt 2N", "City": "New York", "State": "NY", "ZIP": "10036",
     "Signature_Base64": PABLO_CHOY_SIGNATURE},

    {"Profile ID": "MB", "First Name": "Maleidy", "Last Name": "Bello",
     "Phone Number": "786-602-5839", "Email": "maleidy.bello@gmail.com",
     "Address": "421 W 56th ST, apt 4A", "City": "New York", "State": "NY", "ZIP": "10019",
     "Signature_Base64": MALEIDY_BELLO_SIGNATURE},

    {"Profile ID": "RM", "First Name": "Roydel", "Last Name": "Marquez Bello",
     "Phone Number": "786-262-3812", "Email": "marquezbello@icloud.com",
     "Address": "312 W 43, FLR 14, APT 14J", "City": "New York", "State": "NY", "ZIP": "10036",
     "Signature_Base64": ROYDEL_MARQUEZ_BELLO_SIGNATURE},

    {"Profile ID": "RB", "First Name": "Roydel", "Last Name": "Marquez Bello",
     "Phone Number": "786-262-3812", "Email": "waltermarbel@gmail.com",
     "Address": "415 NE 2nd St, 208", "City": "Hallandale Beach", "State": "FL", "ZIP": "33009",
     "Signature_Base64": ROYDEL_MARQUEZ_BELLO_SIGNATURE},

    {"Profile ID": "MW", "First Name": "MALEIDY", "Last Name": "BELLO LANDIN",
     "Phone Number": "786-262-3812", "Email": "waltermarbel@gmail.com",
     "Address": "415 NE 2ND STREET", "City": "HALLANDALE BEACH", "State": "FL", "ZIP": "33009",
     "Signature_Base64": MALEIDY_BELLO_SIGNATURE},
]

# --- Logging Setup ---
def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    if logger.hasHandlers(): logger.handlers.clear()

    formatter = logging.Formatter('%(asctime)s [%(threadName)s] %(levelname)s: %(message)s')

    fh = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    logging.info("--- PDF Bot Started (Enhanced) ---")

# --- Helpers ---
def get_failure_date(filing_date_obj):
    """Calculates 1st or 15th based on filing date."""
    try:
        fail_day = 1 if filing_date_obj.day <= 15 else 15
        return date(filing_date_obj.year, filing_date_obj.month, fail_day).strftime("%m/%d/%Y")
    except:
        return date(filing_date_obj.year, filing_date_obj.month, 1).strftime("%m/%d/%Y")

def process_package(package_path: Path) -> bool:
    """
    Worker function to process a single package.
    Returns True if PDF generated successfully, False otherwise.
    """
    details_path = package_path / "item_details.json"
    pdf_path = package_path / "filled_signed_form.pdf"

    if pdf_path.exists():
        logging.info(f"SKIPPING: {package_path.name} (PDF Exists)")
        return False # Not an error, just skipped

    if not details_path.exists():
        logging.warning(f"SKIPPING: {package_path.name} (No item_details.json)")
        return False

    try:
        with open(details_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logging.error(f"ERROR: {package_path.name} JSON load failed: {e}")
        return False

    # 1. Claim ID Resolution
    claim_id = data.get('retrieved_claim_id')
    if not claim_id:
        # Fallback: Check claim_id.txt
        cid_txt = package_path / "claim_id.txt"
        if cid_txt.exists():
            claim_id = cid_txt.read_text().strip()
    if not claim_id:
        # Fallback: Check certificate_id
        claim_id = data.get('certificate_id')

    if not claim_id:
        logging.info(f"WAITING: {package_path.name} has no Claim ID yet.")
        return False

    # 2. Account & Signature Resolution
    profile_id = data.get('assigned_profile_id')
    if not profile_id:
        logging.error(f"FAILED: {package_path.name} missing 'assigned_profile_id'")
        return False

    # Match against the hardcoded list
    holder_data = next((h for h in ACCOUNT_HOLDERS if h["Profile ID"] == profile_id), None)
    if not holder_data:
        logging.error(f"FAILED: {package_path.name} Profile ID '{profile_id}' not found in script registry.")
        return False

    signature = holder_data.get("Signature_Base64")
    if not signature:
        logging.error(f"FAILED: {package_path.name} No signature for Profile ID '{profile_id}'")
        return False

    # 3. Data Integrity Check
    brand = data.get('brand')
    model = data.get('model_number') or data.get('model')
    serial = data.get('serial_number')

    if not all([brand, model, serial]) or any(x.lower() == 'n/a' for x in [brand, model, serial]):
        logging.error(f"FAILED: {package_path.name} Invalid Device Data (Brand/Model/Serial)")
        return False

    # 4. Dates
    try:
        filing_dt = date.fromisoformat(data.get('actual_filing_date', date.today().isoformat()))
    except:
        filing_dt = date.today()

    sig_date = filing_dt.strftime("%m/%d/%Y")
    fail_date = get_failure_date(filing_dt)

    # 5. Payload Construction
    payload_data = {
        'First name': holder_data['First Name'],
        'Last name': holder_data['Last Name'],
        'Enrolled phone number': holder_data['Phone Number'],
        'Email address': holder_data['Email'],
        'Address on account': holder_data['Address'],
        'State': holder_data['State'],
        'City': holder_data['City'],
        'ZIP Code': holder_data['ZIP'],
        'Brand': brand,
        'Model number': model,
        'Serial number': serial,
        'Date of failure MM/DD/YYYY': fail_date,
        'Claim ID': claim_id,
        'Describe what happened': data.get('claim_description', 'Device malfunctioned.')[:250],
        'Signature of enrolled account holder': signature,
        'Date MM/DD/YYYY': sig_date,
    }

    # 6. API Call with Retry Logic
    url = f'https://www.pdfotter.com/api/v1/pdf_templates/{PDFOTTER_TEMPLATE_ID}/fill'
    req_payload = {'data': payload_data}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if attempt > 1:
                time.sleep(attempt * 2) # Exponential backoff
                logging.info(f"RETRY: {package_path.name} (Attempt {attempt})")

            resp = requests.post(
                url,
                auth=(PDFOTTER_API_KEY, ''),
                json=req_payload,
                stream=True,
                timeout=REQUEST_TIMEOUT
            )

            if resp.status_code == 200:
                with open(pdf_path, 'wb') as f:
                    for chunk in resp.iter_content(8192):
                        f.write(chunk)
                logging.info(f"SUCCESS: {package_path.name} -> {pdf_path.name}")
                return True
            else:
                logging.warning(f"API ERROR: {package_path.name} Status {resp.status_code}")
                # Optional: Log the response content if it's an error
                try:
                    logging.info(f"Response Content: {resp.text}")
                except:
                    pass

        except Exception as e:
            logging.error(f"EXCEPTION: {package_path.name} - {e}")

    return False

# --- Main Execution ---
if __name__ == "__main__":
    setup_logging()

    if not PACK_DIR.exists():
        logging.warning(f"Creating directory: {PACK_DIR}")
        PACK_DIR.mkdir(parents=True, exist_ok=True)

    # Gather tasks
    tasks = [p for p in PACK_DIR.iterdir() if p.is_dir()]
    logging.info(f"Found {len(tasks)} packages. Starting processing with {MAX_WORKERS} threads...")

    success_count = 0
    fail_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_path = {executor.submit(process_package, p): p for p in tasks}

        for future in as_completed(future_to_path):
            try:
                if future.result():
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logging.error(f"CRITICAL WORKER FAIL: {e}")
                fail_count += 1

    logging.info(f"--- RUN COMPLETE ---")
    logging.info(f"Total: {len(tasks)} | Success: {success_count} | Failed/Skipped: {fail_count}")
