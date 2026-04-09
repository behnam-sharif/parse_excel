#!/bin/bash
# Setup script for xlgen_v3 virtual environment

echo "Creating virtual environment..."
py -3 -m venv venv

echo "Activating virtual environment..."
source venv/bin/activate

echo "Upgrading pip..."
pip install --upgrade pip

echo "Installing requirements..."
pip install -r requirements.txt

echo ""
echo "Setup complete!"
echo ""
echo "To activate the virtual environment in future sessions, run:"
echo "  source venv/bin/activate"
echo ""
echo "To run the Streamlit orchestrator:"
echo "  py -3 -m streamlit run apps/orchestrator/app.py"
