# ScanLily Serverless Automation - Deployment Guide

This guide explains how to deploy the serverless Claim Auto-Filer system using **Cloudflare Workers (Python)** and **Supabase**.

## Prerequisites

1.  **Cloudflare Account**: [Sign up here](https://dash.cloudflare.com/sign-up).
2.  **Supabase Account**: [Sign up here](https://supabase.com/).
3.  **PDFOtter Account**: Get your API Key and Template ID.
4.  **Node.js & npm**: Required to install the Cloudflare CLI (`wrangler`).

---

## Step 1: Database Setup (Supabase)

1.  Log in to your [Supabase Dashboard](https://supabase.com/dashboard) and create a new project.
2.  Go to the **SQL Editor** (left sidebar).
3.  Click **New Query**.
4.  Open the file `supabase_schema.sql` from this repository.
5.  Copy and paste the entire content into the SQL Editor.
6.  Click **Run**.
    *   *Result:* This creates the `account_holders`, `inventory_items`, and `claims` tables and populates the initial user accounts.

---

## Step 2: Storage Setup (Cloudflare R2)

This system archives generated PDFs to Cloudflare R2.

1.  Log in to the Cloudflare Dashboard.
2.  Go to **R2** > **Create Bucket**.
3.  Name it (e.g., `scanlily-claims`).
4.  Go to **Manage R2 API Tokens** > **Create API Token**.
    *   **Permissions**: Object Read & Write.
    *   **TTL**: Forever.
5.  **Save these credentials immediately**:
    *   Access Key ID
    *   Secret Access Key
    *   Endpoint (Account ID)

---

## Step 3: Project Configuration

1.  Open your terminal/command prompt.
2.  Install Wrangler (Cloudflare CLI):
    ```bash
    npm install -g wrangler
    ```
3.  Login to Cloudflare:
    ```bash
    wrangler login
    ```
4.  Navigate to this project folder.

---

## Step 4: Set Secrets

The application relies on secrets for security. Run the following commands one by one, pasting the values when prompted:

```bash
# 1. Supabase Connection
npx wrangler secret put SUPABASE_URL
# (e.g., https://your-project.supabase.co)

npx wrangler secret put SUPABASE_KEY
# (Use the 'service_role' key from Supabase Settings > API)

# 2. PDFOtter Credentials
npx wrangler secret put PDFOTTER_API_KEY
npx wrangler secret put PDFOTTER_TEMPLATE_ID

# 3. Security Token (You create this)
npx wrangler secret put GPT_AUTH_TOKEN
# (e.g., generate a random string: 'sl-secret-12345')

# 4. R2 Storage Credentials
npx wrangler secret put R2_ACCOUNT_ID
# (From Step 2)

npx wrangler secret put R2_ACCESS_KEY_ID
npx wrangler secret put R2_SECRET_ACCESS_KEY
npx wrangler secret put R2_BUCKET_NAME
# (e.g., 'scanlily-claims')
```

---

## Step 5: Deploy

Deploy the worker to the Cloudflare edge network:

```bash
npx wrangler deploy
```

**Success!** You will see a URL like: `https://scanlily-worker.yourname.workers.dev`

---

## Step 6: Connect Custom GPT

1.  Go to **ChatGPT** > **Explore** > **Create a GPT**.
2.  In **Configure**, scroll to **Actions** and click **Create new action**.
3.  **Authentication**:
    *   Type: API Key
    *   API Key: (Paste the `GPT_AUTH_TOKEN` you created in Step 4)
    *   Auth Type: Bearer / Custom (Set header name to `X-Auth-Token`)
4.  **Schema**:
    *   Copy the URL of your deployed worker.
    *   Paste the following JSON schema (replace `YOUR_WORKER_URL` with your actual URL):

```json
{
  "openapi": "3.1.0",
  "info": {
    "title": "ScanLily Auto-Filer",
    "description": "API for syncing inventory and generating insurance claims.",
    "version": "1.0.0"
  },
  "servers": [
    {
      "url": "https://scanlily-worker.yourname.workers.dev"
    }
  ],
  "paths": {
    "/inventory/sync": {
      "post": {
        "description": "Scrapes the ScanLily inventory page and updates the database.",
        "operationId": "syncInventory",
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {
                  "scanlily_url": { "type": "string" },
                  "limit": { "type": "integer" }
                }
              }
            }
          }
        },
        "responses": { "200": { "description": "Sync successful" } }
      }
    },
    "/claims/generate-pdf": {
      "post": {
        "description": "Generates a signed PDF claim form.",
        "operationId": "generateClaimPDF",
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {
                  "item_id": { "type": "string" },
                  "profile_id": { "type": "string" },
                  "provider_claim_id": { "type": "string", "description": "The Claim ID provided by the Insurance Portal" },
                  "description": { "type": "string" },
                  "filing_date": { "type": "string", "description": "YYYY-MM-DD" }
                },
                "required": ["item_id", "profile_id", "provider_claim_id"]
              }
            }
          }
        },
        "responses": { "200": { "description": "PDF generated successfully" } }
      }
    }
  }
}
```

---

## Step 7: Automation (Optional)

To sync inventory automatically every hour:

1.  Open `wrangler.toml`.
2.  Add this at the bottom:
    ```toml
    [triggers]
    crons = ["0 * * * *"]
    ```
3.  Deploy again: `npx wrangler deploy`.
