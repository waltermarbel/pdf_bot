from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
from typing import Optional
import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
from urllib.parse import urljoin, urlparse
import boto3
from botocore.config import Config

app = FastAPI()

# --- Configuration (Set via Wrangler Secrets) ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
PDFOTTER_API_KEY = os.environ.get("PDFOTTER_API_KEY")
PDFOTTER_TEMPLATE_ID = os.environ.get("PDFOTTER_TEMPLATE_ID")
GPT_AUTH_TOKEN = os.environ.get("GPT_AUTH_TOKEN")

# R2 Configuration
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME")
# Optional: Custom domain for R2 (e.g., https://files.mydomain.com)
R2_PUBLIC_URL_BASE = os.environ.get("R2_PUBLIC_URL_BASE")

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
    limit: Optional[int] = 10

# --- Security ---
async def verify_token(x_auth_token: str = Header(...)):
    """
    Verifies the API Key sent by the GPT/Client.
    """
    if x_auth_token != GPT_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid Auth Token")

# --- Helpers ---
def supabase_request(method, endpoint, data=None, params=None):
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
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

def upload_to_r2(file_content: bytes, file_name: str, content_type: str = "application/pdf") -> str:
    """
    Uploads bytes to Cloudflare R2 and returns the public URL.
    """
    if not all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME]):
        print("R2 credentials missing. Skipping upload.")
        return None

    try:
        s3 = boto3.client(
            's3',
            endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            config=Config(signature_version='s3v4')
        )

        s3.put_object(
            Bucket=R2_BUCKET_NAME,
            Key=file_name,
            Body=file_content,
            ContentType=content_type
        )

        # Construct URL
        if R2_PUBLIC_URL_BASE:
            return f"{R2_PUBLIC_URL_BASE}/{file_name}"
        else:
            # Fallback: Presigned URL or standard R2 dev URL logic (often cleaner to use custom domain)
            # For now returning the key, user assumes public bucket access pattern
            return f"https://{R2_BUCKET_NAME}.r2.dev/{file_name}" # Example standard dev domain

    except Exception as e:
        print(f"R2 Upload Error: {e}")
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
    details = {}
    try:
        resp = requests.get(item_url, headers=SCRAPE_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        container = soup.find('div', class_='container')

        if not container: return None

        title_tag = container.find('h1', class_='text-2xl')
        details['device_name'] = title_tag.text.strip() if title_tag else "Unknown Device"

        cat_tag = container.find('p', class_='text-gray-600', string=lambda t: t and 'Category:' in t)
        details['category'] = cat_tag.text.replace('Category:', '').strip() if cat_tag else "Other"

        key_map = {
             "brand": "brand", "model number": "model",
             "serial number": "serial_number", "flag": "scanlily_flag",
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
    results = {"scanned": 0, "updated": 0, "errors": []}
    try:
        resp = requests.get(main_url, headers=SCRAPE_HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        parsed_url = urlparse(main_url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}/"
        product_divs = soup.find_all('div', attrs={'data-item-id': True})

        for i, div in enumerate(product_divs):
            if i >= limit: break

            item_id = div.get('data-item-id')
            link_tag = div.find('a', href=True)
            full_item_url = urljoin(base_url, link_tag['href']) if link_tag else urljoin(main_url, f"product/{item_id}")

            details = scrape_item_details(full_item_url)
            if details:
                db_item = {
                    "item_id": item_id,
                    "scanlily_url": full_item_url,
                    "last_scanned_at": datetime.now().isoformat(),
                    "device_name": details.get("device_name"),
                    "category": details.get("category"),
                    "brand": details.get("brand"),
                    "model": details.get("model"),
                    "serial_number": details.get("serial_number"),
                    "scanlily_flag": details.get("scanlily_flag", "neutral"),
                    "equipment_status": details.get("equipment_status")
                }
                if supabase_request("POST", "inventory_items", db_item):
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
    return {"status": "ScanLily Auto-Filer Active", "version": "3.0 (Secured + R2)"}

@app.post("/inventory/sync", dependencies=[Depends(verify_token)])
def trigger_sync(req: SyncRequest):
    return sync_inventory_process(req.scanlily_url, req.limit)

@app.post("/claims/generate-pdf", dependencies=[Depends(verify_token)])
def generate_pdf(req: ClaimRequest):
    # 1. Fetch Data
    items = supabase_request("GET", "inventory_items", params={"item_id": f"eq.{req.item_id}"})
    accounts = supabase_request("GET", "account_holders", params={"profile_id": f"eq.{req.profile_id}"})

    if not items or not accounts:
        raise HTTPException(status_code=404, detail="Item or Account not found.")
    item, account = items[0], accounts[0]

    # 2. Dates
    fail_date = calculate_failure_date(req.filing_date)
    sign_date = date.fromisoformat(req.filing_date).strftime("%m/%d/%Y") if req.filing_date else date.today().strftime("%m/%d/%Y")

    # 3. PDF Payload
    payload = {
        "data": {
            "First name": account.get("first_name"), "Last name": account.get("last_name"),
            "Address": account.get("address"), "City": account.get("city"), "State": account.get("state"),
            "ZIP Code": account.get("zip_code"), "Phone": account.get("phone"), "Email": account.get("email"),
            "Brand": item.get("brand", "N/A"), "Model number": item.get("model", "N/A"),
            "Serial number": item.get("serial_number", "N/A"), "Claim ID": f"{req.item_id}",
            "Describe what happened": req.description, "Date of failure MM/DD/YYYY": fail_date,
            "Date MM/DD/YYYY": sign_date, "Signature of enrolled account holder": account.get("signature_base64")
        },
        "output": { "file_name": f"Claim_{req.item_id}.pdf" }
    }

    # 4. Generate PDF
    try:
        otter_resp = requests.post(
            f"https://www.pdfotter.com/api/v1/pdf_templates/{PDFOTTER_TEMPLATE_ID}/fill",
            auth=(PDFOTTER_API_KEY, ""), json=payload, timeout=30
        )

        if otter_resp.status_code == 200:
            data = otter_resp.json()
            pdf_link = data.get("url") or data.get("output_url")

            final_pdf_url = pdf_link # Default to PDFOtter link

            # 5. R2 Archiving (Enhanced Feature)
            if pdf_link and R2_BUCKET_NAME:
                print("Archiving to R2...")
                try:
                    pdf_bytes = requests.get(pdf_link).content
                    r2_filename = f"claims/Claim_{req.item_id}_{datetime.now().strftime('%Y%m%d')}.pdf"
                    r2_url = upload_to_r2(pdf_bytes, r2_filename)
                    if r2_url:
                        final_pdf_url = r2_url
                        print(f"Archived successfully: {final_pdf_url}")
                except Exception as archive_err:
                    print(f"R2 Archival Failed: {archive_err}")
                    # Fallback to PDFOtter link if archival fails

            # 6. Log to DB
            supabase_request("POST", "claims", {
                "item_id": req.item_id,
                "profile_id": req.profile_id,
                "status": "generated",
                "description": req.description,
                "pdf_url": final_pdf_url
            })

            return {
                "status": "success",
                "message": "PDF Generated",
                "download_url": final_pdf_url
            }
        else:
            raise HTTPException(status_code=500, detail=f"PDF Otter failed: {otter_resp.text}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
