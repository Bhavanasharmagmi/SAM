# SAM Amazon Non-API Project

## Overview

This project automates the process of downloading product assets (images, nutrition info, etc.) for Amazon listings using browser automation and catalog lookups, without relying on Amazon's API. It is built with Python, Flask, Flask-SocketIO, Playwright, and Pandas, and is designed for General Mills product catalogs.

## Features
- **ASIN Lookup**: Finds all active ASINs for a given GTIN by searching DayOne Digital catalogs.
- **Asset Download**: Downloads carousel images, nutrition images, and other assets for each ASIN directly from the General Mills Mojo portal.
- **Retailer Filtering**: Ensures only Amazon-related assets are downloaded, with customizable retailer logic.
- **Folder Organization**: Assets are saved in folders named after each ASIN, following a strict naming convention.
- **Web UI**: Flask web interface for uploading files and monitoring progress in real time via Socket.IO.
- **Catalog Processing**: Handles both Amazon Core and Fresh catalogs, normalizing UPCs and mapping GTINs to ASINs.

## Getting Started

### Prerequisites
- Python 3.8+
- Microsoft Edge installed (update `EDGE_EXECUTABLE_PATH` if needed)
- Node.js (for Playwright installation)
- Chrome/Edge WebDriver (if using browser automation)

### Installation
1. **Clone the repository**:
   ```bash
   git clone https://github.com/Bhavanasharmagmi/SAM.git
   cd SAM-Amazon-non-API
   ```
2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Install Playwright browsers**:
   ```bash
   playwright install
   ```
4. **Set up environment variables**:
   - Create a `.env` file with:
     ```env
     DAYONE_USER=your_dayone_username
     DAYONE_PASS=your_dayone_password
     ```

### Running the Application
1. **Start the Flask server**:
   ```bash
   python App.py
   ```
2. **Access the web interface**:
   - Open your browser and go to `http://localhost:5000`
   - Upload your GTIN/ASIN file and monitor progress live.

## Project Structure
- `App.py`: Main application logic and automation functions.
- `requirements.txt`: Python dependencies.
- `static/`: Static assets (images, CSS, JS).
- `templates/`: HTML templates for Flask.
- `uploads/`: Uploaded files.
- `CATALOGS_TO_CHECK`: List of catalogs to search for ASINs.

## Key Functions & Their Usage

### ASIN Lookup
- `login_dayone(page: Page)`: Logs into DayOne Digital using credentials from `.env`.
- `search_and_get_asins(page: Page, gtin: str)`: Searches for a GTIN in all catalogs and returns active ASINs, skipping 'graveyard' status.
- `lookup_asins_for_items(items_data)`: Processes a batch of items, looking up ASINs for each GTIN and emitting progress via Socket.IO.

### Asset Download
- `download_carousel_images(page, gtin, asins)`: Downloads all carousel images for a GTIN, saving them to each ASIN folder with the naming convention `{asin}.PT{sequence_num:02d}.jpeg`.
- `download_standard_asset(page, gtin, asset_type, search_code, asins, ...)`: Downloads hero or nutrition images, saving as `{asin}.Main.jpeg` or `{asin}.PT{nutrition_sequence:02d}.jpeg`.
- `save_to_all_asin_folders(download, asins, filename_pattern, sequence_num)`: Helper to save a downloaded file to all ASIN folders.

### Catalog Processing
- `_process_downloaded_files(core_path, fresh_path, logger)`: Reads and normalizes catalog files, mapping GTINs to ASINs.
- `download_and_process_catalogs_async(logger)`: Downloads and processes catalog files asynchronously.

### Helper Functions
- `format_product_id_for_display(search_id)`: Formats GTIN for display and search.
- `create_asin_folders(asins)`: Creates folders for each ASIN under the download directory.
- `parse_file(file_path)`: Parses uploaded CSV/Excel files for GTINs.

## Retailer Filtering & Customization
- Retailer logic is handled in asset download functions. Only assets with retailer 'Amazon' (case-insensitive) are downloaded. To change the retailer, update the string match in the relevant function (e.g., replace `'amazon'` with your desired retailer name).

## Naming Convention
- **Carousel Images**: `{asin}.PT{sequence_num:02d}.jpeg`
- **Hero Images**: `{asin}.Main.jpeg`
- **Nutrition Images**: `{asin}.PT{nutrition_sequence:02d}.jpeg`
- All assets are saved in folders named after their ASIN under `DOWNLOAD_FOLDER`.

## How to Add/Change Retailer
1. Locate the retailer check in `download_carousel_images` and `download_standard_asset`:
   ```python
   if "amazon" not in retailer_name.lower():
       # skip
   ```
2. Change "amazon" to your desired retailer (e.g., "walmart").

## How to Change Naming Convention
- Edit the filename logic in `save_to_all_asin_folders`:
   ```python
   filename = f"{asin}.PT{sequence_num:02d}.jpeg"  # Carousel
   filename = f"{asin}.Main.jpeg"  # Hero
   filename = f"{asin}.PT{nutrition_sequence:02d}.jpeg"  # Nutrition
   ```
- Adjust as needed for your requirements.

## Troubleshooting
- **Playwright errors**: Ensure browsers are installed (`playwright install`).
- **Login issues**: Check `.env` credentials and DayOne access.
- **File parsing errors**: Ensure your input file has a `gtin` column.
- **Permission errors**: Run as administrator if saving to protected folders.

