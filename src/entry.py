from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict
import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
import re
from urllib.parse import urljoin, urlparse

app = FastAPI()

# --- Configuration (Set via Wrangler Secrets) ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
PDFOTTER_API_KEY = os.environ.get("PDFOTTER_API_KEY")
PDFOTTER_TEMPLATE_ID = os.environ.get("PDFOTTER_TEMPLATE_ID")

# --- Constants ---
SCRAPE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'
}

# --- Data Models ---
class ClaimRequest(BaseModel):
    item_id: str
    profile_id: str
    description: str = "Device stopped working unexpectedly."
    filing_date: Optional[str] = None # YYYY-MM-DD

class SyncRequest(BaseModel):
    scanlily_url: str
    limit: Optional[int] = 10 # Safety limit to prevent Worker timeouts

# --- Helpers ---
def supabase_request(method, endpoint, data=None, params=None):
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    # "Prefer: resolution=merge-duplicates" is crucial for Upserts (INSERT ON CONFLICT UPDATE)
    if method == "POST" and endpoint == "inventory_items":
        headers["Prefer"] = "resolution=merge-duplicates"

    try:
        if method == "GET":
            r = requests.get(url, headers=headers, params=params)
        elif method == "POST":
            r = requests.post(url, headers=headers, json=data)
        elif method == "PATCH":
            r = requests.patch(url, headers=headers, json=data, params=params)

        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Supabase Error: {e}")
        return None

def calculate_failure_date(filing_date_str: Optional[str]) -> str:
    try:
        dt = date.fromisoformat(filing_date_str) if filing_date_str else date.today()
        day = 1 if dt.day <= 15 else 15
        return date(dt.year, dt.month, day).strftime("%m/%d/%Y")
    except:
        return date.today().strftime("%m/%d/%Y")

# --- Scraping Logic ---

def scrape_item_details(item_url: str):
    """
    Visits a specific item page and extracts detailed metadata.
    """
    details = {}
    try:
        resp = requests.get(item_url, headers=SCRAPE_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        container = soup.find('div', class_='container')

        if not container:
            return None

        # 1. Basic Info
        title_tag = container.find('h1', class_='text-2xl')
        details['device_name'] = title_tag.text.strip() if title_tag else "Unknown Device"

        cat_tag = container.find('p', class_='text-gray-600', string=lambda t: t and 'Category:' in t)
        details['category'] = cat_tag.text.replace('Category:', '').strip() if cat_tag else "Other"

        # 2. Specs (Serial, Brand, Flag)
        # Mapping ScanLily labels to DB columns
        key_map = {
             "brand": "brand",
             "model number": "model",
             "serial number": "serial_number",
             "flag": "scanlily_flag",
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
                        raw_key = label_span.text.strip().rstrip(':').lower()
                        if raw_key in key_map:
                            details[key_map[raw_key]] = value_span.text.strip()

        return details
    except Exception as e:
        print(f"Error scraping item {item_url}: {e}")
        return None

def sync_inventory_process(main_url: str, limit: int = 10):
    """
    Iterates through the main inventory grid, scrapes details, upserts to DB.
    """
    results = {"scanned": 0, "updated": 0, "errors": []}

    try:
        # 1. Fetch Main Page
        resp = requests.get(main_url, headers=SCRAPE_HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')

        # 2. Parse URL structure
        parsed_url = urlparse(main_url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}/"

        # 3. Find Items
        product_divs = soup.find_all('div', attrs={'data-item-id': True})

        # 4. Loop and Scrape (Respecting limit)
        for i, div in enumerate(product_divs):
            if i >= limit:
                break

            item_id = div.get('data-item-id')
            # Construct item URL (ScanLily structure usually /product/{id})
            # Adjust path finding based on your specific URL structure if needed
            # For now assuming standard path relative to root
            # "inventory/..." -> "inventory/.../product/..."

            # Simple heuristic: Look for the link inside the div
            link_tag = div.find('a', href=True)
            if link_tag:
                full_item_url = urljoin(base_url, link_tag['href'])
            else:
                # Fallback construction
                full_item_url = urljoin(main_url, f"product/{item_id}")

            # 5. Deep Scrape
            details = scrape_item_details(full_item_url)

            if details:
                # 6. Prepare DB Payload
                db_item = {
                    "item_id": item_id,
                    "scanlily_url": full_item_url,
                    "last_scanned_at": datetime.now().isoformat(),
                    # Merged details
                    "device_name": details.get("device_name"),
                    "category": details.get("category"),
                    "brand": details.get("brand"),
                    "model": details.get("model"),
                    "serial_number": details.get("serial_number"),
                    "scanlily_flag": details.get("scanlily_flag", "neutral"),
                    "equipment_status": details.get("equipment_status")
                }

                # 7. Upsert to Supabase
                res = supabase_request("POST", "inventory_items", db_item)
                if res:
                    results["updated"] += 1
                else:
                    results["errors"].append(f"Failed DB save for {item_id}")

            results["scanned"] += 1

    except Exception as e:
        results["errors"].append(str(e))

    return results

# --- Endpoints ---

@app.get("/")
def home():
    return {"status": "ScanLily Auto-Filer Active", "version": "2.0"}

@app.post("/inventory/sync")
def trigger_sync(req: SyncRequest):
    """
    Action: "Sync Inventory".
    Scrapes ScanLily and updates Supabase.
    """
    result = sync_inventory_process(req.scanlily_url, req.limit)
    return result

@app.post("/claims/generate-pdf")
def generate_pdf(req: ClaimRequest):
    """
    Action: "Generate PDF".
    Returns the direct PDFOtter download link.
    """
    # 1. Fetch Item & Account from DB
    items = supabase_request("GET", "inventory_items", params={"item_id": f"eq.{req.item_id}"})
    accounts = supabase_request("GET", "account_holders", params={"profile_id": f"eq.{req.profile_id}"})

    if not items or not accounts:
        raise HTTPException(status_code=404, detail="Item or Account not found in Database. Did you sync?")

    item = items[0]
    account = accounts[0]

    # 2. Dates
    fail_date = calculate_failure_date(req.filing_date)
    # If filing_date provided, use it. Otherwise today.
    sign_date = date.fromisoformat(req.filing_date).strftime("%m/%d/%Y") if req.filing_date else date.today().strftime("%m/%d/%Y")

    # 3. Payload Construction
    payload = {
        "data": {
            "First name": account.get("first_name"),
            "Last name": account.get("last_name"),
            "Address": account.get("address"),
            "City": account.get("city"),
            "State": account.get("state"),
            "ZIP Code": account.get("zip_code"),
            "Phone": account.get("phone"),
            "Email": account.get("email"),
            "Brand": item.get("brand", "N/A"),
            "Model number": item.get("model", "N/A"),
            "Serial number": item.get("serial_number", "N/A"),
            "Claim ID": f"{req.item_id}",
            "Describe what happened": req.description,
            "Date of failure MM/DD/YYYY": fail_date,
            "Date MM/DD/YYYY": sign_date,
            "Signature of enrolled account holder": account.get("signature_base64")
        },
        "output": {
            "file_name": f"Claim_{req.item_id}.pdf"
        }
    }

    # 4. Call PDF Otter
    try:
        otter_resp = requests.post(
            f"https://www.pdfotter.com/api/v1/pdf_templates/{PDFOTTER_TEMPLATE_ID}/fill",
            auth=(PDFOTTER_API_KEY, ""),
            json=payload,
            timeout=30
        )

        if otter_resp.status_code == 200:
            data = otter_resp.json()
            # PDFOtter v1 usually returns 'url' or 'output_url' in the response JSON
            pdf_link = data.get("url") or data.get("output_url")

            if not pdf_link:
                # Fallback if API changes: Just return success, but usually URL is there
                return {"status": "success", "message": "PDF generated, but no link returned.", "debug": data}

            # 5. Log to Claims Table
            supabase_request("POST", "claims", {
                "item_id": req.item_id,
                "profile_id": req.profile_id,
                "status": "generated",
                "description": req.description,
                "pdf_url": pdf_link
            })

            return {
                "status": "success",
                "message": "PDF Generated Successfully",
                "download_url": pdf_link,
                "note": "Link expires based on PDFOtter retention policy."
            }
        else:
            raise HTTPException(status_code=500, detail=f"PDF Otter failed: {otter_resp.text}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
