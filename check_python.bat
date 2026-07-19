@echo off
echo Current directory: %cd%
echo Python location: 
where python
echo Python version:
python --version
python -c "import sounddevice; print('sounddevice:', sounddevice.__version__)" 2>&1
pause
