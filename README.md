# **Smart Asset Mover**

## **Overview**
The **SAM** is a web-based application designed to streamline the process of downloading digital assets for retailers like **Sobeys** and **Instacart**. The application supports batch downloads via file uploads, real-time progress tracking, and logging. It is built using **Flask**, **Socket.IO**, and **Python**, with a responsive frontend for seamless user interaction.

---

## **Features**
- **Retailer Support**:
  - Download assets for **Sobeys** and **Instacart**.
  - Support for both single and batch downloads.
  - Handles retailer-specific naming conventions and folder organization.

- **Batch Processing**:
  - Upload `.txt`, `.csv`, `.xlsx`, or `.xls` files for batch downloads.
  - Detects duplicate entries in uploaded files.

- **Real-Time Updates**:
  - Displays real-time progress, logs, and task status.
  - Updates the frontend dynamically using **Socket.IO**.

- **Error Handling**:
  - Identifies BMNs not found in Mojo.
  - Flags BMNs with restricted assets.

- **User-Friendly Interface**:
  - Clean and responsive design.
  - Start and stop functionality for download tasks.

---

## **Technologies Used**
- **Backend**:
  - Flask
  - Flask-SocketIO
  - Python (Threading, AsyncIO, Requests)
  - Pandas (for file parsing)

- **Frontend**:
  - HTML5, CSS3, JavaScript
  - Socket.IO (Real-time communication)
  - Font Awesome (Icons)

- **Deployment**:
  - Compatible with local development and production environments.
  - Can be hosted using WSGI servers like Gunicorn or Docker.

---

## **Installation**

### **Prerequisites**
- Python 3.9 or higher
- Pip (Python package manager)

### **Steps**
1. **Clone the Repository**:
   ```bash
   git clone https://github.com/your-username/general-mills-digital-assets-downloader.git
   cd general-mills-digital-assets-downloader
   ```

2. **Create a Virtual Environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the Application**:
   ```bash
   python App.py
   ```

5. **Access the Application**:
   - Open your browser and navigate to: `http://127.0.0.1:5000`

---

## **Usage**

### **Single Entry Download**
1. Select a retailer (Sobeys, Instacart, or Both).
2. Enter the required fields:
   - **Sobeys**: BMN and Article ID.
   - **Instacart**: BMN and GTIN.
   - **Both**: BMN, Article ID, and GTIN.
3. Click **Start** to begin the download.

### **Batch Download**
1. Upload a `.txt`, `.csv`, `.xlsx`, or `.xls` file containing BMNs and other required fields.
2. Click **Start** to process the file.

### **Stopping the Process**
- Click the **Stop** button to halt the ongoing process.

### **Viewing Logs**
- Logs are displayed in real-time in the **Execution Log** section.

### **Handling Errors**
- BMNs not found in Mojo are listed under **Image not found in MOJO**.
- BMNs with restricted assets are listed under **Restricted Images**.

---

## **Folder Structure**
```
general-mills-digital-assets-downloader/
│
├── App.py                     # Main application file
├── templates/
│   └── index.html             # Frontend HTML template
├── static/
│   ├── css/                   # CSS files
│   ├── js/                    # JavaScript files
│   └── img/                   # Images (logos, etc.)
├── uploads/                   # Temporary folder for uploaded files
├── requirements.txt           # Python dependencies
└── README.md                  # Project documentation
```

---

## **Configuration**

### **Retailer Configurations**
Retailer-specific configurations are defined in the `RETAILER_CONFIGS` dictionary in `App.py`. This includes:
- Download folder paths.
- Asset types and naming conventions.
- Language-specific logic for asset selection.

### **API Endpoint**
The application uses the following API endpoint to fetch asset data:
```
https://picassetimporter.k8s.genmills.com/api/v1/assets/version/{bmn}/json
```

---

## **Error Handling**

### **Common Errors**
1. **BMN Not Found**:
   - BMNs not found in Mojo are added to the **Image not found in MOJO** list.

2. **Restricted Assets**:
   - BMNs with restricted assets are added to the **Restricted Images** list.

3. **File Parsing Errors**:
   - Missing or invalid columns in the uploaded file are logged as errors.

4. **Network Issues**:
   - Network errors during API calls are logged and do not halt the process.

---

## **Deployment**

### **Local Deployment**
1. Run the application using Flask's development server:
   ```bash
   python App.py
   ```

2. Access the application at `http://127.0.0.1:5000`.


