@echo off
echo Starting Apache...
sc start Apache2.4

echo Starting Flask...
cd C:\Users\Administrator\Desktop\construction-einvoice\backend
python app.py