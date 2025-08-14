import asyncio
import os
import re
import shutil
import threading
from collections import defaultdict
import pandas as pd
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright, Page, expect, TimeoutError
from werkzeug.utils import secure_filename
from datetime import datetime
import getpass
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
load_dotenv()

# --- Flask and Socket.IO Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-amazon-project-secret'
app.config['UPLOAD_FOLDER'] = 'uploads'
socketio = SocketIO(app, cors_allowed_origins="*")
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- CONFIGURATION ---
EDGE_EXECUTABLE_PATH = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
EDGE_USER_DATA_DIR = "C:/Temp/EdgeUserData"
DOWNLOAD_FOLDER = "C:/Downloads/Amazon"
HI_RES_JPEG_SELECTOR = 'div.MuiListItemText-root span.MuiTypography-root:has-text("High Resolution/jpeg")'
GRID_ITEM_SELECTOR = "div.MuiCard-root.selectable-content-hub-item"
MAIN_SEARCH_SELECTOR = "#search-search-box-text-tokenfield"
DOWNLOAD_BUTTON_SELECTOR = 'button[data-testid="operation-dropdown-button"][aria-label="Download"]'
BASE_URL = 'https://mojo.generalmills.com/en-us'

# DayOne Configuration
DAYONE_USERNAME = os.getenv("DAYONE_USER")
DAYONE_PASSWORD = os.getenv("DAYONE_PASS")
CATALOGS_TO_CHECK = [
    "Amazon Core Catalog",
    "Amazon Fresh Catalog"
]

# --- Global State Management ---
execution_status = {
    'running': False, 'logs': [], 'progress': 0, 'current_task': '',
    'total_items': 0, 'completed_items': 0, 'current_item_id': '', 'failed_items': [],
    'asin_lookup_progress': 0
}

class WebSocketLogger:
    def log(self, message, level='info'):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = {'timestamp': timestamp, 'message': message, 'level': level}
        execution_status['logs'].append(log_entry)
        socketio.emit('log', log_entry)
        print(f"[{timestamp}] {message}")

logger = WebSocketLogger()

# --- ASIN Lookup Functions (Unchanged) ---
def login_dayone(page: Page):
    """Navigates to the login page and logs in to DayOne."""
    logger.log("Logging into DayOne Digital...")
    page.goto("https://dayonedigital.com/portal/login")

    global DAYONE_USERNAME, DAYONE_PASSWORD
    if not DAYONE_USERNAME:
        DAYONE_USERNAME = input("Enter DayOne Username: ")
    if not DAYONE_PASSWORD:
        DAYONE_PASSWORD = getpass.getpass("Enter DayOne Password: ")

    logger.log(f"Authenticating user: {DAYONE_USERNAME}")
    page.get_by_role("textbox", name="Username*:").fill(DAYONE_USERNAME)
    page.get_by_role("textbox", name="Password*:").fill(DAYONE_PASSWORD)
    page.get_by_role("button", name="Submit").click()

    try:
        dashboard_search_box = page.get_by_role("textbox", name="Search")
        expect(dashboard_search_box).to_be_visible(timeout=60000)
        logger.log("DayOne login successful.")
    except TimeoutError:
        login_error = page.locator(".alert-danger, .error-message, #login-error-text")
        if login_error.is_visible():
            error_text = login_error.inner_text()
            raise Exception(f"DayOne login failed: {error_text.strip()}")
        else:
            raise Exception("DayOne login failed - timeout waiting for dashboard.")



def search_and_get_asins(page: Page, gtin: str):
    """
    Searches for a GTIN across specified catalogs and returns all active ASINs found.
    This version uses a robust waiting mechanism by checking for a change in the
    table's result-count information text.
    """
    search_term = re.sub(r'[^0-9a-zA-Z]', '', gtin)
    logger.log(f"Searching DayOne for GTIN: {search_term}")
    all_asins = []

    for catalog in CATALOGS_TO_CHECK:
        logger.log(f"Checking catalog: {catalog}")
        try:
            # 1. Navigate to the correct catalog
            page.get_by_role("link", name=catalog, exact=True).click()
            page.wait_for_load_state("networkidle", timeout=20000)

            # --- ROBUST WAITING MECHANISM ---
            info_div = page.locator("#catalog-table-export_info")
            initial_info_text = info_div.inner_text()

            # Perform the search action
            search_box = page.get_by_role("textbox", name="Search")
            search_box.clear()
            search_box.fill(search_term)
            search_box.press("Enter")

            # Explicitly wait for the result text to change
            try:
                page.wait_for_function(
                    f"""
                    () => {{
                        const el = document.querySelector('#catalog-table-export_info');
                        return el && el.innerText !== `{initial_info_text}`;
                    }}
                    """,
                    timeout=20000
                )
                logger.log("  - Table refresh detected.")
            except TimeoutError:
                logger.log("  - Table refresh not detected via text change. Using network idle wait as fallback.", "debug")
                page.wait_for_load_state("networkidle", timeout=20000)

            # Scrape the results
            rows = page.locator("#load-catalog tr")
            row_count = rows.count()
            if row_count == 0 or "No matching records found" in rows.first.inner_text():
                logger.log(f"  No items found in {catalog}")
                continue

            catalog_asins = []
            for i in range(row_count):
                row = rows.nth(i)
                status_cell = row.locator("td.status")
                asin_cell = row.locator("td.asin")

                if status_cell.count() > 0 and asin_cell.count() > 0:
                    status_text = status_cell.inner_text().strip()
                    if status_text.lower() != "graveyard":
                        asin_text = asin_cell.inner_text().strip()
                        if asin_text and asin_text not in catalog_asins:
                            catalog_asins.append(asin_text)

            if catalog_asins:
                logger.log(f"  Found ASINs in {catalog}: {', '.join(catalog_asins)}")
                all_asins.extend(catalog_asins)
            else:
                logger.log(f"  No active ASINs found in {catalog}")

        except Exception as e:
            logger.log(f"Error processing catalog {catalog}: {e}", 'error')

    # Create a final list of unique ASINs
    unique_asins = list(dict.fromkeys(all_asins))
    logger.log(f"Total unique ASINs found for GTIN {gtin}: {len(unique_asins)}")
    return unique_asins


def lookup_asins_for_items(items_data):
    """Looks up ASINs for all GTINs in the dataset."""
    logger.log("🔍 Starting ASIN lookup process...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=50)
        page = browser.new_page()
        try:
            login_dayone(page)
            expanded_items = []
            total_items = len(items_data)
            for i, item in enumerate(items_data):
                if not execution_status['running']:
                    break
                gtin = item.get('gtin')
                bmn = item.get('bmn')  # BMN is kept for data integrity, but not used in new search
                progress = int((i / total_items) * 100)
                execution_status['asin_lookup_progress'] = progress
                socketio.emit('asin_lookup_progress', {'progress': progress, 'current_gtin': gtin, 'completed': i, 'total': total_items})
                if not gtin:
                    logger.log(f"Skipping item {i+1} - no GTIN provided", 'warning')
                    continue
                try:
                    asins = search_and_get_asins(page, gtin)
                    if not asins:
                        logger.log(f"No ASINs found for GTIN {gtin}", 'warning')
                        expanded_items.append({'gtin': gtin, 'asin': '', 'bmn': bmn, 'lookup_status': 'no_asin_found'})
                    else:
                        for asin in asins:
                            expanded_items.append({'gtin': gtin, 'asin': asin, 'bmn': bmn, 'lookup_status': 'success'})
                except Exception as e:
                    logger.log(f"Error looking up ASINs for GTIN {gtin}: {e}", 'error')
                    expanded_items.append({'gtin': gtin, 'asin': '', 'bmn': bmn, 'lookup_status': 'error'})
            socketio.emit('asin_lookup_complete', {'total_processed': len(items_data), 'total_expanded': len(expanded_items)})
        except Exception as e:
            logger.log(f"Critical error during ASIN lookup: {e}", 'error')
            return []
        finally:
            browser.close()
    logger.log(f"✅ ASIN lookup complete. Expanded {len(items_data)} initial items to {len(expanded_items)} ASIN-specific items.")
    return expanded_items

# --- Helper Functions ---
def format_product_id_for_display(search_id: str) -> str:
    if len(search_id) == 14 and search_id.startswith('000'):
        return f"{search_id[3:8]}-{search_id[8:13]}"
    return search_id

def create_asin_folders(asins):
    """Creates folders for each ASIN and returns the folder paths."""
    asin_folders = {}
    for asin in asins:
        folder_path = os.path.join(DOWNLOAD_FOLDER, asin)
        os.makedirs(folder_path, exist_ok=True)
        asin_folders[asin] = folder_path
    return asin_folders

async def save_to_all_asin_folders(download, asins, filename_pattern, sequence_num=None):
    """Saves a downloaded file to all ASIN folders with proper naming."""
    asin_folders = create_asin_folders(asins)
    saved_files = []
    
    for asin in asins:
        if sequence_num is not None:
            # For carousel images: {asin}.pt{sequence_num:02d}.jpeg
            filename = f"{asin}.PT{sequence_num:02d}.jpeg"
        else:
            # For hero images: {asin}.Main.jpeg
            filename = f"{asin}.{filename_pattern}.jpeg"
        
        file_path = os.path.join(asin_folders[asin], filename)
        await download.save_as(file_path)
        saved_files.append(file_path)
    
    return saved_files

# --- Core Automation Logic (Modified) ---
async def download_carousel_images(page, gtin: str, asins: list):
    logger.log(f"-- Part 1: Processing Carousel Images for GTIN: {gtin} --")
    downloads_succeeded = 0
    max_sequence_num = 0
    downloaded_priorities = set()
    
    try:
        if not execution_status['running']: return 0, 0
        
        logger.log("Navigating to Advanced Search...")
        await page.goto(BASE_URL, wait_until="domcontentloaded")
        await page.get_by_role('link', name='Search', exact=True).click()
        await page.get_by_test_id('advanced-tab').click()
        await page.get_by_test_id('filter-select').wait_for(state='visible', timeout=15000)
        
        logger.log(f"Applying filter: Product ID '{gtin}'")
        await page.get_by_test_id('filter-select').click()
        await page.get_by_role('option', name='Product', exact=True).click()
        await page.get_by_test_id('relation-chip-input-element').fill(gtin)
        formatted_id = format_product_id_for_display(gtin)
        await page.get_by_text(re.compile(f"^{re.escape(f'{formatted_id} | ConsumerPack |')}")).click()
        await page.get_by_test_id('query-builder-component-add-filter').click()
        await page.wait_for_load_state("networkidle", timeout=30000)

        logger.log("Applying filter: Carousel Priority")
        await page.get_by_test_id('filter-select').click()
        await page.get_by_role('option', name='Carousel Priority', exact=True).click()
        await page.get_by_role('combobox', name='None').click()
        await page.get_by_test_id('select-all').click()
        await page.keyboard.press("Escape")
        await page.get_by_test_id('query-builder-component-add-filter').click()
        await page.get_by_test_id('query-builder-component-add-filter').click()
        logger.log("Second filter 'Add' button clicked successfully.")
        
        logger.log("Waiting for final results to load...")
        await page.wait_for_load_state("networkidle", timeout=30000)
        await page.locator(GRID_ITEM_SELECTOR).first.wait_for(state="visible", timeout=15000)
        logger.log("Final results grid is ready.")

        num_items = await page.locator(GRID_ITEM_SELECTOR).count()
        if num_items == 0:
            logger.log(f"No carousel items found for GTIN {gtin} after filtering.", 'warning')
            return 0, 0
        
        logger.log(f"Found {num_items} potential carousel items. Checking each for retailer and priority.")

        for i in range(num_items):
            if not execution_status['running']: break
            
            sequence_num = None
            should_download = True
            try:
                item_for_priority = page.locator(GRID_ITEM_SELECTOR).nth(i)
                await item_for_priority.click()
                details_button = page.get_by_test_id('details')
                await details_button.wait_for(state="visible", timeout=10000)
                await details_button.click()

                priority_value_container = page.locator("div[data-testid*='CarouselPriority']")
                priority_span = priority_value_container.locator("span.MuiChip-label")
                await priority_span.wait_for(state="visible", timeout=10000)
                sequence_str = await priority_span.inner_text()
                sequence_num = int(sequence_str)
                logger.log(f"  - Item {i+1}: Found Carousel Priority: {sequence_num}")

                retailer_value_container = page.locator("div[data-testid*='RetailerToAsset']")
                if await retailer_value_container.is_visible(timeout=1000):
                    retailer_name = await retailer_value_container.inner_text()
                    if "amazon" not in retailer_name.lower():
                        logger.log(f"    - ❌ Skipping: Retailer is '{retailer_name.strip()}', not Amazon.", "warning")
                        should_download = False
                    else:
                         logger.log(f"    - ✅ OK: Retailer is '{retailer_name.strip()}'.")
                else:
                    logger.log("    - ✅ OK: No specific retailer found, proceeding.")
                
                if sequence_num in downloaded_priorities:
                    logger.log(f"    - ❌ Skipping: Priority {sequence_num} already downloaded for this GTIN.", "warning")
                    should_download = False

                await page.get_by_test_id('back-button').click()
                await page.wait_for_load_state("networkidle", timeout=20000)

            except Exception as e:
                logger.log(f"    - ❌ Could not get metadata for item {i+1}. Error: {e}", 'error')
                if await page.get_by_test_id('back-button').is_visible(timeout=1000):
                    await page.get_by_test_id('back-button').click()
                    await page.wait_for_load_state("networkidle")
                continue
            
            if sequence_num is None or not should_download: continue

            try:
                max_sequence_num = max(max_sequence_num, sequence_num)
                
                item_for_download = page.locator(GRID_ITEM_SELECTOR).nth(i)
                await item_for_download.click()
                await page.locator(DOWNLOAD_BUTTON_SELECTOR).click()
                await page.locator(HI_RES_JPEG_SELECTOR).wait_for(state="visible", timeout=5000)
                
                async with page.expect_download(timeout=90000) as download_info:
                    await page.locator(HI_RES_JPEG_SELECTOR).click()
                download = await download_info.value
                
                # Save directly to all ASIN folders
                saved_files = await save_to_all_asin_folders(download, asins, None, sequence_num)
                logger.log(f"    - ✅ Downloaded carousel pt{sequence_num:02d} to {len(asins)} ASIN folders", 'info')
                
                downloads_succeeded += 1
                downloaded_priorities.add(sequence_num)
                await page.keyboard.press("Escape")

            except Exception as e:
                logger.log(f"    - ❌ Download failed for priority {sequence_num}. Error: {e}", 'error')
                if await page.locator('div[role="dialog"]').is_visible(timeout=1000):
                    await page.keyboard.press("Escape")
    except Exception as e:
        logger.log(f"❌ Critical error during carousel image processing for GTIN {gtin}: {e}", 'error')
    
    return downloads_succeeded, max_sequence_num


async def download_standard_asset(page: Page, gtin: str, asset_type: str, search_code: str, asins: list, is_nutrition: bool = False, nutrition_sequence: int = None):
    logger.log(f"-- Part 2: Processing '{asset_type}' asset for GTIN: {gtin} --")
    try:
        if not execution_status['running']: return False
        
        await page.goto(BASE_URL, wait_until="domcontentloaded")
        search_query = f"{gtin} {search_code}"
        logger.log(f"Searching for: '{search_query}'")
        await page.locator(MAIN_SEARCH_SELECTOR).fill(search_query)
        await page.keyboard.press("Enter")
        await page.wait_for_load_state('networkidle', timeout=30000)

        grid_items = page.locator(GRID_ITEM_SELECTOR)
        num_items = await grid_items.count()

        if num_items == 0:
            logger.log(f"  - ❌ No results found for query '{search_query}'. Skipping.", 'warning')
            return False

        logger.log(f"  - {num_items} results found. Checking each for suitability...")

        for i in range(num_items):
            if not execution_status['running']: return False
            
            logger.log(f"  - Checking result {i + 1}/{num_items}...")

            try:
                item = grid_items.nth(i)
                await item.wait_for(state='visible', timeout=20000)
                
                await item.click()
                details_button = page.get_by_test_id('details')
                await details_button.wait_for(state="visible", timeout=10000)
                await details_button.click()

                # --- FINAL FIX: Use the unique data-testid for the specific panel ---
                # This selector uniquely identifies the "Required Fields" accordion panel.
                details_panel_selector = 'div[data-testid="Gmi.MandatoryMetadata"]'
                await page.locator(details_panel_selector).wait_for(state="visible", timeout=10000)

                # --- Now that the correct panel is loaded, check for the retailer ---
                should_skip_item = False
                retailer_container = page.locator("div[data-testid*='RetailerToAsset']")

                if await retailer_container.count() > 0:
                    retailer_name = await retailer_container.inner_text()
                    if retailer_name.strip() and "amazon" not in retailer_name.lower():
                        logger.log(f"    - ❌ Skipping item {i+1}: Retailer is '{retailer_name.strip()}', not Amazon.", "warning")
                        should_skip_item = True
                    else:
                        logger.log(f"    - ✅ OK: Retailer for item {i+1} is '{retailer_name.strip()}' or is Amazon-related.")
                else:
                    logger.log(f"    - ✅ OK: No specific retailer field present for item {i+1}, proceeding.")
                
                if should_skip_item:
                    await page.get_by_test_id('back-button').click()
                    await page.wait_for_load_state("networkidle", timeout=20000)
                    continue

                # --- If checks pass, proceed with download ---
                logger.log(f"  - Found suitable asset at result {i + 1}. Starting download...")
                await page.locator(DOWNLOAD_BUTTON_SELECTOR).click()
                async with page.expect_download(timeout=90000) as download_info:
                    await page.locator(HI_RES_JPEG_SELECTOR).click()
                download = await download_info.value
                
                if is_nutrition and nutrition_sequence is not None:
                    await save_to_all_asin_folders(download, asins, None, nutrition_sequence)
                    logger.log(f"  - ✅ Downloaded nutrition pt{nutrition_sequence:02d} to {len(asins)} ASIN folders", 'info')
                else:
                    await save_to_all_asin_folders(download, asins, "Main")
                    logger.log(f"  - ✅ Downloaded {asset_type} to {len(asins)} ASIN folders", 'info')
                
                await page.get_by_test_id('back-button').click()
                await page.wait_for_load_state("networkidle", timeout=20000)
                return True

            except Exception as e:
                logger.log(f"  - ❌ An error occurred processing item {i+1}: {e}. Trying next item.", 'error')
                if await page.get_by_test_id('back-button').is_visible(timeout=2000):
                    await page.get_by_test_id('back-button').click()
                    await page.wait_for_load_state("networkidle", timeout=20000)
                continue

        logger.log(f"  - ❌ No suitable asset found after checking all {num_items} results.", 'warning')
        return False

    except Exception as e:
        logger.log(f"  - ❌ A critical error occurred during '{asset_type}' processing for GTIN {gtin}: {e}", 'error')
        if await page.get_by_test_id('back-button').is_visible(timeout=2000):
            await page.get_by_test_id('back-button').click()
            await page.wait_for_load_state("networkidle", timeout=20000)
        return False

async def process_single_gtin(page, gtin: str, asins: list):
    logger.log(f"--- Processing GTIN: {gtin} (Associated ASINs: {', '.join(asins)}) ---")
    
    # Create folders for all ASINs upfront
    asin_folders = create_asin_folders(asins)
    logger.log(f"Created/verified folders for {len(asins)} ASINs")

    downloaded_any = False

    try:
        # Step 1: Download carousel images directly to ASIN folders
        carousel_downloads, max_carousel_seq = await download_carousel_images(page, gtin, asins)
        if carousel_downloads > 0:
            downloaded_any = True

        # # Step 2: Download hero image directly to ASIN folders
        # hero_success = await download_standard_asset(page, gtin, "Mobile Hero", "Mobile Hero", asins)
        # if hero_success:
        #     downloaded_any = True
        
        # Step 3: Download nutrition image with proper sequence numbering
        nutrition_sequence = max_carousel_seq + 1
        logger.log(f"Nutrition image will be saved as pt{nutrition_sequence:02d}")
        nutrition_success = await download_standard_asset(page, gtin, "Nutrition", "Nutrition", asins, is_nutrition=True, nutrition_sequence=nutrition_sequence)
        if nutrition_success:
            downloaded_any = True

        if not downloaded_any:
            logger.log(f"❗️ No assets were downloaded for GTIN {gtin}.", 'warning')
            return False
        else:
            logger.log(f"✅ Successfully downloaded assets for GTIN {gtin} to {len(asins)} ASIN folders")
            return True

    except Exception as e:
        logger.log(f"❌ Error processing GTIN {gtin}: {e}", 'error')
        return False


async def process_all_items(items_to_process):
    # Group items by GTIN to process downloads once per GTIN
    gtin_groups = defaultdict(list)
    for item in items_to_process:
        if item.get('gtin') and item.get('asin'):
             gtin_groups[item['gtin']].append(item['asin'])

    execution_status['total_items'] = len(gtin_groups)
    execution_status['completed_items'] = 0
    failed_gtins = set()
        
    async with async_playwright() as p:
        logger.log("🚀 Launching browser with persistent context...")
        try:
            browser_context = await p.chromium.launch_persistent_context(
                user_data_dir=EDGE_USER_DATA_DIR, 
                executable_path=EDGE_EXECUTABLE_PATH,
                headless=False, 
                accept_downloads=True, 
                slow_mo=50, 
                timeout=60000
            )
            page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()

            for i, (gtin, asins) in enumerate(gtin_groups.items()):
                if not execution_status['running']:
                    logger.log("Process stopped by user.", 'warning')
                    break
                
                item_id = f"GTIN {gtin}"
                execution_status['current_item_id'] = item_id
                execution_status['current_task'] = f'Processing {item_id} ({i+1}/{len(gtin_groups)})'
                socketio.emit('progress', {
                    'progress': int((i / len(gtin_groups)) * 100), 
                    'task': execution_status['current_task']
                })

                gtin_succeeded = await process_single_gtin(page, gtin, asins)
                if not gtin_succeeded:
                    logger.log(f"❗️ GTIN processing failed to download any assets: {gtin}", 'warning')
                    failed_gtins.add(gtin)
                
                execution_status['completed_items'] = i + 1
                socketio.emit('item_completed', {
                    'completed': i + 1, 
                    'total': len(gtin_groups), 
                    'item_id': item_id,
                    'progress': int(((i + 1) / len(gtin_groups)) * 100)
                })

            logger.log("🎉 All tasks completed. Closing browser.")
            await browser_context.close()
        except Exception as e:
            logger.log(f"💥 Critical Error during browser processing: {e}", 'error')

    execution_status['failed_items'] = sorted(list(failed_gtins))
    socketio.emit('execution_summary', {'failed_items': execution_status['failed_items']})


def parse_file(file_path):
    try:
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path, dtype=str)
        else:
            df = pd.read_excel(file_path, dtype=str)
        df.columns = [col.strip().lower() for col in df.columns]
        
        # Remove BMN from required columns
        required_cols = ['gtin']
        if not all(col in df.columns for col in required_cols):
            logger.log(f"File missing required columns: {required_cols}. Found: {list(df.columns)}", 'error')
            return []
        
        df = df[required_cols].dropna(subset=['gtin']).to_dict('records')
        logger.log(f"Successfully parsed {len(df)} items from file.")
        return df
    except Exception as e:
        logger.log(f"Failed to parse file: {e}", 'error')
        return []

# --- New Catalog Download Functions ---
CATALOG_DOWNLOAD_DIR = Path("C:/Downloads/Amazon/Catalogs")
os.makedirs(CATALOG_DOWNLOAD_DIR, exist_ok=True)

def _process_downloaded_files(core_path: Path, fresh_path: Path, logger: WebSocketLogger):
    """
    Reads downloaded Excel files separately. Normalizes UPCs by dropping the check digit
    and stripping leading zeros to create a reliable lookup key.
    """
    logger.log("Processing downloaded catalog files separately...")
    gtin_to_asins_map = defaultdict(set)

    files_to_process = [
        {'name': 'Core Catalog', 'path': core_path},
        {'name': 'Fresh Catalog', 'path': fresh_path}
    ]

    for file_info in files_to_process:
        name, path = file_info['name'], file_info['path']
        if not path.exists():
            logger.log(f"Catalog file not found, skipping: {path}", 'warning')
            continue

        logger.log(f"--- Reading {name} ---")
        try:
            df = pd.read_excel(path, dtype=str)
            df.columns = [col.strip().lower() for col in df.columns]

            required_cols = ['upc', 'asin', 'status']
            if not all(col in df.columns for col in required_cols):
                logger.log(f"Required columns missing in {name}. Skipping.", 'error')
                continue

            valid_statuses = ['Active', 'Inactive', 'Pending Graveyard']
            df_filtered = df[df['status'].str.strip().str.title().isin(valid_statuses)]
            logger.log(f"Found {len(df_filtered)} active/inactive items in {name}.")
            
            for _, row in df_filtered.iterrows():
                upc_str = str(row.get('upc')).replace('.0', '').strip()
                asin = str(row.get('asin', '')).strip()

                # Normalize by removing check digit, then stripping leading zeros.
                if len(upc_str) >= 12:
                    identifier_part = upc_str[:-1]  # Remove last digit (check digit)
                    lookup_key = identifier_part.lstrip('0')
                    if lookup_key and asin:
                        gtin_to_asins_map[lookup_key].add(asin)
        except Exception as e:
            logger.log(f"Error processing {name}: {e}", "error")

    logger.log(f"Finished processing catalogs. Final map contains {len(gtin_to_asins_map)} unique identifiers.")
    return dict(gtin_to_asins_map)


async def download_and_process_catalogs_async(logger: WebSocketLogger):
    """
    Uses Playwright to log in to DayOne, download catalog files, and process them.
    """
    if not DAYONE_USERNAME or not DAYONE_PASSWORD:
        logger.log("DayOne username or password not found in .env file.", 'error')
        raise ValueError("Missing DayOne Credentials in .env file (DAYONE_USER, DAYONE_PASS)")

    core_catalog_path = CATALOG_DOWNLOAD_DIR / "core_catalog.xlsx"
    fresh_catalog_path = CATALOG_DOWNLOAD_DIR / "fresh_catalog.xlsx"

    logger.log("🔍 Starting catalog download process from DayOne Digital...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=50)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()
        
        try:
            logger.log("Navigating to DayOne login page...")
            await page.goto("https://dayonedigital.com/portal/login")
            await page.get_by_role("textbox", name="Username*:").fill(DAYONE_USERNAME)
            await page.get_by_role("textbox", name="Password*:").fill(DAYONE_PASSWORD)
            await page.get_by_role("button", name="Submit").click()
            await page.wait_for_load_state("networkidle", timeout=60000)
            logger.log("Login successful.")

            logger.log("Downloading Core Catalog...")
            await page.get_by_role("button", name="Export").click()
            await page.get_by_role("link", name="Details").wait_for(state="visible")
            async with page.expect_download() as d1_info:
                await page.get_by_role("link", name="Details").click()
            download1 = await d1_info.value
            await download1.save_as(core_catalog_path)
            logger.log(f"✅ Core Catalog saved to {core_catalog_path}")

            logger.log("Navigating to and downloading Fresh Catalog...")
            await page.goto("https://dayonedigital.com/portal/freshcatalog")
            await page.wait_for_load_state("networkidle", timeout=60000)
            await page.get_by_role("button", name="Export").click()
            await page.get_by_role("link", name="Details").wait_for(state="visible")
            async with page.expect_download() as d2_info:
                await page.get_by_role("link", name="Details").click()
            download2 = await d2_info.value
            await download2.save_as(fresh_catalog_path)
            logger.log(f"✅ Fresh Catalog saved to {fresh_catalog_path}")

        except Exception as e:
            logger.log(f"An error occurred during catalog download: {e}", 'error')
            return {}
        finally:
            await browser.close()

    return _process_downloaded_files(core_catalog_path, fresh_catalog_path, logger)


async def lookup_asins_from_files(items_data, gtin_to_asins_map, logger: WebSocketLogger):
    """
    Looks up ASINs for given GTINs using the pre-compiled map from downloaded files.
    """
    logger.log("Matching provided GTINs against the downloaded catalog data...")
    expanded_items = []
    total_items = len(items_data)

    for i, item in enumerate(items_data):
        if not execution_status['running']: break
        
        progress = int(((i + 1) / total_items) * 100)
        execution_status['asin_lookup_progress'] = progress
        socketio.emit('asin_lookup_progress', {'progress': progress, 'current_gtin': item.get('gtin', ''), 'completed': i + 1, 'total': total_items})

        gtin_input_original = item.get('gtin')
        gtin_input_str = str(gtin_input_original).strip()
        bmn = item.get('bmn', '')

        if not gtin_input_str:
            logger.log(f"Skipping item {i + 1} - no GTIN provided", 'warning')
            continue

        # Normalize the 14-digit GTIN by removing check digit and stripping leading zeros.
        if len(gtin_input_str) >= 12:
            identifier_part = gtin_input_str[:-1]  # Remove last digit
            lookup_key = identifier_part.lstrip('0')
            asins = gtin_to_asins_map.get(lookup_key)
        else:
            asins = None  # Input is too short to be a valid GTIN/UPC
        
        if not asins:
            logger.log(f"No active ASIN found for GTIN {gtin_input_original} (Lookup Key: {lookup_key})", 'warning')
            expanded_items.append({'gtin': gtin_input_original, 'asin': '', 'bmn': bmn, 'lookup_status': 'no_asin_found'})
        else:
            logger.log(f"Found {len(asins)} ASIN(s) for GTIN {gtin_input_original}: {', '.join(asins)}", 'info')
            for asin in asins:
                expanded_items.append({'gtin': gtin_input_original, 'asin': asin, 'bmn': bmn, 'lookup_status': 'success'})

    socketio.emit('asin_lookup_complete', {'total_processed': total_items, 'total_expanded': len(expanded_items)})
    logger.log(f"✅ ASIN lookup complete. Expanded {total_items} initial items to {len(expanded_items)} ASIN-specific items.")
    return expanded_items


def run_download_task(items):
    def run_async():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            logger.log("Step 1: Downloading and processing catalog files...")
            gtin_to_asins_map = loop.run_until_complete(download_and_process_catalogs_async(logger))
            
            if not gtin_to_asins_map:
                logger.log("No catalog data available for ASIN lookup.", 'error')
                return

            logger.log("Step 2: Matching GTINs against catalog data...")
            expanded_items = loop.run_until_complete(lookup_asins_from_files(items, gtin_to_asins_map, logger))
            
            if not expanded_items:
                logger.log("No items to process after ASIN lookup.", 'error')
                return

            items_with_asins = [item for item in expanded_items if item.get('asin') and item.get('lookup_status') == 'success']
            if not items_with_asins:
                logger.log("No items have valid ASINs for downloading.", 'error')
                return
            
            logger.log(f"Step 3: Downloading assets for {len(items_with_asins)} ASIN-specific items...")
            loop.run_until_complete(process_all_items(items_with_asins))
            
        except Exception as e:
            logger.log(f"Execution failed in thread: {e}", 'error')
        finally:
            execution_status['running'] = False
            socketio.emit('execution_complete')
            loop.close()
    
    threading.Thread(target=run_async, daemon=True).start()

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/execute', methods=['POST'])
def execute_download():
    if execution_status['running']:
        return jsonify({'error': 'Another task is already running'}), 400
    
    search_data = []
    if 'file' in request.files and request.files['file'].filename != '':
        file = request.files['file']
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        search_data = parse_file(file_path)
        os.remove(file_path)
    else:
        gtin = request.form.get('gtin')
        if gtin:  # Only GTIN is required
            search_data = [{'gtin': gtin}]
        else:
            return jsonify({'error': 'For single entry, GTIN is required.'}), 400

    if not search_data:
        return jsonify({'error': 'No valid items to process.'}), 400

    execution_status.update({
        'running': True, 'logs': [], 'progress': 0, 
        'current_task': 'Starting ASIN lookup...', 
        'total_items': 0, 'completed_items': 0, 
        'current_item_id': '', 'failed_items': [],
        'asin_lookup_progress': 0
    })
    
    run_download_task(search_data)
    return jsonify({
        'success': True, 
        'message': 'Process started with ASIN lookup.', 
        'initial_items': len(search_data)
    })

@app.route('/stop', methods=['POST'])
def stop_execution():
    if execution_status['running']:
        execution_status['running'] = False
        logger.log("Stop signal received. Process will halt shortly.", 'warning')
        return jsonify({'success': True, 'message': 'Stop signal sent.'})
    return jsonify({'error': 'No active process to stop.'}), 400

@socketio.on('connect')
def handle_connect():
    emit('status_update', execution_status)

if __name__ == '__main__':
    # Clean up temp folder on start

    socketio.run(app, debug=False, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)