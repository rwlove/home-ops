#!/bin/bash

echo "Connect to frameo:5555"
adb connect frameo:5555

echo "Start Fully Kiosk"
adb shell am start -n de.ozerov.fully/.MainActivity
