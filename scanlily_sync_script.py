#!/usr/bin/env python3
# ---
# Name: scanlily_sync_script.py
# Purpose: (Phase 1 & 6)
# 1. Scrapes ScanLily inventory.
# 2. Compares ScanLily flags/statuses to local package directories.
# 3. Creates new 'Ready' packages if a "Broken" trigger is set and no package exists.
# 4. Archives 'Ready' packages if a "Done" trigger is set.
# 5. Resets (deletes) 'Ready' packages if a "Neutral" trigger is set AND no Claim ID has been recorded.
# ---
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import json
import time # To add delays
import os
import re # For cleaning provider names
import shutil # For downloading images and moving folders
from datetime import datetime, date # For generating dates
from pathlib import Path # For creating output directory and paths
import logging # --- NEW: Added for robust logging ---
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Configuration ---
MAIN_INVENTORY_URL = "https://s.scanlily.com/inventory/MzkyNDo4ZTllMDE0NzljYzk4Nzcw000000000000000en"
DELAY_BETWEEN_REQUESTS = 0.5 # Can be faster, just checking flags

# --- Define a base directory ---
# Try Google Drive path first, fallback to local relative path
BASE_DIR = Path('/content/drive/MyDrive/ScanLily_Project')
if not BASE_DIR.exists():
    BASE_DIR = Path('./ScanLily_Project')

# --- Updated Paths to save to Google Drive ---
SUMMARY_OUTPUT_FILENAME = BASE_DIR / 'scanlily_sync_summary.json'
CLAIM_PACKAGES_DIR = BASE_DIR / 'claim_packages_ready_for_filing'
CLAIM_PACKAGES_ARCHIVED_DIR = BASE_DIR / 'claim_packages_archived'
LOG_FILE = BASE_DIR / 'system_activity.log'# --- NEW: Central log file ---

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/91.0.4472.114 Safari/537.36'
    )
}
REQUEST_TIMEOUT = 25

# --- Flag & Status Logic ---
CREATE_PACKAGE_FLAGS = ['broken', 'need to service']
CREATE_PACKAGE_STATUSES = ['out of service', 'limited use', 'failed calibration']
ARCHIVE_PACKAGE_FLAGS = ['done', 'dispose', 'lost', 'restock', 'approved', 'rejected'] # Added approved/rejected
ARCHIVE_PACKAGE_STATUSES = ['in service']

# --- Data Structures ---

# Policy Rules (Expanded with fallbacks)
POLICY_RULES = {
    "blood pressure monitor": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "computer speakers": {"recommended_providers": ["Verizon", "Asurion", "Tmobile"], "accidental_damage_covered": False},
    "dvd/blu-ray player": {"recommended_providers": ["Verizon", "Asurion", "Tmobile"], "accidental_damage_covered": True},
    "external hard drive": {"recommended_providers": ["Asurion", "Tmobile"], "accidental_damage_covered": False},
    "keyboard": {"recommended_providers": ["Verizon", "Asurion", "Tmobile"], "accidental_damage_covered": False},
    "mesh router": {"recommended_providers": ["Asurion", "Tmobile"], "accidental_damage_covered": False},
    "modem": {"recommended_providers": ["Verizon", "Asurion"], "accidental_damage_covered": False},
    "monitor": {"recommended_providers": ["Verizon", "Asurion"], "accidental_damage_covered": False},
    "mouse": {"recommended_providers": ["Verizon", "Asurion", "Tmobile"], "accidental_damage_covered": False},
    "personal ekg monitor": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "portable dvd/blu-ray player": {"recommended_providers": ["Verizon", "Asurion", "Tmobile"], "accidental_damage_covered": True},
    "printer displays": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "pulse oximeter": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "range extender": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "receiver": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "remote control": {"recommended_providers": ["Verizon", "Asurion", "Tmobile"], "accidental_damage_covered": False},
    "router (non-mesh)": {"recommended_providers": ["Verizon", "Asurion", "Tmobile"], "accidental_damage_covered": False},
    "smart alarm contact sensor": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "smart alarm flood/freeze": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "smart alarm range extender": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "smart carbon monoxide": {"recommended_providers": ["Verizon", "Asurion"], "accidental_damage_covered": False},
    "smart hub": {"recommended_providers": ["Verizon", "Asurion"], "accidental_damage_covered": False},
    "smart light dimmer": {"recommended_providers": ["Verizon", "Asurion"], "accidental_damage_covered": False},
    "smart smoke detector": {"recommended_providers": ["Verizon", "Asurion"], "accidental_damage_covered": False},
    "video game controller": {"recommended_providers": ["Verizon", "Asurion", "Tmobile"], "accidental_damage_covered": False},
    "video streaming device": {"recommended_providers": ["Verizon", "Asurion"], "accidental_damage_covered": False},
    "smart alarm keypad": {"recommended_providers": ["Asurion", "Tmobile"], "accidental_damage_covered": False},
    "smart alarm panic button": {"recommended_providers": ["Asurion", "Tmobile"], "accidental_damage_covered": False},
    "smart door lock": {"recommended_providers": ["Asurion", "Tmobile"], "accidental_damage_covered": False},
    "smart glass break sensor": {"recommended_providers": ["Asurion", "Tmobile"], "accidental_damage_covered": False},
    "smart motion sensor": {"recommended_providers": ["Asurion", "Tmobile"], "accidental_damage_covered": False},
    "smart security camera": {"recommended_providers": ["Asurion", "Tmobile"], "accidental_damage_covered": False},
    "smart thermostat": {"recommended_providers": ["Asurion", "Tmobile"], "accidental_damage_covered": False},
    "smart video doorbell": {"recommended_providers": ["Asurion", "Tmobile"], "accidental_damage_covered": False},
    "amplifier": {"recommended_providers": ["Verizon", "Tmobile"], "accidental_damage_covered": False},
    "automatic fetch machine": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "fitness ring": {"recommended_providers": ["Verizon", "Tmobile"], "accidental_damage_covered": False},
    "fitness tracker": {"recommended_providers": ["Verizon", "Tmobile"], "accidental_damage_covered": True},
    "headphones / ear buds": {"recommended_providers": ["Verizon", "Tmobile"], "accidental_damage_covered": True},
    "smart pet food dispenser": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "smart watch": {"recommended_providers": ["Verizon", "Tmobile"], "accidental_damage_covered": True},
    "desktop (all in one)": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "desktop (tower)": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "digital cameras": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "handheld video game player": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": True},
    "home theater system": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "laptop": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": True},
    "printer/scanner": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "soundbar": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "speakers": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "tablet": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "television": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "tuner": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "video game console": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "vr headset": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "ar glasses": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "smart floor care": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "smart pet collar": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "router": {"recommended_providers": ["Verizon", "Asurion", "Tmobile"], "accidental_damage_covered": False},
    "headphones": {"recommended_providers": ["Verizon", "Tmobile"], "accidental_damage_covered": True},
    "desktop": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "printer": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "tv": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "robotic vacuum": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "smart floor care/robotic vacuum": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "wireless access point": {"recommended_providers": ["Verizon", "Asurion", "Tmobile"], "accidental_damage_covered": False},
    "hard drive": {"recommended_providers": ["Asurion", "Tmobile"], "accidental_damage_covered": False},
    "motherboard": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "carpet cleaner": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "ice maker": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "media player": {"recommended_providers": ["Verizon", "Asurion", "Tmobile"], "accidental_damage_covered": True},
    "network device": {"recommended_providers": ["Verizon", "Asurion", "Tmobile"], "accidental_damage_covered": False},
    "smart lock": {"recommended_providers": ["Asurion", "Tmobile"], "accidental_damage_covered": False},
    "computer component": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "electronic component": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "display unit": {"recommended_providers": ["Verizon", "Asurion"], "accidental_damage_covered": False},
    "hdtv": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "gaming desktop tower": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "lcd monitor": {"recommended_providers": ["Verizon", "Asurion"], "accidental_damage_covered": False},
    "robotic vacuum cleaner": {"recommended_providers": ["Verizon"], "accidental_damage_covered": False},
    "range extender/booster/repeater": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "audio": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "office": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
    "other": {"recommended_providers": ["Tmobile"], "accidental_damage_covered": False},
}

# --- NEW: Signature Variables ---
# Signatures populated from PDF Bot configuration
PABLO_CHOY_SIGNATURE = "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"
MALEIDY_BELLO_SIGNATURE = "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"
ROYDEL_MARQUEZ_BELLO_SIGNATURE = "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"

# Unified Account Holder Data (Now uses signature variables)
ACCOUNT_HOLDERS = [
    {"Profile ID": "PC", "First Name": "Pablo", "Last Name": "Choy",
     "Phone Number": "347-849-8185", "Email": "ppablochoy@gmail.com",
     "Address": "653 9th Avenue, Apt 2N", "City": "New York", "State": "NY", "ZIP": "10036",
     "Plan Type": "Asurion", "Password": "Cubano280!",
     "Photo Front ID": "https://scn3.nyc3.digitaloceanspaces.com/3924/image/img_251107193344.jpeg",
     "Photo Back ID": "https://scn3.nyc3.digitaloceanspaces.com/3924/image/img_251107193346.jpeg",
     "URL for claims": "https://www.asurion.com/my-devices/landing?mxclient=asurion&mxproducttype=home",
     "Signature_Base64": PABLO_CHOY_SIGNATURE},

    {"Profile ID": "MB", "First Name": "Maleidy", "Last Name": "Bello",
     "Phone Number": "786-602-5839", "Email": "maleidy.bello@gmail.com",
     "Address": "421 W 56th ST, apt 4A", "City": "New York", "State": "NY", "ZIP": "10019",
     "Plan Type": "Asurion", "Password": "Majagua1970$",
     "Photo Front ID": "https://scn3.nyc3.digitaloceanspaces.com/3924/image/img_251107193362.jpeg",
     "Photo Back ID": "https://scn3.nyc3.digitaloceanspaces.com/3924/image/img_251107193326.jpeg",
     "URL for claims": "https://www.asurion.com/my-devices/landing?mxclient=asurion&mxproducttype=home",
     "Signature_Base64": MALEIDY_BELLO_SIGNATURE},

    {"Profile ID": "RM", "First Name": "Roydel", "Last Name": "Marquez Bello",
     "Phone Number": "786-262-3812", "Email": "marquezbello@icloud.com",
     "Address": "312 W 43, FLR 14, APT 14J", "City": "New York", "State": "NY", "ZIP": "10036",
     "Plan Type": "Verizon", "Password": "Maleroy028!",
     "Photo Front ID": "https://scn3.nyc3.digitaloceanspaces.com/3924/image/img_251107193648.jpg",
     "Photo Back ID": "https://scn3.nyc3.digitaloceanspaces.com/3924/image/img_251107193616.jpg",
     "URL for claims": "https://www.asurion.com/my-devices?mxclient=verizon",
     "Signature_Base64": ROYDEL_MARQUEZ_BELLO_SIGNATURE},

    {"Profile ID": "RB", "First Name": "Roydel", "Last Name": "Marquez Bello",
     "Phone Number": "786-262-3812", "Email": "waltermarbel@gmail.com",
     "Address": "415 NE 2nd St, 208", "City": "Hallandale Beach", "State": "FL", "ZIP": "33009",
     "Plan Type": "Verizon", "Password": "Maleroy028!",
     "Photo Front ID": "https://scn3.nyc3.digitaloceanspaces.com/3924/image/img_251107193648.jpg",
     "Photo Back ID": "https://scn3.nyc3.digitaloceanspaces.com/3924/image/img_251107193616.jpg",
     "URL for claims": "https://www.asurion.com/my-devices?mxclient=verizon",
     "Signature_Base64": ROYDEL_MARQUEZ_BELLO_SIGNATURE}, # Assuming RB uses RM's signature

    {"Profile ID": "MW", "First Name": "MALEIDY", "Last Name": "BELLO LANDIN",
     "Phone Number": "786-262-3812", "Email": "waltermarbel@gmail.com",
     "Address": "415 NE 2ND STREET", "City": "HALLANDALE BEACH", "State": "FL", "ZIP": "33009",
     "Plan Type": "Tmobile", "Password": "Maleroy028!",
     "Photo Front ID": "https://scn3.nyc3.digitaloceanspaces.com/3924/image/img_251107193362.jpeg",
     "Photo Back ID": "https://scn3.nyc3.digitaloceanspaces.com/3924/image/img_251107193326.jpeg",
     "URL for claims": "https://tmobile.assurant.com/device-select",
     "Signature_Base64": MALEIDY_BELLO_SIGNATURE}, # Assuming MW uses MB's signature
]


# --- NEW: Logging Setup ---
def setup_logging():
    """Configures logging to file and console."""
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO) # Set base level

    # Prevent duplicate handlers if script is re-run in same session
    if logger.hasHandlers():
        logger.handlers.clear()

    # Create file handler (append mode)
    file_handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
    file_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_format)
    file_handler.setLevel(logging.INFO) # Log info level and above to file
    logger.addHandler(file_handler)

    # Create console handler
    console_handler = logging.StreamHandler()
    console_format = logging.Formatter('%(message)s') # Simpler format for console
    console_handler.setFormatter(console_format)
    console_handler.setLevel(logging.INFO) # Also show info level in console
    logger.addHandler(console_handler)

    logging.info("\n--- Logging started for new run ---")

# --- Helper Functions ---
def get_failure_date_str():
    """Calculates potential failure date based on *today*"""
    today = date.today()
    fail_day = 1 if today.day <= 15 else 15
    try: failure_date = date(today.year, today.month, fail_day)
    except ValueError: failure_date = date(today.year, today.month, 1)
    return failure_date.strftime("%m/%d/%Y")

def sanitize_filename(name):
    """Removes invalid characters for filenames and limits length."""
    if not name: name = "untitled"
    sanitized = re.sub(r'[<>:"/\\|?*\s]+', '_', name)
    sanitized = sanitized.strip('_')
    return sanitized[:100]

def download_image(url, filepath):
    """Downloads an image, handling potential errors including 403."""
    if not url:
        return False, "No URL provided"

    parsed_url = urlparse(url)
    if 'drive.google.com' in parsed_url.netloc and '/view' in parsed_url.path:
         warning_msg = f"URL is GDrive view link, not direct. Manual download required: {url[:70]}..."
         logging.warning(f"    Skipping {filepath.name}. {warning_msg}")
         return False, warning_msg
    elif 'drive.google.com' in parsed_url.netloc and '/uc?' not in url:
         warning_msg = f"URL might not be direct GDrive link (missing /uc?). Manual download required: {url[:70]}..."
         logging.warning(f"    Skipping {filepath.name}. {warning_msg}")
         return False, warning_msg

    try:
        logging.info(f"    Downloading {filepath.name} from {url[:60]}...")
        temp_headers = HEADERS.copy()
        temp_headers['Referer'] = 'https://s.scanlily.com/'

        response = requests.get(url, headers=temp_headers, stream=True, timeout=REQUEST_TIMEOUT, verify=False)

        if response.status_code == 403:
            err_msg = f"Forbidden (403). Direct download likely blocked for {url[:60]}..."
            logging.error(f"    {err_msg}")
            return False, err_msg

        response.raise_for_status()

        content_type = response.headers.get('content-type', '').lower()
        allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'application/pdf', 'application/octet-stream']
        is_allowed_type = any(allowed in content_type for allowed in allowed_types)

        if content_type and not is_allowed_type:
             warning_msg = f"URL content type ({content_type}) not recognized as image/pdf. Skipping {filepath.name}."
             logging.warning(f"    {warning_msg}")
             return False, warning_msg

        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192): f.write(chunk)
        logging.info(f"    Successfully downloaded {filepath.name}")
        return True, None
    except requests.exceptions.Timeout: err_msg = f"Timeout downloading {url[:60]}..."; logging.error(f"    {err_msg}"); return False, err_msg
    except requests.exceptions.RequestException as e: err_msg = f"Request error: {e}"; logging.error(f"    Error downloading {filepath.name}: {err_msg}"); return False, err_msg
    except IOError as e: err_msg = f"File saving error: {e}"; logging.error(f"    Error saving {filepath.name}: {err_msg}"); return False, err_msg
    except Exception as e: err_msg = f"Unexpected download error: {e}"; logging.error(f"    {err_msg}"); return False, err_msg


def download_all_images(item_data, claim_folder_path):
    """Refactored function to download all images for a package."""
    download_errors = []
    image_download_status = {}
    assigned_profile_id = item_data.get('assigned_profile_id')
    holder_info = next((h for h in ACCOUNT_HOLDERS if h.get("Profile ID") == assigned_profile_id), None)

    logging.info("    Downloading item images (ScanLily links may fail with 403)...")
    img_map = {
        'item_label': item_data.get('device_label_photo'),
        'item_front': item_data.get('device_front_photo'),
        'item_back': item_data.get('device_back_photo'),
        'item_top': item_data.get('device_top_photo')
    }
    for img_key, img_url in img_map.items():
        img_success, err_msg = download_image(img_url, claim_folder_path / f'{img_key}.jpg')
        image_download_status[img_key] = {"success": img_success, "url": img_url, "error": err_msg}
        if not img_success and err_msg: download_errors.append(f"{img_key}: {err_msg}")

    if holder_info:
        logging.info("    Downloading account holder ID images (check GDrive/AppZend URL format)...")
        front_id_url = holder_info.get('Photo Front ID')
        img_success, err_msg = download_image(front_id_url, claim_folder_path / 'holder_id_front.jpg')
        image_download_status['holder_id_front'] = {"success": img_success, "url": front_id_url, "error": err_msg}
        if not img_success and err_msg: download_errors.append(f"holder_id_front: {err_msg}")

        back_id_url = holder_info.get('Photo Back ID')
        img_success, err_msg = download_image(back_id_url, claim_folder_path / 'holder_id_back.jpg')
        image_download_status['holder_id_back'] = {"success": img_success, "url": back_id_url, "error": err_msg}
        if not img_success and err_msg: download_errors.append(f"holder_id_back: {err_msg}")
    else:
        id_err = "Could not retrieve holder info for ID photo download."
        logging.critical(f"    {id_err}")
        download_errors.append(id_err)

    return image_download_status, download_errors


# --- Scraping & Assignment Functions ---
def scrape_inventory_item_urls(inventory_url):
    """Scrapes the main inventory page to find item URLs."""
    item_urls = []
    try:
        logging.info(f"Fetching main inventory page: {inventory_url}")
        response = requests.get(inventory_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, verify=False)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        product_grid = soup.find('div', class_='grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4')
        if not product_grid:
            logging.error("ERROR: Could not find product grid container.")
            return []

        product_divs = product_grid.find_all('div', class_='product', attrs={'data-item-id': True})
        if not product_divs:
            logging.warning("WARNING: No product divs with 'data-item-id' found.")
            return []

        logging.info(f"Found {len(product_divs)} item containers.")
        parsed_main_url = urlparse(inventory_url)
        base_url = f"{parsed_main_url.scheme}://{parsed_main_url.netloc}/"
        inventory_path = parsed_main_url.path.split('/product/')[0].strip('/')

        for product_div in product_divs:
            item_id = product_div.get('data-item-id')
            if item_id:
                item_path = f"{inventory_path}/product/{item_id}"
                full_item_url = urljoin(base_url, item_path)
                item_urls.append(full_item_url)

        logging.info(f"Extracted {len(item_urls)} item URLs.")
    except requests.exceptions.Timeout: logging.error(f"ERROR: Timeout fetching {inventory_url}")
    except requests.exceptions.RequestException as e: logging.error(f"ERROR: Request error fetching {inventory_url}: {e}")
    except Exception as e: logging.error(f"ERROR: Unexpected error parsing inventory page: {e}", exc_info=True)

    return item_urls


def assign_policy_and_holder(item_details, policy_rules, account_holders):
    """
    Assigns provider, profile_id, and ADH coverage.
    NEW LOGIC:
    1. Attempts to assign holder based on 'location' tag.
    2. If no location or no match, falls back to category-based POLICY_RULES.
    """
    item_details['assigned_provider'] = None
    item_details['assigned_profile_id'] = None
    item_details['accidental_damage_covered'] = False
    assigned = False # Flag to track if assignment was successful
    category = item_details.get('category')

    # --- 1. Category Inference (No change from original) ---
    if not category and item_details.get('device_name_id'):
        title_lower = item_details['device_name_id'].lower()
        if 'router' in title_lower: category = 'Router'
        elif 'modem' in title_lower: category = 'Modem'
        elif 'monitor' in title_lower: category = 'Monitor'
        elif 'tv' in title_lower or 'television' in title_lower: category = 'Television'
        elif 'printer' in title_lower: category = 'Printer'
        elif 'laptop' in title_lower or 'macbook' in title_lower: category = 'Laptop'
        elif 'desktop' in title_lower or 'imac' in title_lower: category = 'Desktop'
        elif 'hard drive' in title_lower: category = 'External Hard Drive'
        elif 'vacuum' in title_lower: category = 'Robotic Vacuum'
        elif 'headphones' in title_lower or 'airpods' in title_lower: category = 'Headphones'
        elif 'lock' in title_lower: category = 'Smart Lock'

        if category:
            item_details['category'] = category
            logging.info(f"    Note: Assigned category '{category}' based on title.")
        else:
            category = "Other"; item_details['category'] = category
            logging.warning(f"    Warning: Cannot infer category, assigning default '{category}'.")
    elif not category:
        category = "Other"; item_details['category'] = category
        logging.warning(f"    Warning: Category missing, assigning default '{category}'.")

    # --- 2. Get Policy Rule (Needed for ADH coverage and fallback logic) ---
    category_lower = category.lower().strip()
    rule = policy_rules.get(category_lower)
    if not rule: # Try partial match (No change from original)
        matched_key = None
        for key in policy_rules:
            if key != 'other' and key in category_lower:
                 rule = policy_rules[key]; matched_key = key; break
        if not rule:
            for key in policy_rules:
                 if key != 'other' and category_lower in key:
                     rule = policy_rules[key]; matched_key = key; break
        if not rule:
            rule = policy_rules.get('other'); matched_key = 'other' if rule else None
        if matched_key: logging.info(f"    Note: Using rule '{matched_key}' for category '{category}'.")
        elif not rule:
            logging.critical(f"    CRITICAL Warning: No rule found for '{category}' and no 'other' fallback."); return item_details

    # Assign Accidental Damage coverage regardless of assignment method
    item_details['accidental_damage_covered'] = rule.get('accidental_damage_covered', False)

    # --- 3. NEW: Attempt Assignment by Location Tag ---
    location_tag = item_details.get('location')
    if location_tag:
        logging.info(f"    Attempting assignment based on Location Tag: '{location_tag}'")
        location_lower = location_tag.lower()
        for holder in account_holders:
            # Check if location matches first name, last name, or profile ID
            holder_name_full = f"{holder.get('First Name', '')} {holder.get('Last Name', '')}".lower()
            holder_profile_id = holder.get('Profile ID', '').lower()

            if (location_lower in holder_name_full) or (location_lower == holder_profile_id):
                item_details['assigned_provider'] = holder.get('Plan Type')
                item_details['assigned_profile_id'] = holder.get('Profile ID')
                item_details['assigned_account_details'] = holder.copy()
                logging.info(f"    SUCCESS: Assigned by Location. Provider: {item_details['assigned_provider']}, Profile ID: {item_details['assigned_profile_id']}")
                assigned = True
                break # Stop on first match

    # --- 4. FALLBACK: Assign by Policy Rule (Original Logic) ---
    if not assigned:
        if location_tag:
            logging.warning(f"    Location '{location_tag}' did not match any holder. Falling back to policy rules.")
        else:
            logging.info(f"    No location tag found. Falling back to policy rules for category '{category}'.")

        if not rule.get('recommended_providers'):
             logging.warning(f"    Warning: No recommended providers for category rule '{category_lower}'."); return item_details

        for provider in rule['recommended_providers']:
            for holder in account_holders:
                holder_plan_type = holder.get('Plan Type')
                if holder_plan_type and holder_plan_type.strip().lower() == provider.lower():
                    item_details['assigned_provider'] = provider
                    item_details['assigned_profile_id'] = holder.get('Profile ID')
                    item_details['assigned_account_details'] = holder.copy()

                    if "PLACEHOLDER_" in holder.get("Signature_Base64", "PLACEHOLDER_"):
                        logging.critical(f"    CRITICAL Warning: Assigned Profile {holder.get('Profile ID')} has PLACEHOLDER signature.")

                    logging.info(f"    Assigned by Rule. Provider: {provider}, Profile ID: {item_details['assigned_profile_id']}")
                    assigned = True; break
            if assigned: break

    if not assigned:
        logging.warning(f"    FINAL Warning: No account holder found for any recommended provider {rule.get('recommended_providers')}.")

    return item_details

def scrape_item_details(item_url):
    """Scrapes all details for a single item, including its flag."""
    details = {'scanlily_url': item_url}
    try:
        logging.info(f"  Fetching item details from: {item_url}")
        response = requests.get(item_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, verify=False)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        container = soup.find('div', class_='container')
        if not container:
            details['error'] = "Main container not found."; logging.error(f"  Error: {details['error']} for {item_url}."); return details

        # Scrape Title, Category, Price
        title_tag = container.find('h1', class_='text-2xl')
        details['device_name_id'] = title_tag.text.strip() if title_tag else None
        category_tag = container.find('p', class_='text-gray-600', string=lambda t: t and 'Category:' in t)
        details['category'] = category_tag.text.replace('Category:', '').strip() if category_tag else None
        price_tag = container.find('p', class_='item-price')
        details['price'] = price_tag.text.strip().replace('$', '').replace(',', '') if price_tag else None

        # Scrape Specs (including Flag)
        key_map = {
             "brand": "brand", "model number": "model_number", "serial number": "serial_number",
             "color": "color", "location": "location", "barcode": "barcode",
             "flag": "flag", "certificate id": "certificate_id",
             "equipment status": "equipment_status"
        }
        specs_section = container.find('div', class_='bg-white rounded-lg shadow-lg p-6 mb-8')
        if specs_section:
            specs_div = specs_section.find('div', class_='space-y-4')
            if specs_div:
                for item in specs_div.find_all('div', class_='flex'):
                    label_span = item.find('span', class_=['font-medium', 'w-32', 'text-gray-700'])
                    value_span = item.find('span', class_='text-gray-800')

                    if label_span and value_span:
                        key_raw = label_span.text.strip().rstrip(':')
                        key_lower = key_raw.lower()
                        sheet_key = key_map.get(key_lower)
                        value = value_span.text.strip()
                        if sheet_key:
                            details[sheet_key] = value

        # Scrape Description
        description = None
        all_sections = container.find_all('div', class_='bg-white rounded-lg shadow-lg p-6 mb-8')
        for section in all_sections:
            header = section.find('h2', string=lambda t: t and 'Description' in t.strip())
            if header:
                desc_p = section.find('p', class_='text-gray-700')
                if desc_p: description = desc_p.text.strip(); break
        details['description'] = description

        # Scrape Images
        details['device_label_photo'] = None
        details['device_front_photo'] = None
        details['device_back_photo'] = None
        details['device_top_photo'] = None
        details['other_photo_urls'] = []
        main_img_tag = container.select_one('div.md\\:w-1\\/2 div.bg-white img.object-contain')
        if main_img_tag and main_img_tag.get('src') and 'digitaloceanspaces.com' in main_img_tag['src']:
            details['device_label_photo'] = main_img_tag['src']
        more_imgs_section = None
        for section in all_sections:
            header = section.find('h2', string=lambda t: t and 'More Images' in t.strip())
            if header: more_imgs_section = section; break
        if more_imgs_section:
            more_img_tags = more_imgs_section.select('div.grid img.object-contain')
            more_image_urls=[img.get('src') for img in more_img_tags if img.get('src') and 'digitaloceanspaces.com' in img['src']]
            keys_to_assign = ['device_front_photo', 'device_back_photo', 'device_top_photo']
            for i, url in enumerate(more_image_urls):
                if i < len(keys_to_assign): details[keys_to_assign[i]] = url
                else: details['other_photo_urls'].append(url)

        logging.info("    --- Scraped Details ---")
        for key, value in details.items():
             if key == 'description' and value and len(value) > 100: logging.info(f"    {key}: {value[:100]}...")
             elif key not in ['error', 'assigned_account_details']: logging.info(f"    {key}: {value}")
        logging.info("    -----------------------")

    except requests.exceptions.Timeout: details['error']="Request timed out."; logging.error(f"  Error: {details['error']} fetching {item_url}")
    except requests.exceptions.RequestException as e: details['error']=str(e); logging.error(f"  Error fetching {item_url}: {details['error']}")
    except Exception as e: details['error']=str(e); logging.error(f"  Error parsing {item_url}: {e}", exc_info=True)
    return details

def check_claim_id_exists(package_path):
    """Checks if a claim ID has been recorded in the package."""
    json_path = package_path / "item_details.json"
    txt_path = package_path / "claim_id.txt"

    if txt_path.exists():
        return True

    if json_path.exists():
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('retrieved_claim_id'):
                return True
        except Exception as e:
            logging.warning(f"    Could not read {json_path} to check for Claim ID: {e}")

    return False

# --- Main Execution Block ---
if __name__ == "__main__":

    # --- NEW: Create the base directory FIRST ---
    try:
        # BASE_DIR is defined above
        BASE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # Can't use logging yet, so just print
        print(f"CRITICAL: Could not create BASE_DIR: {e}. Exiting.")
        exit()

    setup_logging()  # --- NEW: Initialize logging (NOW it's safe to run)
    overall_summary = []

    try:
        # These will now create subfolders inside the existing BASE_DIR
        CLAIM_PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
        CLAIM_PACKAGES_ARCHIVED_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logging.critical(f"CRITICAL: Could not create sub-directories: {e}. Exiting.")
        exit()

    logging.info(f"--- Configuration ---")
    logging.info(f"Main Inventory URL: {MAIN_INVENTORY_URL}")
    logging.info(f"Delay: {DELAY_BETWEEN_REQUESTS}s")
    logging.info(f"Summary File: {SUMMARY_OUTPUT_FILENAME}")
    logging.info(f"Ready Directory: {CLAIM_PACKAGES_DIR.resolve()}")
    logging.info(f"Archive Directory: {CLAIM_PACKAGES_ARCHIVED_DIR.resolve()}")
    logging.info("--- Action Triggers ---")
    logging.info(f"CREATE on Flags: {CREATE_PACKAGE_FLAGS}")
    logging.info(f"CREATE on Status: {CREATE_PACKAGE_STATUSES}")
    logging.info(f"ARCHIVE on Flags: {ARCHIVE_PACKAGE_FLAGS}")
    logging.info(f"ARCHIVE on Status: {ARCHIVE_PACKAGE_STATUSES}")
    logging.info("All other flags/statuses will trigger a RESET (delete 'Ready' package if no Claim ID).")
    logging.info(f"---------------------\n")

    # 1. Scan local directories
    logging.info("Scanning existing claim package directories...")
    existing_packages = {} # map item_id -> folder_path
    for pkg_dir in CLAIM_PACKAGES_DIR.iterdir():
        if pkg_dir.is_dir():
            match = re.search(r'_(\d{5,})_', pkg_dir.name)
            if match:
                item_id = match.group(1)
                existing_packages[item_id] = pkg_dir

    archived_packages_map = {}
    for pkg_dir in CLAIM_PACKAGES_ARCHIVED_DIR.iterdir():
        if pkg_dir.is_dir():
            match = re.search(r'_(\d{5,})_', pkg_dir.name)
            if match:
                item_id = match.group(1)
                archived_packages_map[item_id] = pkg_dir

    logging.info(f"Found {len(existing_packages)} existing 'Ready' packages.")
    logging.info(f"Found {len(archived_packages_map)} existing 'Archived' packages.")

    # 2. Scrape ScanLily
    logging.info("\nStarting ScanLily Inventory Scraper...")
    item_urls = scrape_inventory_item_urls(MAIN_INVENTORY_URL)

    if item_urls:
        logging.info(f"\nFound {len(item_urls)} items. Reconciling with local state...")

        items_created = 0
        items_archived = 0
        items_reset = 0
        items_skipped = 0
        items_failed = 0

        for index, url in enumerate(item_urls):
            logging.info(f"\n===== Reconciling item {index + 1}/{len(item_urls)}: {url} =====")
            item_status = {"scanlily_url": url, "status": "Skipped", "action_taken": "None", "folder_path": None, "errors": []}
            item_data = {}
            item_id = None
            claim_folder_path = None

            try:
                # 3. Extract item_id
                match = re.search(r'/product/(\d+)', url)
                if not match:
                    raise ValueError(f"Could not extract item_id from URL: {url}")
                item_id = match.group(1)
                item_status["item_id"] = item_id

                # 4. Scrape Details for Flag/Status
                item_data = scrape_item_details(url)
                if 'error' in item_data:
                    raise ValueError(f"Scraping failed: {item_data['error']}")

                # 5. Get Flags
                item_flag = item_data.get('flag', '').strip().lower()
                if not item_flag: item_flag = '(not flagged)'
                equip_status = item_data.get('equipment_status', '').strip().lower()
                logging.info(f"    ScanLily Flag: '{item_flag}' | Equipment Status: '{equip_status}'")

                # 6. Check local state
                existing_pkg_path = existing_packages.get(item_id)
                archived_pkg_path = archived_packages_map.get(item_id)

                # === 7. Execute Flag-Based Logic ===

                action_create = item_flag in CREATE_PACKAGE_FLAGS or equip_status in CREATE_PACKAGE_STATUSES
                action_archive = item_flag in ARCHIVE_PACKAGE_FLAGS or equip_status in ARCHIVE_PACKAGE_STATUSES

                # Trigger: Create Package
                if action_create:
                    if existing_pkg_path:
                        logging.info("    Status: 'Create' trigger, but package already 'Ready'. No action.")
                        item_status["status"] = "Skipped (Already Prepared)"
                        item_status["action_taken"] = "None"
                        item_status["folder_path"] = str(existing_pkg_path.resolve())
                        items_skipped += 1
                    elif archived_pkg_path:
                        logging.info("    Status: 'Create' trigger. Package is 'Archived'. Moving back to Ready...")
                        new_path = CLAIM_PACKAGES_DIR / archived_pkg_path.name
                        shutil.move(archived_pkg_path, new_path)
                        item_status["status"] = "Un-archived"
                        item_status["action_taken"] = "Moved from Archive to Ready"
                        item_status["folder_path"] = str(new_path.resolve())
                        items_created += 1
                    else:
                        logging.info("    Status: 'Create' trigger. No package exists. Creating new package...")

                        item_data = assign_policy_and_holder(item_data, POLICY_RULES, ACCOUNT_HOLDERS)
                        assigned_profile_id = item_data.get('assigned_profile_id')
                        if not assigned_profile_id:
                            raise ValueError("Assignment failed: Could not assign profile ID.")

                        item_data['potential_failure_date'] = get_failure_date_str()
                        accidental_damage = item_data.get('accidental_damage_covered', False)
                        if equip_status in CREATE_PACKAGE_STATUSES:
                            item_data['claim_description'] = f"Equipment status reported as: {equip_status}."
                        else:
                            item_data['claim_description'] = "The device fell and is now broken." if accidental_damage else "Device stopped working unexpectedly. Not powering up."
                        logging.info(f"    Claim Description: '{item_data['claim_description']}'")

                        item_title_sanitized = sanitize_filename(item_data.get('device_name_id', f'item_{item_id}'))
                        item_serial = item_data.get('serial_number', 'NoSerial')
                        folder_name = f"{item_title_sanitized}_{sanitize_filename(item_serial)}_{assigned_profile_id}_{item_id}_{date.today().isoformat()}"
                        claim_folder_path = CLAIM_PACKAGES_DIR / folder_name
                        claim_folder_path.mkdir(parents=True, exist_ok=True)
                        item_status["folder_path"] = str(claim_folder_path.resolve())
                        logging.info(f"    Created claim folder: {claim_folder_path.name}")

                        image_download_status, download_errors = download_all_images(item_data, claim_folder_path)
                        item_data['image_download_status'] = image_download_status
                        if download_errors:
                            logging.warning(f"    NOTE: Some image downloads failed. Errors: {len(download_errors)}")
                            item_status["errors"].extend(download_errors)

                        details_filepath = claim_folder_path / "item_details.json"
                        item_data_to_save = item_data.copy()
                        item_data_to_save['retrieved_claim_id'] = None
                        item_data_to_save['actual_filing_date'] = None

                        account_details_to_save = item_data.get('assigned_account_details', {}).copy()
                        if "PLACEHOLDER_" in account_details_to_save.get("Signature_Base64", "PLACEHOLDER_"):
                            account_details_to_save["Signature_Base64"] = "MISSING_OR_PLACEHOLDER"
                        else:
                            account_details_to_save["Signature_Base64"] = "PRESENT_IN_SOURCE"
                        item_data_to_save['assigned_account_details'] = account_details_to_save

                        with open(details_filepath, 'w', encoding='utf-8') as f:
                            json.dump(item_data_to_save, f, indent=2, sort_keys=True)
                        logging.info(f"    Saved item details to: {details_filepath.name}")

                        item_status["status"] = "Prepared"
                        item_status["action_taken"] = "Created New Package"
                        items_created += 1

                # Trigger: Archive Package
                elif action_archive:
                    if existing_pkg_path:
                        logging.info(f"    Status: 'Archive' trigger. Archiving 'Ready' package...")
                        new_path = CLAIM_PACKAGES_ARCHIVED_DIR / existing_pkg_path.name
                        shutil.move(existing_pkg_path, new_path)
                        item_status["status"] = "Archived"
                        item_status["action_taken"] = "Moved from Ready to Archive"
                        item_status["folder_path"] = str(new_path.resolve())
                        items_archived += 1
                    elif archived_pkg_path:
                        logging.info(f"    Status: 'Archive' trigger. Package already 'Archived'. No action.")
                        item_status["status"] = "Skipped (Already Archived)"
                        item_status["action_taken"] = "None"
                        item_status["folder_path"] = str(archived_pkg_path.resolve())
                        items_skipped += 1
                    else:
                        logging.info(f"    Status: 'Archive' trigger. No local package exists. No action.")
                        item_status["status"] = "Skipped (No Package)"
                        item_status["action_taken"] = "None"
                        items_skipped += 1

                # Trigger: All other flags (Reset)
                else:
                    if existing_pkg_path:
                         # --- IMMUTABILITY CHECK ---
                         if check_claim_id_exists(existing_pkg_path):
                             logging.warning(f"    Status: '{item_flag}' (Reset Trigger). 'Ready' package has a CLAIM ID. Archiving instead of deleting.")
                             new_path = CLAIM_PACKAGES_ARCHIVED_DIR / existing_pkg_path.name
                             shutil.move(existing_pkg_path, new_path)
                             item_status["status"] = "Archived (Reset)"
                             item_status["action_taken"] = "Archived 'Ready' Package due to Reset Trigger (Claim ID Present)"
                             item_status["folder_path"] = str(new_path.resolve())
                             items_archived += 1
                         else:
                             logging.info(f"    Status: '{item_flag}' (Reset Trigger). 'Ready' package exists (no Claim ID). Deleting local package...")
                             shutil.rmtree(existing_pkg_path)
                             item_status["status"] = "Reset"
                             item_status["action_taken"] = "Deleted Local 'Ready' Package"
                             items_reset += 1
                    elif archived_pkg_path:
                         logging.info(f"    Status: '{item_flag}'. 'Archived' package exists. No action (won't reset archived).")
                         item_status["status"] = "Skipped (Is Archived)"
                         item_status["action_taken"] = "None"
                         item_status["folder_path"] = str(archived_pkg_path.resolve())
                         items_skipped += 1
                    else:
                         logging.info(f"    Status: '{item_flag}'. No package exists. No action needed.")
                         item_status["status"] = "Skipped (No Action)"
                         item_status["action_taken"] = "None"
                         items_skipped += 1

            except (ValueError, OSError, IOError) as e:
                 err_msg = str(e)
                 item_status["status"] = "Failed"
                 item_status["errors"].append(err_msg)
                 items_failed += 1
                 logging.error(f"---> CRITICAL ERROR processing item: {err_msg}")
            except Exception as e:
                 err_msg = f"Unexpected error: {e}"
                 item_status["status"] = "Failed"
                 item_status["errors"].append(err_msg)
                 items_failed += 1
                 logging.error(f"---> CRITICAL UNEXPECTED ERROR: {e}", exc_info=True)

            # Add status to summary
            overall_summary.append(item_status)

            # Delay before next item
            logging.info(f"---> Waiting {DELAY_BETWEEN_REQUESTS} second(s)...")
            time.sleep(DELAY_BETWEEN_REQUESTS)

        # --- End of loop ---

        logging.info("=" * 30)
        logging.info("\nScanLily Sync complete.")
        logging.info(f"Total Items Scanned: {len(item_urls)}")
        logging.info(f"New/Moved Packages Prepared: {items_created}")
        logging.info(f"Packages Archived: {items_archived}")
        logging.info(f"Packages Reset (Deleted): {items_reset}")
        logging.info(f"Packages Skipped (No Change): {items_skipped}")
        logging.info(f"Items Failed (Error): {items_failed}")
        logging.info("=" * 30)


        # Save the overall summary JSON data
        try:
            with open(SUMMARY_OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
                json.dump(overall_summary, f, indent=2)
            logging.info(f"\nSuccessfully saved overall summary data to '{SUMMARY_OUTPUT_FILENAME}'")
        except IOError as e:
            logging.error(f"\nError saving summary data to file '{SUMMARY_OUTPUT_FILENAME}': {e}")
            logging.info("\nDisplaying final summary data in console instead:\n")
            logging.info(json.dumps(overall_summary, indent=2))

    else:
        logging.warning("No item URLs were found on the main inventory page. Cannot proceed.")

    logging.info("\nScript finished.")
