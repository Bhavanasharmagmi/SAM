# General Mills Asset Downloader

A robust web application for batch downloading product assets from the Mojo platform, supporting multiple retailers (Sobeys, Instacart) with customizable logic and naming conventions.

---

## Table of Contents
- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Execution Flow](#execution-flow)
  - [Frontend](#frontend)
  - [Backend](#backend)
- [Retailer Customization](#retailer-customization)
- [Naming Conventions](#naming-conventions)
- [Setup & Installation](#setup--installation)
- [Usage](#usage)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Overview
This application enables users to batch download product assets (images) from the Mojo platform for different retailers. It supports file uploads (Excel/CSV), single entry mode, progress tracking, logging, and duplicate/restricted asset reporting.

## Features
- Upload Excel/CSV files or enter single BMN/ID for asset download
- Select retailer (Sobeys, Instacart, or Both)
- Custom asset selection and naming logic per retailer
- Real-time progress and logs via Socket.IO
- Duplicate and restricted asset reporting
- Modern, responsive UI

## Architecture
- **Frontend:** HTML/CSS/JS (see `templates/index.html`)
- **Backend:** Flask + Flask-SocketIO (see `App.py`)
- **Automation:** Playwright for browser automation
- **Data:** Pandas for file parsing

---

## Execution Flow

### Frontend
1. **User Interaction:**
   - Select retailer, upload file, or enter BMN/ID manually.
   - Click 'Start' to begin download.
2. **File Upload:**
   - Drag-and-drop or select file. UI shows file name and allows removal.
3. **Form Submission:**
   - On submit, sends data to `/execute` endpoint via AJAX.
4. **Progress & Logs:**
   - Receives real-time updates via Socket.IO:
     - Progress bar, current task, completed/remaining items
     - Log entries (info, warning, error)
     - Lists of failed, duplicate, and restricted BMNs
5. **Stop Execution:**
   - User can stop the process via 'Stop' button, which calls `/stop`.

### Backend
1. **Routes:**
   - `/` : Renders main page
   - `/execute` : Handles execution requests (file upload or single entry)
   - `/stop` : Stops current execution
   - `/status` : Returns current status
2. **File Parsing:**
   - Parses uploaded file using Pandas
   - Validates required columns per retailer
   - Detects duplicates and restricted assets
3. **Retailer Logic:**
   - Uses `RETAILER_CONFIGS` for retailer-specific asset selection and naming
   - Each retailer has:
     - Download folder
     - Asset types and keywords
     - Filename function
     - Asset selection function
     - Search/save ID keys
4. **Automation:**
   - Launches Playwright browser contexts
   - Searches for assets, applies retailer logic, downloads files
   - Updates progress and logs via Socket.IO
5. **Threading:**
   - Runs automation in a background thread to keep Flask responsive

---

## Retailer Customization
Retailers are defined in the `RETAILER_CONFIGS` dictionary in `App.py`:
```python
RETAILER_CONFIGS = {
    "Sobeys": {
        "download_folder": "C:/Downloads/Sobeys",
        "asset_types": { ... },
        "get_filename_func": get_sobeys_filename,
        "select_assets_func": select_assets_sobeys,
        "search_id_key": "bmn",
        "save_id_key": "article_id"
    },
    "Instacart": {
        "download_folder": "C:/Downloads/Instacart",
        "asset_types": { ... },
        "get_filename_func": get_instacart_filename,
        "select_assets_func": select_assets_instacart,
        "search_id_key": "bmn",
        "save_id_key": "gtin"
    }
}
```
- **To add a new retailer:**
  - Define its asset types and keywords
  - Implement its filename and asset selection functions
  - Add its config to `RETAILER_CONFIGS`

## Naming Conventions
- **Sobeys:**
  - Filenames follow the format: `{save_id}_EA_{lang_code}_na_{asset_code}_na.jpg`
  - Asset codes: `left`, `front`, `ing`, `nfp` (see `get_sobeys_filename`)
- **Instacart:**
  - Filenames follow the format: `{save_id}-{suffix}.jpg`
  - Suffixes: `main`, `sideleft`, `sideright`, `ing`, `nut` (see `get_instacart_filename`)

## Setup & Installation
1. **Clone the repository**
2. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```
3. **Install Playwright browsers:**
   ```powershell
   python -m playwright install
   ```
4. **Configure Edge paths in `App.py` if needed**
5. **Run the app:**
   ```powershell
   python App.py
   ```
6. **Access the app:**
   - Open your browser at `http://localhost:5000`

## Usage
- Select retailer and upload your Excel/CSV file (must contain required columns: BMN + Article ID/GTIN)
- Optionally, enter a single BMN/ID
- Click 'Start' to begin batch download
- Monitor progress, logs, and asset status in real time
- Downloaded assets are saved in the configured folders per retailer

## Troubleshooting
- **Missing columns:** Ensure your file contains the required columns for the selected retailer
- **Playwright errors:** Make sure Playwright browsers are installed and Edge paths are correct
- **Permission issues:** Run as administrator if saving to protected folders
- **SocketIO issues:** Check firewall and network settings

