import asyncio  # Importing asyncio for asynchronous programming
import os  # Importing os for interacting with the operating system
import re  # Importing re for regular expressions
import shutil  # Importing shutil for file operations
import threading  # Importing threading for running tasks in separate threads
from collections import defaultdict  # Importing defaultdict for creating dictionaries with default values
import pandas as pd  # Importing pandas for data manipulation and analysis
import requests  # Importing requests for making HTTP requests
from flask import Flask, render_template, request, jsonify  # Importing Flask modules for web application
from flask_socketio import SocketIO, emit  # Importing SocketIO for real-time communication
from playwright.async_api import async_playwright, Page, expect, TimeoutError  # Importing Playwright for browser automation
from werkzeug.utils import secure_filename  # Importing secure_filename for secure file handling
from datetime import datetime  # Importing datetime for date and time operations
import getpass  # Importing getpass for secure password input
from dotenv import load_dotenv  # Importing dotenv to load environment variables
from pathlib import Path  # Importing Path for file path operations

# Load environment variables from a .env file
load_dotenv()

# --- Flask and Socket.IO Setup ---
app = Flask(__name__)  # Initialize Flask application
app.config['UPLOAD_FOLDER'] = 'uploads'  # Set the upload folder for files
socketio = SocketIO(app, cors_allowed_origins="*")  # Initialize Socket.IO for real-time communication
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)  # Create the upload folder if it doesn't exist

# --- CONFIGURATION ---
EDGE_EXECUTABLE_PATH = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"  # Path to Microsoft Edge executable
EDGE_USER_DATA_DIR = "C:/Temp/EdgeUserData"  # Path to store Edge user data
DOWNLOAD_FOLDER = "C:/Downloads/Amazon"  # Folder to store downloaded files
BASE_URL = 'https://picassetimporter.k8s.genmills.com'  # Base URL for API
API_ENDPOINT_TEMPLATE = f"{BASE_URL}/api/v1/assets/product/{{gtin}}/json"  # API endpoint template

# DayOne Configuration
DAYONE_USERNAME = os.getenv("DAYONE_USER")  # Load DayOne username from environment variables
DAYONE_PASSWORD = os.getenv("DAYONE_PASS")  # Load DayOne password from environment variables
CATALOG_DOWNLOAD_DIR = Path("catalog_cache")  # Directory to store downloaded catalog files
os.makedirs(CATALOG_DOWNLOAD_DIR, exist_ok=True)  # Create the catalog directory if it doesn't exist

# --- Global State Management ---
execution_status = {  # Dictionary to manage the execution state
    'running': False, 'logs': [], 'progress': 0, 'current_task': '',
    'total_items': 0, 'completed_items': 0, 'current_item_id': '', 'failed_items': [],
    'asin_lookup_progress': 0
}

class WebSocketLogger:  # Logger class for real-time logging via WebSocket
    def log(self, message, level='info'):
        timestamp = datetime.now().strftime("%H:%M:%S")  # Get the current timestamp
        log_entry = {'timestamp': timestamp, 'message': message, 'level': level}  # Create a log entry
        execution_status['logs'].append(log_entry)  # Append the log entry to the execution status
        socketio.emit('log', log_entry)  # Emit the log entry via WebSocket
        print(f"[{timestamp}] {message}")  # Print the log entry to the console

logger = WebSocketLogger()  # Initialize the logger

# --- NEW ASIN LOOKUP (FILE-BASED) ---

def _process_downloaded_files(core_path: Path, fresh_path: Path, logger: WebSocketLogger):
    """
    Reads downloaded Excel files separately. Normalizes UPCs by dropping the check digit
    and stripping leading zeros to create a reliable lookup key.
    """
    logger.log("Processing downloaded catalog files separately...")  # Log the start of processing
    gtin_to_asins_map = defaultdict(set)  # Create a dictionary to map GTINs to ASINs

    files_to_process = [  # List of files to process
        {'name': 'Core Catalog', 'path': core_path},
        {'name': 'Fresh Catalog', 'path': fresh_path}
    ]

    for file_info in files_to_process:  # Iterate over the files to process
        name, path = file_info['name'], file_info['path']  # Extract file name and path
        if not path.exists():  # Check if the file exists
            logger.log(f"Catalog file not found, skipping: {path}", 'warning')  # Log a warning if the file is missing
            continue

        logger.log(f"--- Reading {name} ---")  # Log the start of reading the file
        try:
            df = pd.read_excel(path, dtype=str)  # Read the Excel file into a DataFrame
            df.columns = [col.strip().lower() for col in df.columns]  # Normalize column names

            required_cols = ['upc', 'asin', 'status']  # Required columns in the file
            if not all(col in df.columns for col in required_cols):  # Check if required columns are present
                logger.log(f"Required columns missing in {name}. Skipping.", 'error')  # Log an error if columns are missing
                continue

            valid_statuses = ['Active', 'Inactive','Pending Graveyard']  # Valid statuses for filtering
            df_filtered = df[df['status'].str.strip().str.title().isin(valid_statuses)]  # Filter rows based on status
            logger.log(f"Found {len(df_filtered)} active/inactive items in {name}.")  # Log the number of valid items
            
            for _, row in df_filtered.iterrows():  # Iterate over the filtered rows
                upc_str = str(row.get('upc')).replace('.0', '').strip()  # Normalize the UPC
                asin = str(row.get('asin', '')).strip()  # Get the ASIN

                # **NEW LOGIC**: Normalize by removing check digit, then stripping leading zeros.
                if len(upc_str) >= 12:  # Check if the UPC is valid
                    identifier_part = upc_str[:-1]  # Remove the check digit
                    lookup_key = identifier_part.lstrip('0')  # Strip leading zeros
                    if lookup_key and asin:  # Check if the lookup key and ASIN are valid
                        gtin_to_asins_map[lookup_key].add(asin)  # Add the ASIN to the map
        except Exception as e:
            logger.log(f"Error processing {name}: {e}", "error")  # Log an error if processing fails

    logger.log(f"Finished processing catalogs. Final map contains {len(gtin_to_asins_map)} unique identifiers.")  # Log the completion of processing
    return dict(gtin_to_asins_map)  # Return the map of GTINs to ASINs


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
        browser = await p.chromium.launch(headless=False, slow_mo=50)  # Launch the browser in non-headless mode
        context = await browser.new_context(accept_downloads=True)  # Create a new browser context with download support
        page = await context.new_page()  # Open a new page in the browser

        try:
            logger.log("Navigating to DayOne login page...")  # Log the navigation to the login page
            await page.goto("https://dayonedigital.com/portal/login")  # Navigate to the DayOne login page
            await page.get_by_role("textbox", name="Username*:").fill(DAYONE_USERNAME)  # Fill in the username
            await page.get_by_role("textbox", name="Password*:").fill(DAYONE_PASSWORD)  # Fill in the password
            await page.get_by_role("button", name="Submit").click()  # Click the submit button
            await page.wait_for_load_state("networkidle", timeout=60000)  # Wait for the page to load completely
            logger.log("Login successful.")  # Log successful login

            logger.log("Downloading Core Catalog...")  # Log the start of Core Catalog download
            await page.get_by_role("button", name="Export").click()  # Click the export button
            await page.get_by_role("link", name="Details").wait_for(state="visible")  # Wait for the details link to be visible
            async with page.expect_download() as d1_info:  # Expect a download to start
                await page.get_by_role("link", name="Details").click()  # Click the details link
            download1 = await d1_info.value  # Get the download object
            await download1.save_as(core_catalog_path)  # Save the downloaded file to the specified path
            logger.log(f"✅ Core Catalog saved to {core_catalog_path}")  # Log the successful download

            logger.log("Navigating to and downloading Fresh Catalog...")  # Log the start of Fresh Catalog download
            await page.goto("https://dayonedigital.com/portal/freshcatalog")  # Navigate to the Fresh Catalog page
            await page.wait_for_load_state("networkidle", timeout=60000)  # Wait for the page to load completely
            await page.get_by_role("button", name="Export").click()  # Click the export button
            await page.get_by_role("link", name="Details").wait_for(state="visible")  # Wait for the details link to be visible
            async with page.expect_download() as d2_info:  # Expect a download to start
                await page.get_by_role("link", name="Details").click()  # Click the details link
            download2 = await d2_info.value  # Get the download object
            await download2.save_as(fresh_catalog_path)  # Save the downloaded file to the specified path
            logger.log(f"✅ Fresh Catalog saved to {fresh_catalog_path}")  # Log the successful download

        except Exception as e:
            logger.log(f"An error occurred during catalog download: {e}", 'error')  # Log any errors that occur
            return {}  # Return an empty dictionary in case of an error
        finally:
            await browser.close()  # Close the browser

    return _process_downloaded_files(core_catalog_path, fresh_catalog_path, logger)  # Process the downloaded files and return the result


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

        if not gtin_input_str:  # Check if GTIN is provided
            logger.log(f"Skipping item {i + 1} - no GTIN provided", 'warning')  # Log a warning for missing GTIN
            continue  # Skip to the next item

        # **NEW LOGIC**: Normalize the 14-digit GTIN by removing check digit and stripping leading zeros.
        if len(gtin_input_str) >= 12:  # Check if the GTIN is at least 12 digits long
            identifier_part = gtin_input_str[:-1]  # Remove the last digit (check digit)
            lookup_key = identifier_part.lstrip('0')  # Strip leading zeros to create the lookup key
            asins = gtin_to_asins_map.get(lookup_key)  # Retrieve ASINs from the map using the lookup key
        else:
            asins = None  # Set ASINs to None if the GTIN is too short

        if not asins:  # Check if no ASINs were found
            logger.log(f"No active ASIN found for GTIN {gtin_input_original} (Lookup Key: {lookup_key})", 'warning')  # Log a warning
            expanded_items.append({'gtin': gtin_input_original, 'asin': '', 'bmn': bmn, 'lookup_status': 'no_asin_found'})  # Add a placeholder entry
        else:
            logger.log(f"Found {len(asins)} ASIN(s) for GTIN {gtin_input_original}: {', '.join(asins)}", 'info')  # Log the found ASINs
            for asin in asins:  # Iterate over the found ASINs
                expanded_items.append({'gtin': gtin_input_original, 'asin': asin, 'bmn': bmn, 'lookup_status': 'success'})  # Add each ASIN to the expanded items

    socketio.emit('asin_lookup_complete', {'total_processed': total_items, 'total_expanded': len(expanded_items)})  # Emit the completion event
    logger.log(f"✅ ASIN lookup complete. Expanded {total_items} initial items to {len(expanded_items)} ASIN-specific items.")  # Log the completion of the lookup
    return expanded_items  # Return the expanded items


# --- Helper Functions (Unchanged) ---
def create_asin_folders(asins):
    asin_folders = {}
    for asin in asins:
        folder_path = os.path.join(DOWNLOAD_FOLDER, asin)
        os.makedirs(folder_path, exist_ok=True)
        asin_folders[asin] = folder_path
    return asin_folders

def save_image_from_url(url, folder_path, file_name):
    try:
        with requests.get(url, stream=True, timeout=90) as r:
            r.raise_for_status()
            full_path = os.path.join(folder_path, file_name)
            with open(full_path, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        return True
    except requests.exceptions.RequestException as e:
        logger.log(f"Failed to download image from {url}. Error: {e}", 'error')
        return False

# --- Core Automation Logic (Unchanged) ---
async def process_single_gtin_api(page, gtin: str, asins: list, bmn: str = None):
    logger.log(f"--- Processing GTIN: {gtin} (Associated ASINs: {', '.join(asins)}) ---")
    logger.log(f"BMN received for processing: {bmn}")  # Debug log for BMN

    asin_folders = create_asin_folders(asins)
    logger.log(f"Created/verified folders for {len(asins)} ASINs")

    api_url = API_ENDPOINT_TEMPLATE.format(gtin=gtin)
    logger.log(f"Fetching asset data from API: {api_url}")

    try:
        json_response = await page.evaluate(f'fetch("{api_url}").then(res => res.json())')
        
        if not json_response or 'assets' not in json_response:
            logger.log(f"No 'assets' key found in API response for GTIN {gtin}. Skipping.", 'warning')
            return False

        assets = json_response['assets']
        logger.log(f"Found {len(assets)} assets in API response for GTIN {gtin}.")

        # Process Carousel Images
        carousel_assets = []
        downloaded_priorities = set()

        for asset in assets:
            priority_str = asset.get('carouselPriority')
            if priority_str and priority_str.isdigit():
                priority = int(priority_str)
                if priority not in downloaded_priorities:
                    carousel_assets.append({'priority': priority, 'asset': asset})
                    downloaded_priorities.add(priority)

        max_carousel_seq = 0
        downloaded_any = False

        for item in sorted(carousel_assets, key=lambda x: x['priority']):
            priority = item['priority']
            asset = item['asset']
            max_carousel_seq = max(max_carousel_seq, priority)

            if asset.get('pimRenditions') and asset['pimRenditions'][0].get('url'):
                url = asset['pimRenditions'][0]['url']
                filename = f".PT{priority:02d}.jpeg"
                logger.log(f"  Downloading Carousel image pt{priority:02d}...")

                for asin in asins:
                    asin_filename = f"{asin}{filename}"
                    if save_image_from_url(url, asin_folders[asin], asin_filename):
                        downloaded_any = True
                        logger.log(f"  ✅ Successfully downloaded pt{priority:02d} to folder {asin_folders[asin]}.")

        # Handle BMN-related assets if BMN is provided
        if bmn:
            bmn_api_url = f"{BASE_URL}/api/v1/assets/version/{bmn}/json"
            logger.log(f"Fetching BMN-related assets from API: {bmn_api_url}")  # Debug log for BMN API call
            try:
                bmn_response = await page.evaluate(f'fetch("{bmn_api_url}").then(res => res.json())')
                if not bmn_response or 'assets' not in bmn_response:
                    logger.log(f"No 'assets' key found in BMN API response for BMN {bmn}. Skipping.", 'warning')
                else:
                    bmn_assets = bmn_response['assets']
                    logger.log(f"Found {len(bmn_assets)} assets in BMN API response for BMN {bmn}.")

                    # Save Nutrition-Ingredients Combined asset
                    nutrition_asset = next((asset for asset in bmn_assets if asset.get('packageFacingIndicator') == "Nutrition-Ingredients Combined"), None)
                    if nutrition_asset and nutrition_asset.get('pimRenditions') and nutrition_asset['pimRenditions'][0].get('url'):
                        nutrition_url = nutrition_asset['pimRenditions'][0]['url']
                        nutrition_filename = f".PT{max_carousel_seq + 1:02d}.jpeg"
                        logger.log(f"  Downloading Nutrition-Ingredients Combined asset...")

                        for asin in asins:
                            asin_filename = f"{asin}{nutrition_filename}"
                            if save_image_from_url(nutrition_url, asin_folders[asin], asin_filename):
                                logger.log(f"  ✅ Successfully downloaded Nutrition-Ingredients Combined to folder {asin_folders[asin]}.")

                    # Save Mobile Hero asset
                    mobile_hero_asset = next((asset for asset in bmn_assets if asset.get('packageFacingIndicator') == "Mobile Hero"), None)
                    if mobile_hero_asset and mobile_hero_asset.get('pimRenditions') and mobile_hero_asset['pimRenditions'][0].get('url'):
                        mobile_hero_url = mobile_hero_asset['pimRenditions'][0]['url']
                        logger.log(f"  Downloading Mobile Hero asset...")

                        for asin in asins:
                            mobile_hero_folder = os.path.join(DOWNLOAD_FOLDER, "Mobile Hero")
                            os.makedirs(mobile_hero_folder, exist_ok=True)
                            mobile_hero_filename = f"{asin}.main.jpeg"
                            if save_image_from_url(mobile_hero_url, mobile_hero_folder, mobile_hero_filename):
                                logger.log(f"  ✅ Successfully downloaded Mobile Hero to folder {mobile_hero_folder}.")

            except Exception as e:
                logger.log(f"Error fetching BMN-related assets: {e}", 'error')

        if not downloaded_any:
            logger.log(f"❗️ No suitable assets were downloaded for GTIN {gtin}.", 'warning')
            return False

        return True

    except Exception as e:
        logger.log(f"❌ Critical error processing GTIN {gtin} via API: {e}", 'error')
        return False

async def process_all_items(items_to_process):
    gtin_groups = defaultdict(list)
    bmn_map = {}  # Map GTIN to BMN
    for item in items_to_process:
        if item.get('gtin') and item.get('asin'):
            gtin_groups[item['gtin']].append(item['asin'])
        if item.get('gtin') and item.get('bmn'):  # Map GTIN to BMN
            bmn_map[item['gtin']] = item['bmn']

    execution_status.update({'total_items': len(gtin_groups), 'completed_items': 0})
    failed_gtins = set()
    async with async_playwright() as p:
        logger.log("🚀 Launching browser with persistent context for authentication...")
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
            await page.goto(BASE_URL, wait_until="domcontentloaded")
            logger.log(f"Browser authenticated. Starting API processing loop.")
            for i, (gtin, asins) in enumerate(gtin_groups.items()):
                if not execution_status['running']:
                    logger.log("Process stopped by user.", 'warning')
                    break
                item_id = f"GTIN {gtin}"
                execution_status.update({
                    'current_item_id': item_id,
                    'current_task': f'Processing {item_id} ({i+1}/{len(gtin_groups)})'
                })
                socketio.emit('progress', {
                    'progress': int((i / len(gtin_groups)) * 100),
                    'task': execution_status['current_task']
                })
                bmn = bmn_map.get(gtin)  # Get the BMN for the current GTIN
                if not await process_single_gtin_api(page, gtin, asins, bmn):  # Pass BMN to the function
                    logger.log(f"❗️ GTIN processing failed to download assets: {gtin}", 'warning')
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
            logger.log(f"💥 Critical Error during browser/API processing: {e}", 'error')
    execution_status['failed_items'] = sorted(list(failed_gtins))
    socketio.emit('execution_summary', {'failed_items': execution_status['failed_items']})

def parse_file(file_path):
    try:
        df = pd.read_csv(file_path, dtype=str) if file_path.endswith('.csv') else pd.read_excel(file_path, dtype=str)
        df.columns = [col.strip().lower() for col in df.columns]
        if 'gtin' not in df.columns: logger.log(f"File missing required column: 'gtin'.", 'error'); return []
        if 'bmn' not in df.columns: df['bmn'] = ''
        df = df[['gtin', 'bmn']].dropna(subset=['gtin']).to_dict('records')
        logger.log(f"Successfully parsed {len(df)} items from file.")
        return df
    except Exception as e: logger.log(f"Failed to parse file: {e}", 'error'); return []

def run_download_task(items):
    def run_async():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            logger.log("--- STEP 1: DOWNLOADING & PROCESSING DAYONE CATALOGS ---")
            gtin_to_asins_map = loop.run_until_complete(download_and_process_catalogs_async(logger))
            if not gtin_to_asins_map: logger.log("Failed to create ASIN lookup map from catalogs. Halting.", 'error'); return
            if not execution_status['running']: logger.log("Process stopped after catalog download.", 'warning'); return
            
            logger.log("--- STEP 2: MATCHING INPUT GTINS TO CATALOG DATA ---")
            expanded_items = loop.run_until_complete(lookup_asins_from_files(items, gtin_to_asins_map, logger))
            if not execution_status['running'] or not expanded_items: logger.log("ASIN lookup stopped or yielded no results. Halting.", 'warning'); return
            
            items_with_asins = [item for item in expanded_items if item.get('asin') and item.get('lookup_status') == 'success']
            if not items_with_asins: logger.log("No items have valid ASINs for downloading.", 'error'); return
            
            logger.log(f"--- STEP 3: DOWNLOADING ASSETS FOR {len(items_with_asins)} ASIN-SPECIFIC ITEMS ---")
            loop.run_until_complete(process_all_items(items_with_asins))
        except Exception as e: logger.log(f"Execution failed in thread: {e}", 'error')
        finally:
            execution_status['running'] = False; socketio.emit('execution_complete'); loop.close()
    threading.Thread(target=run_async, daemon=True).start()


# --- Flask Routes ---
@app.route('/')  # Define the route for the home page
def index(): return render_template('index.html')  # Render the index.html template

@app.route('/execute', methods=['POST'])  # Define the route for executing the download process
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
        bmn = request.form.get('bmn', '')  # Get BMN from the form
        logger.log(f"Received GTIN: {gtin}, BMN: {bmn}")  # Debug log for GTIN and BMN
        if gtin:
            search_data = [{'gtin': gtin, 'bmn': bmn}]  # Include BMN in the search data
        else:
            return jsonify({'error': 'For single entry, GTIN is required.'}), 400

    if not search_data:
        return jsonify({'error': 'No valid items to process.'}), 400

    execution_status.update({
        'running': True, 'logs': [], 'progress': 0, 'current_task': 'Starting catalog download...',
        'total_items': 0, 'completed_items': 0, 'current_item_id': '', 'failed_items': [],
        'asin_lookup_progress': 0
    })
    run_download_task(search_data)
    return jsonify({'success': True, 'message': 'Process started. Downloading DayOne catalogs first.', 'initial_items': len(search_data)})  # Return a success response

@app.route('/stop', methods=['POST'])  # Define the route for stopping the execution
def stop_execution():
    if execution_status['running']:
        execution_status['running'] = False; logger.log("Stop signal received. Process will halt shortly.", 'warning')  # Stop the execution
        return jsonify({'success': True, 'message': 'Stop signal sent.'})  # Return a success response
    return jsonify({'error': 'No active process to stop.'}), 400  # Return an error if no process is running

@socketio.on('connect')  # Define the event for WebSocket connection
def handle_connect(): emit('status_update', execution_status)  # Emit the current execution status

if __name__ == '__main__':  # Check if the script is run directly
    socketio.run(app, debug=False, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)  # Run the Flask application