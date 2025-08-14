from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import asyncio
import threading
import os
import pandas as pd
import requests
import shutil
from datetime import datetime
from werkzeug.utils import secure_filename
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
socketio = SocketIO(app, cors_allowed_origins="*")

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- Configuration Section ---
BASE_URL = "https://picassetimporter.k8s.genmills.com"
API_ENDPOINT_TEMPLATE = f"{BASE_URL}/api/v1/assets/version/{{bmn}}/json"

# --- Global State Management ---
execution_status = {
    'running': False, 'logs': [], 'progress': 0, 'current_task': '',
    'total_items': 0, 'completed_items': 0, 'current_search_id': '',
    'not_in_mojo_bmns': [],  # For BMNs not found in Mojo
    'restricted_bmns': []   # For BMNs with restricted assets
}
execution_lock = threading.Lock()  # Lock for thread-safe updates

class WebSocketLogger:
    def log(self, message, level='info'):
        """Logs a message and emits it to the frontend via SocketIO."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = {'timestamp': timestamp, 'message': message, 'level': level}
        with execution_lock:  # Ensure thread-safe updates
            execution_status['logs'].append(log_entry)
            # Keep only the last 100 logs to avoid memory issues
            if len(execution_status['logs']) > 100:
                execution_status['logs'].pop(0)
        socketio.emit('log', log_entry)  # Emit the log to the frontend
        print(f"[{timestamp}] {message}")

logger = WebSocketLogger()

# --- Helper Functions ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'txt', 'csv', 'xlsx', 'xls'}

def _get_jpg_url(asset):
    """Finds the JPG download URL from the pimRenditions."""
    if 'pimRenditions' in asset:
        for rendition in asset['pimRenditions']:
            if rendition.get('format', '').lower() == 'jpg':
                return rendition.get('url')
    return None

def save_image_from_url(url, file_path):
    """Downloads an image from a URL and saves it."""
    try:
        with requests.get(url, stream=True, timeout=90, verify=False) as r:
            r.raise_for_status()
            with open(file_path, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        return True
    except requests.exceptions.RequestException as e:
        logger.log(f"Failed to download image from {url}. Error: {e}", 'error')
        return False

# --- Retailer-Specific Naming Conventions ---
def get_sobeys_filename(article_id, lang_code, asset_type):
    asset_mapping = {
        "Mobile Hero": "left", "Front - 3D": "front",
        "Ingredients": "ing", "Nutrition": "nfp"
    }
    asset_code = asset_mapping.get(asset_type, "na")

    if asset_type == "Mobile Hero":
        return f"{article_id}_EA_{lang_code}_na_{asset_code}_na.jpg"
    if asset_type == "Front - 3D":
        return f"{article_id}_EA_{lang_code}_primary_{asset_code}_na.jpg"
    
    return f"{article_id}_EA_{lang_code}_na_na_{asset_code}.jpg"

def get_instacart_filename(gtin, asset_type):
    asset_mapping = {
        "Mobile Hero": "main", "Left Front - 3D": "sideleft",
        "Right Front - 3D": "sideright", "Ingredients": "ing", "Nutrition": "nut"
    }
    suffix = asset_mapping.get(asset_type, "na")
    return f"{gtin}-{suffix}.jpg"

# --- API-Based Retailer Logic ---
def _select_and_download_sobeys(assets_for_type, asset_label, article_id, download_folder):
    """Contains Sobeys specific language and download logic."""
    if not assets_for_type: return False
    assets_to_download = []
    
    en_asset = next((a for a in assets_for_type if "English" in a['languages'] and 'French-Canadian' not in a['languages']), None)
    fr_asset = next((a for a in assets_for_type if "French-Canadian" in a['languages']), None)
    ml_asset = next((a for a in assets_for_type if "English" in a['languages'] and "French-Canadian" in a['languages']), None)

    if asset_label == "Mobile Hero":
        if en_asset: assets_to_download.append({'asset': en_asset, 'lang': 'en'})
        if fr_asset: assets_to_download.append({'asset': fr_asset, 'lang': 'fr'})
    else:
        if ml_asset:
            assets_to_download.append({'asset': ml_asset, 'lang': 'ml'})
        else:
            if en_asset: assets_to_download.append({'asset': en_asset, 'lang': 'en'})
            if fr_asset: assets_to_download.append({'asset': fr_asset, 'lang': 'fr'})
    
    if not assets_to_download: return False
    
    download_succeeded = False
    for item in assets_to_download:
        url = _get_jpg_url(item['asset'])
        if not url: continue
        
        filename = get_sobeys_filename(article_id, item['lang'], asset_label)
        file_path = os.path.join(download_folder, filename)
        if save_image_from_url(url, file_path):
            logger.log(f"✅ Saved Sobeys '{asset_label}' ({item['lang']}) to: {filename}")
            download_succeeded = True
    return download_succeeded

def _select_and_download_instacart(assets_for_type, asset_label, gtin, download_folder):
    """Contains Instacart specific language and download logic."""
    if not assets_for_type: return False
    
    chosen_asset = next((a for a in assets_for_type if "English" in a['languages'] and 'French-Canadian' not in a['languages']), None)
    if not chosen_asset:
        chosen_asset = next((a for a in assets_for_type if "English" in a['languages'] and "French-Canadian" in a['languages']), None)

    if not chosen_asset: return False
    url = _get_jpg_url(chosen_asset)
    if not url: return False
    
    filename = get_instacart_filename(gtin, asset_label)
    file_path = os.path.join(download_folder, filename)
    if save_image_from_url(url, file_path):
        logger.log(f"✅ Saved Instacart '{asset_label}' to: {filename}")
        return True
    return False

# --- RETAILER CONFIGURATION ---
RETAILER_CONFIGS = {
    "Sobeys": {
        "download_folder": "C:/Downloads/Sobeys",
        "asset_types": {"Mobile Hero": "Mobile Hero", "Front - 3D": "Front - 3D", "Ingredients": "Ingredients", "Nutrition": "Nutrition"},
        "save_id_key": "article_id",
        "download_func": _select_and_download_sobeys
    },
    "Instacart": {
        "download_folder": "C:/Downloads/Instacart",
        "asset_types": {"Mobile Hero": "Mobile Hero", "Left Front - 3D": "Left Front - 3D", "Right Front - 3D": "Right Front - 3D", "Ingredients": "Ingredients", "Nutrition": "Nutrition"},
        "save_id_key": "gtin",
        "download_func": _select_and_download_instacart
    }
}

# --- Core Logic ---
def process_bmn(item_data, retailers_to_process):
    """Processes a single BMN."""
    bmn = item_data.get('bmn')
    with execution_lock:  # Thread-safe updates
        execution_status['current_search_id'] = bmn
        execution_status['current_task'] = f"Processing BMN {bmn}"
        if not execution_status['running']:
            return False

    logger.log(f"Fetching data for BMN: {bmn}")
    api_url = API_ENDPOINT_TEMPLATE.format(bmn=bmn)

    try:
        response = requests.get(api_url, timeout=30, verify=False)
        
        # Handle API errors
        if not response.ok:
            is_not_found = False
            if response.status_code == 500:
                try:
                    error_json = response.json()
                    if "NotFound" in error_json.get("title", ""):
                        is_not_found = True
                except ValueError:  # Not a JSON response
                    pass
            
            if is_not_found:
                logger.log(f"BMN {bmn} not found in Mojo. Adding to 'Not in Mojo' list.", 'warning')
                with execution_lock:
                    execution_status['not_in_mojo_bmns'].append(bmn)
                    socketio.emit('status_update', execution_status)  # Emit updated status
            else:
                logger.log(f"API Error for BMN {bmn}: Status {response.status_code}, Reason: {response.reason}", 'error')
            return False

        data = response.json()

    except requests.exceptions.RequestException as e:
        logger.log(f"Network error fetching API data for BMN {bmn}: {e}", 'error')
        with execution_lock:
            execution_status['not_in_mojo_bmns'].append(bmn)
            socketio.emit('status_update', execution_status)  # Emit updated status
        return False

    # Check if assets are present
    if 'assets' not in data or not data.get('assets'):
        logger.log(f"No assets data found for BMN {bmn}. Adding to 'Not in Mojo' list.", 'warning')
        with execution_lock:
            execution_status['not_in_mojo_bmns'].append(bmn)
            socketio.emit('status_update', execution_status)  # Emit updated status
        return False

    # Check for restricted assets
    if any(a.get('assetState') == 'Restricted' for a in data['assets']):
        logger.log(f"BMN {bmn} contains restricted assets. Adding to 'Restricted BMNs' list.", 'warning')
        with execution_lock:
            execution_status['restricted_bmns'].append(bmn)
            socketio.emit('status_update', execution_status)  # Emit updated status
        return False

    # Filter for 'Current' assets for download
    valid_assets = [a for a in data['assets'] if a.get('assetState') == 'Current']
    if not valid_assets:
        logger.log(f"No 'Current' state assets found for BMN {bmn}. Skipping.", 'warning')
        return False

    grouped_assets = defaultdict(list)
    for asset in valid_assets:
        indicator = asset.get('packageFacingIndicator')
        if indicator:
            grouped_assets[indicator].append(asset)

    # Process for each retailer
    for retailer_name in retailers_to_process:
        config = RETAILER_CONFIGS[retailer_name]
        os.makedirs(config['download_folder'], exist_ok=True)
        save_id = item_data.get(config['save_id_key'])
        if not save_id:
            logger.log(f"Skipping {retailer_name} for BMN {bmn} due to missing Save ID.", 'warning')
            continue

        for asset_label, keyword in config['asset_types'].items():
            assets_for_type = grouped_assets.get(keyword, [])
            if config['download_func'](assets_for_type, asset_label, save_id, config['download_folder']):
                logger.log(f"✅ Processed {retailer_name} for BMN {bmn}.")
    return True

def run_download_task(search_data, retailer):
    """Runs the download task using ThreadPoolExecutor."""
    def run():
        # Determine the list of retailers to process
        retailers_to_process = list(RETAILER_CONFIGS.keys()) if retailer == 'Both' else [retailer]

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(process_bmn, item, retailers_to_process): item for item in search_data}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.log(f"Error processing BMN {item['bmn']}: {e}", 'error')

                with execution_lock:  # Update progress safely
                    execution_status['completed_items'] += 1
                    progress = int((execution_status['completed_items'] / execution_status['total_items']) * 100)
                    execution_status['progress'] = progress
                    remaining_items = execution_status['total_items'] - execution_status['completed_items']
                    
                    # Emit progress update
                    socketio.emit('progress', {
                        'progress': progress,
                        'task': execution_status['current_task'],
                        'completed': execution_status['completed_items'],
                        'total': execution_status['total_items'],
                        'remaining': remaining_items
                    })

        with execution_lock:
            execution_status['running'] = False
        socketio.emit('execution_complete')
        socketio.emit('status_update', execution_status)  # Emit final status

    # Start the thread
    execution_status['running'] = True
    threading.Thread(target=run, daemon=True).start()

# --- File Parsing (MODIFIED) ---
def parse_search_ids_from_file(file_path, retailer):
    try:
        # Load and clean headers
        df = pd.read_excel(file_path, dtype=str) if file_path.endswith(('xlsx', 'xls')) else pd.read_csv(file_path, dtype=str)
        df.columns = [str(c).strip() for c in df.columns]
        # Standardize common column names
        df.rename(columns={'ArticleID': 'Article ID'}, inplace=True, errors='ignore')

        # Determine required columns
        required_cols = ['BMN']
        if retailer == "Sobeys": required_cols.append('Article ID')
        elif retailer == "Instacart": required_cols.append('GTIN')
        elif retailer == "Both": required_cols.extend(['Article ID', 'GTIN'])
        
        if not all(col in df.columns for col in required_cols):
            missing_cols = [col for col in required_cols if col not in df.columns]
            logger.log(f"Input file is missing one or more required columns: {missing_cols}", 'error')
            return [], {}

        # --- Duplicate Detection ---
        duplicates = {}
        if 'BMN' in df.columns:
            bmn_counts = df['BMN'].dropna().value_counts()
            duplicates['duplicate_bmns'] = bmn_counts[bmn_counts > 1].index.tolist()
        if 'Article ID' in df.columns:
            article_id_counts = df['Article ID'].dropna().value_counts()
            duplicates['duplicate_article_ids'] = article_id_counts[article_id_counts > 1].index.tolist()
        if 'GTIN' in df.columns:
            gtin_counts = df['GTIN'].dropna().value_counts()
            duplicates['duplicate_gtins'] = gtin_counts[gtin_counts > 1].index.tolist()

        # --- Data Extraction (ensuring unique BMNs for processing) ---
        search_data, seen_bmns = [], set()
        for _, row in df.iterrows():
            bmn = str(row.get('BMN', '')).strip()
            if not bmn or bmn == 'nan' or bmn in seen_bmns:
                continue

            item = {'bmn': bmn}
            if 'Article ID' in required_cols:
                item['article_id'] = str(row.get('Article ID', '')).strip()
            if 'GTIN' in required_cols:
                item['gtin'] = str(row.get('GTIN', '')).strip()

            search_data.append(item)
            seen_bmns.add(bmn)

        logger.log(f"Parsed {len(search_data)} unique BMN entries from file.")
        if any(duplicates.values()):
            logger.log(f"Found duplicate entries in the file.", 'info')

        return search_data, duplicates
    except Exception as e:
        logger.log(f"Error parsing file: {str(e)}", 'error')
        return [], {}

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/execute', methods=['POST'])
def execute_download():
    if execution_status['running']:
        return jsonify({'error': 'Another task is already running'}), 400

    retailer = request.form.get('retailer')
    search_data = []
    duplicates = {}

    if 'file' in request.files and request.files['file'].filename != '':
        file = request.files['file']
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type.'}), 400
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
        file.save(file_path)
        search_data, duplicates = parse_search_ids_from_file(file_path, retailer)
        os.remove(file_path)
    else:
        bmn = request.form.get('bmn')
        article_id = request.form.get('article_id')
        gtin = request.form.get('gtin')
        if not bmn:
            return jsonify({'error': 'BMN is required for single entry.'}), 400
        search_data = [{'bmn': bmn, 'article_id': article_id, 'gtin': gtin}]

    if not search_data:
        return jsonify({'error': 'No valid search data found or parsed.'}), 400

    execution_status.update({
        'running': True, 'logs': [], 'progress': 0, 'current_task': 'Starting...',
        'total_items': len(search_data), 'completed_items': 0, 'current_search_id': '',
        'not_in_mojo_bmns': [],
        'restricted_bmns': []
    })

    run_download_task(search_data, retailer)
    return jsonify({
        'success': True,
        'message': f'Download started for {len(search_data)} item(s).',
        'total_items': len(search_data),
        'duplicates': duplicates
    })


@app.route('/stop', methods=['POST'])
def stop_execution():
    with execution_lock:
        execution_status['running'] = False
    return jsonify({'success': True, 'message': 'Stop signal sent.'})

@app.route('/status')
def get_status():
    with execution_lock:
        return jsonify(execution_status)

@socketio.on('connect')
def handle_connect():
    emit('status_update', execution_status)

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)