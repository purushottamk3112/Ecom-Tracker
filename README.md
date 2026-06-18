# Omni-Track (Ecom-Tracker)

Omni-Track is a comprehensive e-commerce product tracker and comparison tool. It allows users to track product availability, prices, and stock statuses across multiple quick-commerce and e-commerce platforms in India, including Blinkit, Zepto, Swiggy Instamart, Amazon, and various Flipkart marketplaces (Main, Grocery, Minutes).

## Features
- **Multi-Platform Scraping**: Extract product details (name, price, stock status) from major platforms.
- **Location-based Tracking**: Support for searching specific pin codes (e.g., Delhi, Mumbai, Bangalore, Pune).
- **Fuzzy Matching & FSN Support**: Search products by name (with intelligent fuzzy matching) or by precise FSN/ASIN codes.
- **Modern UI**: A responsive, React-based frontend built with Vite and TailwindCSS for a premium user experience.
- **Robust Backend**: A FastAPI Python backend powered by Playwright for headless browser scraping and DOM traversal.

## Project Structure
- `frontend/`: React application built using Vite + TailwindCSS.
- `backend/`: FastAPI backend and Playwright scraping engine (`api.py`).

## Tech Stack
- **Frontend**: React, Vite, TailwindCSS
- **Backend**: Python 3, FastAPI, Playwright, Uvicorn
- **Utilities**: thefuzz (for string matching)

## Setup & Installation

### Backend
1. Navigate to the `omni-track/backend/` directory.
2. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Install Playwright browsers:
   ```bash
   playwright install
   ```
5. Run the API server:
   ```bash
   python api.py
   ```

### Frontend
1. Navigate to the `omni-track/frontend/` directory.
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start the development server:
   ```bash
   npm run dev
   ```

## License
MIT
