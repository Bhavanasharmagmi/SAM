# Technical Documentation for SAM

## Overview
The SAM is a Flask-based web application designed to facilitate the downloading of assets for various retailers. It provides a user-friendly interface for uploading files, selecting retailers, and monitoring the download progress in real-time. The application uses Socket.IO for real-time communication and supports multiple file formats for input.

---

## Execution Flow

### Backend Execution Flow
1. **File Upload**: Users upload a file containing BMNs (Business Material Numbers).
2. **File Parsing**: The backend parses the uploaded file to extract BMNs.
3. **API Calls**: For each BMN, the application makes API calls to fetch asset data.
4. **Retailer-Specific Processing**: Assets are processed and downloaded based on retailer-specific configurations.
5. **Real-Time Updates**: Progress and logs are sent to the frontend via WebSocket.
6. **Completion**: A summary of the process, including failed and restricted BMNs, is displayed.

### Frontend Execution Flow
1. **User Interaction**: Users interact with the interface to upload files and start/stop the process.
2. **Real-Time Feedback**: The frontend receives updates from the backend and displays progress, logs, and status indicators.
3. **Error Handling**: Errors are displayed in the logs section for user awareness.

---

## Frontend Features

### Key Components
1. **File Upload Area**: Allows users to upload files containing BMNs.
2. **Retailer Selection**: Users can select one or multiple retailers for processing.
3. **Progress Bar**: Displays the progress of the download task.
4. **Logs Section**: Shows real-time logs of the process.
5. **Status Indicators**: Indicates the current status (Idle, Running, Error).
6. **Failed BMNs List**: Displays BMNs that were not found or restricted.

### Technologies Used
- HTML5, CSS3, JavaScript
- Socket.IO for real-time communication
- Font Awesome for icons
- Google Fonts for typography

---

## Backend Functions

### Key Functions
1. **`allowed_file(filename)`**: Validates the uploaded file type.
2. **`parse_search_ids_from_file(file_path, retailer)`**: Parses BMNs from the uploaded file.
3. **`process_bmn(item_data, retailers_to_process)`**: Processes a single BMN for the selected retailers.
4. **`_select_and_download_sobeys`**: Handles Sobeys-specific asset processing and downloading.
5. **`_select_and_download_instacart`**: Handles Instacart-specific asset processing and downloading.
6. **`save_image_from_url(url, file_path)`**: Downloads and saves an image from a given URL.
7. **`run_download_task(search_data, retailer)`**: Manages the execution of the download task using threading.

### WebSocket Communication
- **`WebSocketLogger`**: Logs messages and emits them to the frontend.
- **`socketio.emit`**: Sends real-time updates to the frontend.

---

## File Upload and Parsing
- **Supported Formats**: `.txt`, `.csv`, `.xlsx`, `.xls`
- **Parsing Logic**: Extracts BMNs and validates them before processing.

---

## Retailer-Specific Logic

### Sobeys
- **Download Folder**: `C:/Downloads/Sobeys`
- **Asset Types**: Mobile Hero, Front - 3D, Ingredients, Nutrition
- **Filename Convention**: Based on `article_id`, `lang_code`, and `asset_type`.

### Instacart
- **Download Folder**: `C:/Downloads/Instacart`
- **Asset Types**: Mobile Hero, Left Front - 3D, Right Front - 3D, Ingredients, Nutrition
- **Filename Convention**: Based on `gtin` and `asset_type`.

---

## Error Handling
- **Logging**: Errors are logged and displayed in the frontend logs section.
- **Retries**: Implements retries for failed downloads.
- **Restricted Assets**: Identifies and skips restricted assets.

---

## Deployment Instructions

### Step 1: Install Dependencies
1. Ensure Python is installed on your system (version 3.7 or higher is recommended).
2. Open a terminal or command prompt.
3. Navigate to the project directory where the `requirements.txt` file is located.
4. Run the following command to install all required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Step 2: Start the Application
1. After installing the dependencies, ensure you are still in the project directory.
2. Run the following command to start the Flask application:
   ```bash
   python App.py
   ```
3. The application will start, and you should see output indicating that the server is running (e.g., `Running on http://127.0.0.1:5000/`).

### Step 3: Access the Application
1. Open a web browser of your choice.
2. Enter the following URL in the address bar:
   ```
   http://localhost:5000
   ```
3. The application interface will load, allowing you to upload files, select retailers, and start the download process.

### Notes
- If you encounter any issues, ensure all dependencies are installed correctly and that no other application is using port 5000.
- For deployment on a production server, consider using a WSGI server like Gunicorn or deploying via Docker.

---

## Future Enhancements
1. **Add More Retailers**: Extend support for additional retailers.
2. **Improve Error Handling**: Implement more robust error handling mechanisms.
3. **Optimize Performance**: Use asynchronous processing for better scalability.
4. **Enhance UI**: Add more interactive elements and improve responsiveness.

---

## File Structure

The project is organized as follows:

```
Canada API-Based-V3/
├── App.py                # Main Flask application file
├── Single-exe.py         # Alternate execution script
├── README.md             # Project overview and instructions
├── requirements.txt      # Python dependencies
├── document.txt          # Placeholder or additional documentation
├── Insta-test.xlsx       # Sample input file for testing
├── Test-all.xlsx         # Another sample input file
├── test.xlsx             # Additional sample input file
├── static/               # Static assets (e.g., images, CSS, JS)
│   ├── img/              # Image assets
│   │   ├── betty_crocker_logo.png
│   │   ├── gm_logo.png
│   │   ├── gm_logo1.png
│   │   ├── haagen-dazs_logo.png
│   │   ├── nature_valley_logo.png
│   │   └── pillsbury_logo.png
├── templates/            # HTML templates for the Flask app
│   └── index.html        # Main HTML template
├── uploads/              # Directory for uploaded files
```

### Key Directories and Files
- **`App.py`**: Contains the main logic for the Flask application.
- **`Single-exe.py`**: Provides an alternate execution flow.
- **`static/`**: Stores static assets like images and stylesheets.
- **`templates/`**: Contains HTML templates for rendering the frontend.
- **`uploads/`**: Temporary storage for user-uploaded files.

This structure ensures a clear separation of concerns, making the project easy to navigate and maintain.

---

This document serves as a comprehensive guide for developers and users to understand the application's functionality and architecture.
