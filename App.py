
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_socketio import SocketIO, emit
import asyncio
import threading
import os
import sys
from datetime import datetime
import json
from werkzeug.utils import secure_filename
import pandas as pd
from playwright.async_api import async_playwright

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
socketio = SocketIO(app, cors_allowed_origins="*")

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- Configuration Section ---
EDGE_EXECUTABLE_PATH = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
EDGE_USER_DATA_DIR = "C:/Users/G713313/AppData/Local/Microsoft/Edge/User Data"
EDGE_PROFILE = "Default"
BASE_URL = "https://mojo.generalmills.com/"
SEARCH_INPUT_SELECTOR = "#search-search-box-text-tokenfield"
ORIGINAL_OPTION_SELECTOR = 'div.MuiListItemText-root span.MuiTypography-root:has-text("JPEG")'
GRID_ITEM_SELECTOR = "div.MuiCard-root.selectable-content-hub-item"

# Global variables to track execution
execution_status = {
    'running': False, 'logs': [], 'progress': 0, 'current_task': '',
    'total_items': 0, 'completed_items': 0, 'current_search_id': '', 'failed_bmns': []
}

class WebSocketLogger:
    def log(self, message, level='info'):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = {'timestamp': timestamp, 'message': message, 'level': level}
        execution_status['logs'].append(log_entry)
        socketio.emit('log', log_entry)
        print(f"[{timestamp}] {message}")

logger = WebSocketLogger()

# --- Reusable Helper Functions ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'txt', 'csv', 'xlsx', 'xls'}

def determine_language(title):
    title_lower = title.lower()
    if "_fr" in title_lower: return "French", "fr"
    if "_en" in title_lower: return "English", "en"
    return "Multilingual", "ml"

# --- Retailer-Specific Functions ---
def get_sobeys_filename(save_id, lang_code, asset_type):
    asset_mapping = {"Mobile Hero": "left", "Front 3D": "front", "Ingredients": "ing", "Nutrition": "nfp"}
    asset_code = asset_mapping.get(asset_type, "na")
    if asset_type == "Mobile Hero": return f"{save_id}_EA_{lang_code}_na_{asset_code}_na.jpg"
    if asset_type == "Front 3D": return f"{save_id}_EA_{lang_code}_primary_{asset_code}_na.jpg"
    return f"{save_id}_EA_{lang_code}_na_na_{asset_code}.jpg"

def get_instacart_filename(save_id, lang_code, asset_type):
    asset_mapping = {"Mobile Hero": "main", "Left Front - 3D": "sideleft", "Right Front - 3D": "sideright", "Ingredients": "ing", "Nutrition": "nut"}
    suffix = asset_mapping.get(asset_type, "na")
    return f"{save_id}-{suffix}.jpg"

async def select_assets_sobeys(grid_items, asset_label):
    items_to_download, available_languages, items_info = [], set(), []
    for item in grid_items[:3]:
        try:
            title = (await item.inner_text()).strip()
            if title.lower().endswith((".tif", ".jpg", ".png")):
                lang_val, lang_code = determine_language(title)
                available_languages.add(lang_val)
                items_info.append({'item': item, 'lang_val': lang_val, 'lang_code': lang_code})
        except Exception: continue
    
    logger.log(f"Sobeys Logic - Available languages for '{asset_label}': {available_languages}")
    
    languages_to_download = set()
    if asset_label == "Mobile Hero":
        if "English" in available_languages: languages_to_download.add("English")
        if "French" in available_languages: languages_to_download.add("French")
    else:
        if "Multilingual" in available_languages: languages_to_download.add("Multilingual")
        else:
            if "English" in available_languages: languages_to_download.add("English")
            if "French" in available_languages: languages_to_download.add("French")
            
    for info in items_info:
        if info['lang_val'] in languages_to_download: items_to_download.append(info)
    return items_to_download

async def select_assets_instacart(grid_items, asset_label):
    items_to_download = []
    available_languages = set()
    items_info = []
 
    # Collect all available languages and their corresponding items
    for item in grid_items[:10]:  # Increase the limit if needed
        try:
            title = (await item.inner_text()).strip()
            if title.lower().endswith((".tif", ".jpg", ".png")):
                lang_val, lang_code = determine_language(title)
                available_languages.add(lang_val)
                items_info.append({'item': item, 'lang_val': lang_val, 'lang_code': lang_code})
        except Exception:
            continue
 
    logger.log(f"Instacart Logic - Available languages for '{asset_label}': {available_languages}")
 
    # Prioritize languages: English > Multilingual > French
    languages_to_download = set()
    if "English" in available_languages:
        languages_to_download.add("English")
    elif "Multilingual" in available_languages:
        languages_to_download.add("Multilingual")
   
 
    # Select items based on prioritized languages
    for info in items_info:
        if info['lang_val'] in languages_to_download:
            items_to_download.append(info)
 
    return items_to_download

# --- RETAILER CONFIGURATION ---
RETAILER_CONFIGS = {
    "Sobeys": { "download_folder": "C:/Downloads/Sobeys", "asset_types": {"Mobile Hero": "Mobile Hero", "Front 3D": "Front - 3D", "Ingredients": "Ingredients", "Nutrition": "Nutrition"},
        "get_filename_func": get_sobeys_filename, "select_assets_func": select_assets_sobeys, "search_id_key": "bmn", "save_id_key": "article_id" },
    "Instacart": { "download_folder": "C:/Downloads/Instacart", "asset_types": {"Mobile Hero": "Mobile Hero", "Left Front - 3D": "Left Front - 3D", "Right Front - 3D": "Right Front - 3D", "Ingredients": "Ingredients", "Nutrition": "Nutrition"},
        "get_filename_func": get_instacart_filename, "select_assets_func": select_assets_instacart, "search_id_key": "bmn", "save_id_key": "gtin" }
}

# --- Core Automation Logic ---
def _process_dataframe(df):
    search_data = []
    has_sobeys_cols = ('Article ID' in df.columns or 'ArticleID' in df.columns) and 'BMN' in df.columns
    has_instacart_cols = 'GTIN' in df.columns and 'BMN' in df.columns
    if not (has_sobeys_cols or has_instacart_cols):
        logger.log("Required columns not found. Expected (BMN + Article ID/ArticleID) or (BMN + GTIN)", 'error')
        return []
    
    article_col = 'Article ID' if 'Article ID' in df.columns else 'ArticleID'
    for _, row in df.iterrows():
        bmn = str(row.get('BMN', '')).strip()
        article_id = str(row.get(article_col, '')).strip() if has_sobeys_cols else ''
        gtin = str(row.get('GTIN', '')).strip() if has_instacart_cols else ''
        if bmn and bmn != 'nan' and (article_id or gtin):
            search_data.append({'bmn': bmn, 'article_id': article_id, 'gtin': gtin})
    logger.log(f"Parsed {len(search_data)} potential entries from file.")
    return search_data

def parse_search_ids_from_file(file_path):
    try:
        file_ext = file_path.split('.')[-1].lower()
        df = pd.read_excel(file_path, dtype=str) if file_ext in ['xlsx', 'xls'] else pd.read_csv(file_path, dtype=str)
        search_data = _process_dataframe(df)
        
        # Track duplicates
        duplicate_bmns = []
        duplicate_article_ids = []
        duplicate_gtins = []
        seen_bmns = set()
        seen_article_ids = set()
        seen_gtins = set()

        unique_search_data, seen = [], set()
        for item in search_data:
            key = (item['bmn'], item['article_id'], item['gtin'])
            if key not in seen:
                unique_search_data.append(item)
                seen.add(key)
            else:
                # Check for duplicate BMNs
                if item['bmn'] not in seen_bmns:
                    duplicate_bmns.append(item['bmn'])
                seen_bmns.add(item['bmn'])

                # Check for duplicate Article IDs (Sobeys)
                if item['article_id'] not in seen_article_ids:
                    duplicate_article_ids.append(item['article_id'])
                seen_article_ids.add(item['article_id'])

                # Check for duplicate GTINs (Instacart)
                if item['gtin'] not in seen_gtins:
                    duplicate_gtins.append(item['gtin'])
                seen_gtins.add(item['gtin'])

        logger.log(f"Found {len(unique_search_data)} unique entries to process.")
        logger.log(f"Duplicate BMNs: {duplicate_bmns}")
        logger.log(f"Duplicate Article IDs: {duplicate_article_ids}")
        logger.log(f"Duplicate GTINs: {duplicate_gtins}")

        # Return parsed data along with duplicates
        return {
            'unique_search_data': unique_search_data,
            'duplicate_bmns': duplicate_bmns,
            'duplicate_article_ids': duplicate_article_ids,
            'duplicate_gtins': duplicate_gtins
        }
    except Exception as e:
        logger.log(f"Error parsing file: {str(e)}", 'error')
        return {
            'unique_search_data': [],
            'duplicate_bmns': [],
            'duplicate_article_ids': [],
            'duplicate_gtins': []
        }

async def _download_asset_task(page, config, search_id, save_id, asset_label, keyword):
    download_succeeded = False
    restricted_asset = False
    logger.log(f"🔍 Searching for '{asset_label}' with query: {search_id} {keyword}")
    try:
        await page.wait_for_selector(SEARCH_INPUT_SELECTOR, timeout=10000)
        await page.fill(SEARCH_INPUT_SELECTOR, f"{search_id} {keyword}")
        await page.keyboard.press("Enter")
        
        # Wait for network requests to finish after search
        await page.wait_for_load_state('networkidle', timeout=30000)
        await page.wait_for_selector(GRID_ITEM_SELECTOR, timeout=20000)

    except Exception:
        logger.log(f"No results found for '{asset_label}'", 'error')
        return False, restricted_asset

    assets_to_download = await config['select_assets_func'](await page.query_selector_all(GRID_ITEM_SELECTOR), asset_label)
    if not assets_to_download:
        logger.log(f"No suitable asset found for '{asset_label}' based on retailer logic.", 'warning')
        return False, restricted_asset

    for asset_info in assets_to_download:
        try:
            await asset_info['item'].click()
            await page.wait_for_timeout(1000)
            await page.locator('button[data-testid="operation-dropdown-button"][aria-label="Download"]').click()
            async with page.expect_download() as download_info:
                await page.click(ORIGINAL_OPTION_SELECTOR)
            download = await download_info.value
            filename = config['get_filename_func'](save_id, asset_info['lang_code'], asset_label)
            file_path = os.path.join(config['download_folder'], filename)
            await download.save_as(file_path)
            logger.log(f"✅ Saved '{asset_info['lang_val']}' version to: {file_path}")
            download_succeeded = True
            await page.keyboard.press("Escape")
        except Exception as e:
            logger.log(f"Download failed for '{asset_label}': ")
            restricted_asset = True  # Mark as restricted if download fails
            if await page.locator('button[aria-label="Close details"]').is_visible():
                await page.keyboard.press("Escape")
    return download_succeeded, restricted_asset

async def _process_item_for_one_retailer(p, retailer_name, item_data):
    if not execution_status['running']:
        return False, False
    config = RETAILER_CONFIGS[retailer_name]
    os.makedirs(config['download_folder'], exist_ok=True)
    search_id, save_id = item_data.get(config['search_id_key']), item_data.get(config['save_id_key'])
    if not search_id or not save_id:
        logger.log(f"Skipping {retailer_name} for item {item_data.get('bmn')} due to missing IDs.", 'warning')
        return False, False

    logger.log(f"--- Starting {retailer_name} processing for BMN: {search_id} (Save ID: {save_id}) ---")
    contexts, results, restricted = [], [], []
    try:
        pages = [await (await p.chromium.launch_persistent_context(
            user_data_dir=os.path.join(EDGE_USER_DATA_DIR, f"{retailer_name}_{EDGE_PROFILE}_{i}"),
            executable_path=EDGE_EXECUTABLE_PATH, headless=True, accept_downloads=True)).new_page() for i in range(len(config['asset_types']))]
        contexts = [page.context for page in pages]
        await asyncio.gather(*[page.goto(BASE_URL, wait_until="domcontentloaded") for page in pages])
        tasks = [
            _download_asset_task(pages[idx], config, search_id, save_id, asset_label, keyword)
            for idx, (asset_label, keyword) in enumerate(config['asset_types'].items()) if execution_status['running']
        ]
        results = await asyncio.gather(*tasks)
    finally:
        await asyncio.gather(*[context.close() for context in contexts])
        logger.log(f"--- Finished {retailer_name} processing for {search_id} ---")
    return any(result[0] for result in results), any(result[1] for result in results)

async def process_all_items(search_data, selected_retailer):
    retailers_to_process = list(RETAILER_CONFIGS.keys()) if selected_retailer == 'Both' else [selected_retailer]
    failed_bmn_set = set()
    restricted_bmn_set = set()
    logger.log(f"Starting batch download for {len(search_data)} items. Retailers: {retailers_to_process}")

    async with async_playwright() as p:
        for i, item_data in enumerate(search_data, 1):
            if not execution_status['running']:
                logger.log("Download process stopped by user.", 'warning')
                break
            
            search_id_display = item_data.get('bmn', 'N/A')
            execution_status.update({
                'current_search_id': search_id_display,
                'current_task': f'Processing {search_id_display} ({i}/{len(search_data)})'
            })
            socketio.emit('progress', {'progress': int((i - 1) / len(search_data) * 100), 'task': execution_status['current_task']})
            logger.log(f"--- Item {i}/{len(search_data)} | BMN: {search_id_display} ---")

            item_succeeded = False
            item_restricted = False
            for retailer_name in retailers_to_process:
                if not execution_status['running']:
                    break
                succeeded, restricted = await _process_item_for_one_retailer(p, retailer_name, item_data)
                if succeeded:
                    item_succeeded = True
                if restricted:
                    item_restricted = True
            
            if not item_succeeded and item_restricted:
                logger.log(f"❗️ Restricted assets for BMN '{search_id_display}' for any requested retailer.", 'warning')
                restricted_bmn_set.add(search_id_display)
            elif not item_succeeded:
                logger.log(f"❗️ No assets found for BMN '{search_id_display}' for any requested retailer.", 'warning')
                failed_bmn_set.add(search_id_display)

            execution_status['completed_items'] = i
            socketio.emit('item_completed', {'completed': i, 'total': len(search_data), 'search_id': search_id_display})
            await asyncio.sleep(1)

    execution_status['failed_bmns'] = sorted(list(failed_bmn_set))
    execution_status['restricted_bmns'] = sorted(list(restricted_bmn_set))
    if execution_status['failed_bmns']:
        logger.log(f"SUMMARY: {len(execution_status['failed_bmns'])} BMN(s) had no assets found: {execution_status['failed_bmns']}", 'warning')
    if execution_status['restricted_bmns']:
        logger.log(f"SUMMARY: {len(execution_status['restricted_bmns'])} BMN(s) had restricted assets: {execution_status['restricted_bmns']}", 'warning')
    
    socketio.emit('execution_summary', {
        'failed_bmns': execution_status['failed_bmns'],
        'restricted_bmns': execution_status['restricted_bmns']
    })
    logger.log(f"Batch download finished. Processed {execution_status['completed_items']}/{len(search_data)} items.")
    execution_status['current_task'] = 'Completed'
    socketio.emit('progress', {'progress': 100, 'task': 'Completed'})

# --- Flask and SocketIO Routes ---
def run_download_task(search_data, retailer):
    def run_async():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        try: loop.run_until_complete(process_all_items(search_data, retailer))
        except Exception as e: logger.log(f"Execution failed in thread: {str(e)}", 'error')
        finally:
            execution_status['running'] = False; socketio.emit('execution_complete'); loop.close()
    threading.Thread(target=run_async, daemon=True).start()

@app.route('/')
def index(): return render_template('index.html')

@app.route('/execute', methods=['POST'])
def execute_download():
    if execution_status['running']:
        return jsonify({'error': 'Another task is already running'}), 400

    retailer = request.form.get('retailer')
    search_data = []
    duplicate_data = {}

    if 'file' in request.files and request.files['file'].filename != '':
        file = request.files['file']
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type.'}), 400

        file_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
        file.save(file_path)
        parsed_data = parse_search_ids_from_file(file_path)
        os.remove(file_path)

        search_data = parsed_data['unique_search_data']
        duplicate_data = {
            'duplicate_bmns': parsed_data['duplicate_bmns'],
            'duplicate_article_ids': parsed_data['duplicate_article_ids'],
            'duplicate_gtins': parsed_data['duplicate_gtins']
        }
    else:
        # Handle single entry case
        bmn = request.form.get('bmn')
        if retailer == 'Sobeys':
            article_id = request.form.get('article_id')
            if not bmn or not article_id:
                return jsonify({'error': 'BMN and Article ID are required for Sobeys'}), 400
            search_data = [{'bmn': bmn, 'article_id': article_id, 'gtin': article_id}]
        elif retailer == 'Instacart':
            gtin = request.form.get('gtin')
            if not bmn or not gtin:
                return jsonify({'error': 'BMN and GTIN are required for Instacart'}), 400
            search_data = [{'bmn': bmn, 'gtin': gtin, 'article_id': gtin}]
        elif retailer == 'Both':
            article_id = request.form.get('article_id')
            gtin = request.form.get('gtin')
            if not bmn or not (article_id or gtin):
                return jsonify({'error': 'BMN and at least one of Article ID or GTIN are required'}), 400
            search_data = [{'bmn': bmn, 'article_id': article_id or gtin, 'gtin': gtin or article_id}]
        else:
            return jsonify({'error': 'A retailer selection is required for single entry.'}), 400

    if not search_data:
        return jsonify({'error': 'No valid search data found or parsed.'}), 400

    execution_status.update({
        'running': True,
        'logs': [],
        'progress': 0,
        'current_task': 'Starting...',
        'total_items': len(search_data),
        'completed_items': 0,
        'current_search_id': '',
        'failed_bmns': []
    })

    run_download_task(search_data, retailer)

    return jsonify({
        'success': True,
        'message': f'Download started for {len(search_data)} item(s) for {retailer}.',
        'total_items': len(search_data),
        'duplicates': duplicate_data
    })

@app.route('/stop', methods=['POST'])
def stop_execution():
    if execution_status['running']: execution_status['running'] = False; return jsonify({'success': True, 'message': 'Stop signal sent.'})
    return jsonify({'error': 'No active download process to stop.'}), 400

@app.route('/status')
def get_status(): return jsonify(execution_status)

@socketio.on('connect')
def handle_connect(): emit('status_update', execution_status)

@socketio.on('stop_execution')
def handle_stop_execution_socket():
    if execution_status['running']: execution_status['running'] = False; logger.log("Stop signal received via WebSocket.", 'warning'); emit('execution_stopped')

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)