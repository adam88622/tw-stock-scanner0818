"""Stable server launcher - no debug, no reloader"""
import sys, os

# Use hardcoded path to avoid __file__ issues with different launch methods
PROJECT_DIR = r'D:\claude\tw-stock-scanner'
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

from app import app
app.run(debug=False, host='127.0.0.1', port=5000, use_reloader=False)
