# 👁️ Eye Health AI - Retinal Disease Screening

A deep learning-powered web application for screening diabetic retinopathy and eye disorders from retinal fundus images.

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.0+-green.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

## 🌟 Features

- **AI-Powered Analysis**: Deep learning model for retinal image classification
- **Disease-Specific Insights**: Personalized recommendations based on detected condition
- **Interactive Dashboard**: Modern glassmorphism UI with tabbed interface
- **Comprehensive Guidance**: Daily habits, prevention tips, and medical recommendations
- **Downloadable Reports**: Export analysis results as text reports
- **Responsive Design**: Works on desktop and mobile devices

## 📋 Project Structure

```
deep learning project/
├── app.py                  # Flask backend server
├── requirements.txt        # Python dependencies
├── README.md              # This file
├── .gitignore             # Git ignore rules
├── templates/
│   └── index.html         # Main HTML template
├── static/
│   ├── style.css          # CSS styles
│   └── script.js          # Frontend JavaScript
├── uploads/               # Temporary image storage
└── venv/                  # Virtual environment
```

## 🚀 Quick Start

### 1. Clone the Repository
```bash
git clone <your-repo-url>
cd "deep learning project"
```

### 2. Create Virtual Environment
```bash
python -m venv venv
```

### 3. Activate Virtual Environment
**Windows:**
```bash
venv\Scripts\activate
```
**macOS/Linux:**
```bash
source venv/bin/activate
```

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

### 5. Run the Application
```bash
python app.py
```

### 6. Open in Browser
Navigate to: `http://localhost:5000`

## 📦 Dependencies

```txt
flask>=2.0.0
flask-cors>=3.0.0
requests>=2.25.0
werkzeug>=2.0.0
```

## 🔧 Configuration

The application can be configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `HUGGINGFACE_API_TOKEN` | (set in code) | API token for Hugging Face |
| `MODEL_NAME` | `google/vit-base-patch16-224` | Model to use for inference |
| `SECRET_KEY` | (auto-generated) | Flask secret key |
| `LOG_LEVEL` | `INFO` | Logging level |

## 🏗️ Architecture

### Backend (Flask)
- **app.py**: Main Flask application
  - `/` - Serves the main HTML page
  - `/predict` - POST endpoint for image analysis
  - `/health` - Health check endpoint
  - `/config` - Configuration endpoint

### Frontend
- **index.html**: Semantic HTML5 structure
- **style.css**: Modern CSS with CSS variables, glassmorphism effects
- **script.js**: Vanilla JavaScript with disease knowledge base

## 🩺 Disease Classifications

The application is designed to classify diabetic retinopathy into 5 severity levels:

| Level | Classification | Description |
|-------|---------------|-------------|
| 0 | No_DR | No diabetic retinopathy detected |
| 1 | Mild | Mild nonproliferative DR |
| 2 | Moderate | Moderate nonproliferative DR |
| 3 | Severe | Severe nonproliferative DR |
| 4 | Proliferative_DR | Proliferative diabetic retinopathy |

## 🎨 UI Features

### Disease-Specific Cards
Each detected condition displays:
- **Recommendations Tab**: Medical recommendations and next steps
- **Daily Habits Tab**: Lifestyle modifications for managing the condition
- **Prevention Tab**: Tips to prevent progression

### Interactive Elements
- Expandable/collapsible disease cards
- Tabbed interface for organized information
- Confidence meters with visual indicators
- Animated loading states
- Downloadable analysis reports

## 📱 API Endpoints

### POST /predict
Upload an image for analysis.

**Request:**
```
Content-Type: multipart/form-data
Field: image (file)
```

**Response:**
```json
{
  "success": true,
  "predictions": [
    {
      "label": "No_DR",
      "confidence": 95.5,
      "health_status": "healthy",
      "recommendation": "Continue regular eye exams..."
    }
  ],
  "model_used": "model-name",
  "timestamp": "2025-12-13T22:00:00"
}
```

### GET /health
Check server status.

**Response:**
```json
{
  "status": "healthy",
  "api_configured": true,
  "model": "model-name"
}
```

## 🔒 Security Features

- Secure filename handling with `werkzeug.secure_filename`
- File type validation (PNG, JPG, JPEG, BMP, TIFF, WEBP)
- Maximum file size limit (16MB)
- CORS configuration for cross-origin requests
- Temporary file cleanup after processing

## 🌐 Supported Image Formats

- PNG
- JPG / JPEG
- BMP
- TIFF
- WEBP

Maximum file size: **16 MB**

## 📊 Model Information

### Current Model
- **Name**: `google/vit-base-patch16-224`
- **Type**: Vision Transformer (ViT)
- **Note**: This is a general image classifier. For production use, a specialized diabetic retinopathy model is recommended.

### Recommended Models for Production
- `jdelgado2002/diabetic_retinopathy_detection` (ResNet-50, 83% accuracy)
- MobileNetV3 variants (~98% accuracy, ~20MB size)
- EfficientNetB0 (~97% accuracy, ~20MB size)

## 🛠️ Development

### Running in Debug Mode
```bash
python app.py
```
Debug mode is enabled by default in development.

### Logging
Logs are written to:
- Console (stdout)
- `app.log` file

## ⚠️ Medical Disclaimer

**This AI screening tool is for informational and educational purposes only.**

- Results should NOT be used as a substitute for professional medical diagnosis
- Always consult a qualified ophthalmologist for proper eye examination
- This tool is designed to assist, not replace, medical professionals
- Do not make medical decisions based solely on this screening

## 📄 License

This project is for educational purposes.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📞 Support

For issues and questions, please open a GitHub issue.

---

**Built with ❤️ using Flask, Hugging Face, and Modern Web Technologies**
