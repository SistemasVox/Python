import json
import os
import platform
import time
import requests
import random
from datetime import timedelta
import datetime 

def fetch_data():
    random_x = random.randint(10000, 99999)
    url = f"http://192.168.1.1/api/model.json?internalapi=1&x={random_x}"

    username = "admin"
    password = "q1w2"

    response = retry_get(url, backoff_factor=2)

    if response and response.status_code == 200:
        data = json.loads(response.text)
        return data
    else:
        print(f"Erro ao buscar dados: {response.status_code if response else 'Desconhecido'}")
        return None

def retry_get(url, max_retries=3, backoff_factor=1):
    retries = 0
    session = requests.Session()

    while retries <= max_retries:
        try:
            session.cookies.clear()
            response = session.get(url)
            if response.status_code != 503:
                return response
            else:
                print(f"Tentativa {retries + 1} falhou com o status: {response.status_code}")
        except Exception as e:
            print(f"Tentativa {retries + 1} falhou devido ao erro: {e}")

        wait_time = backoff_factor * (2 ** retries)
        time.sleep(wait_time)
        retries += 1

    return None

def clear_screen():
    if platform.system() == "Windows":
        os.system("cls")
    else:
        os.system("clear")

clear_screen()
keys_to_extract = [
    "SPN", "model", "webAppVersion", "devTemperature", "verMajor", "model", "currTime",
    "deviceName", "PMState", "batteryTemperature", "batteryVoltage", "battChargeLevel",
    "battChargeSource", "batteryState", "IP", "registerNetworkDisplay", "currentPSserviceType",
    "connectionText", "sessDuration", "sessStartTime", "dataTransferred", "dataTransferredRx",
    "dataTransferredTx", "signalStrength", "curBand", "radioQuality", "country", "MCC", "cellId"
]

def format_duration(duration):
    return str(timedelta(seconds=duration))

def format_temperature(temp):
    return f"{temp}°"

def format_voltage(voltage):
    return f"{voltage / 1000:.2f}V"

def format_battery(level):
    return f"{level}%"

def extract_values(data):
    extracted_values = {}
    for key in keys_to_extract:
        try:
            if key == "signalStrength":
                extracted_values[key] = data["wwan"]["signalStrength"]
            elif key == "curBand":
                extracted_values[key] = data["wwan"]["curBand"]
            # ... todos os outros casos
            if key in data["general"]:
                extracted_values[key] = data["general"][key]
            elif key in data["power"]:
                extracted_values[key] = data["power"][key]
            elif key in data["wwan"]:
                extracted_values[key] = data["wwan"][key]
        except KeyError:
            pass

    return extracted_values

def format_bytes(value):
    value = float(value)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if value < 1024.0:
            break
        value /= 1024.0
    return f"{value:.2f} {unit}"

def format_datetime(timestamp):
    dt = datetime.datetime.fromtimestamp(timestamp)
    # return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%H:%M:%S")

def format_signal_strength(signal_strength):
    formatted_strength = []
    for key, value in signal_strength.items():
        if key == "rssi":
            formatted_strength.append(f"RSSI: {value} dBm")
        elif key == "rscp":
            formatted_strength.append(f"RSCP: {value} dBm")
        elif key == "ecio":
            formatted_strength.append(f"Ec/Io: {value} dB")
        elif key == "rsrp":
            formatted_strength.append(f"RSRP: {value} dBm")
        elif key == "rsrq":
            formatted_strength.append(f"RSRQ: {value} dB")
        elif key == "bars":
            formatted_strength.append(f"Barras: {value}")
        elif key == "sinr":
            formatted_strength.append(f"SINR: {value} dB")
    return "\n                ".join(formatted_strength)

while True:
    data = fetch_data()
    if data:
        clear_screen()
        extracted_data = extract_values(data)
        for key, value in extracted_data.items():
            if key in ["devTemperature", "batteryTemperature", "verMajor"]:
                print(f"{key}: {format_temperature(value)}")
            elif key == "batteryVoltage":
                print(f"{key}: {format_voltage(value)}")
            elif key == "battChargeLevel":
                print(f"{key}: {format_battery(value)}")
            elif key in ["sessDuration"]:
                print(f"{key}: {format_duration(value)}")
            elif key in ["sessStartTime", "currTime"]:
                print(f"{key}: {format_datetime(value)}")
            elif key in ["dataTransferred", "dataTransferredRx", "dataTransferredTx"]:
                print(f"{key}: {format_bytes(value)}")
            elif key == "signalStrength":
                formatted_signal_strength = format_signal_strength(value)
                print(f"{key}: {formatted_signal_strength}")
            else:
                print(f"{key}: {value}")
        time.sleep(5)
    else:
        print("reconectando...")