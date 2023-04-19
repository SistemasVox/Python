import json
import os
import platform
import time
import requests
import random
from datetime import timedelta
import datetime

# Importações adicionais
from colorama import init, Fore, Back, Style

# Inicialização da biblioteca colorama
init(autoreset=True)


def fetch_data():
    random_x = random.randint(10000, 99999)
    url = f"http://192.168.1.1/api/model.json?internalapi=1&x={random_x}"

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


def format_duration(duration):
    return str(timedelta(seconds=duration))


def format_temperature(temp):
    return f"{temp}°"


def format_voltage(voltage):
    return f"{voltage / 1000:.2f}mV"


def format_battery(level):
    return f"{level}%"


def format_bytes(value):
    value = float(value)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if value < 1024.0:
            break
        value /= 1024.0
    return f"{value:.2f} {unit}"


def format_datetime(timestamp):
    dt = datetime.datetime.fromtimestamp(timestamp)
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


def calculate_speeds(curr_time, prev_time, data, prev_data):
    time_diff = curr_time - prev_time
    curr_data_transferred = float(data.get("wwan", {}).get("dataTransferred", 0))
    prev_data_transferred = float(prev_data.get("wwan", {}).get("dataTransferred", 0))
    curr_data_transferred_rx = float(data.get("wwan", {}).get("dataTransferredRx", 0))
    prev_data_transferred_rx = float(prev_data.get("wwan", {}).get("dataTransferredRx", 0))
    curr_data_transferred_tx = float(data.get("wwan", {}).get("dataTransferredTx", 0))
    prev_data_transferred_tx = float(prev_data.get("wwan", {}).get("dataTransferredTx", 0))

    total_speed = (curr_data_transferred - prev_data_transferred) / time_diff
    download_speed = (curr_data_transferred_rx - prev_data_transferred_rx) / time_diff
    upload_speed = (curr_data_transferred_tx - prev_data_transferred_tx) / time_diff

    return total_speed, download_speed, upload_speed


def print_speeds(total_speed, download_speed, upload_speed):
    print("---- Velocidade Conexão ----")
    print(f"Total: {format_bytes(total_speed)}/s.")
    print(f"Download: {format_bytes(download_speed)}/s.")
    print(f"Upload: {format_bytes(upload_speed)}/s.")
    print("----------------------------")


def print_data(data, prev_data, curr_time, prev_time):
    clear_screen()

    print("---- Dispositivo -----------")
    print(f"Nome: {data['general']['deviceName']}.")
    print(f"Hora: {format_datetime(data['general']['currTime'])}.")
    print(f"Temperatura: {format_temperature(data['general']['devTemperature'])}.")
    print(f"Temperatura MAX: {format_temperature(data['general']['verMajor'])}.")
    print("----------------------------")

    print("---- Bateria ---------------")
    print(f"Status: {data.get('power', {}).get('PMState', 'N/A')}")
    print(f"Temperatura: {format_temperature(data.get('power', {}).get('batteryTemperature'))}.")
    print(f"Voltagem: {format_voltage(data.get('power', {}).get('batteryVoltage'))}.")
    print(f"Carga: {format_battery(data.get('power', {}).get('battChargeLevel'))}.")
    print(f"Fonte: {data.get('power', {}).get('battChargeSource', 'N/A')}.")
    print(f"Estado: {data.get('power', {}).get('batteryState', 'N/A')}.")
    print("----------------------------")
    print("---- Operadora -------------")
    print(f"Conexão: {data['wwan']['connectionText']}.")
    print(f"Tipo de Conexão: {data['wwan']['currentPSserviceType']}.")
    print(f"Banda: {data['wwanadv']['curBand']}")
    print(f"Barras: {data.get('wwan', {}).get('signalStrength', {}).get('bars')}.")
    print(f"RSRP: {data.get('wwan', {}).get('signalStrength', {}).get('rsrp')} dBm.")
    print(f"RSRQ: {data.get('wwan', {}).get('signalStrength', {}).get('rsrq')} dB.")
    print(f"SINR: {data.get('wwan', {}).get('signalStrength', {}).get('sinr')} dB.")
    print(f"Operadora: {data.get('wwan', {}).get('registerNetworkDisplay')}.")
    print(f"Torre: {data.get('wwanadv', {}).get('cellId')}.")
    print(f"radioQuality: {data.get('wwanadv', {}).get('radioQuality')}dBm.")
    print("----------------------------")

    print("---- Conexão ---------------")
    print(f"IP: {data.get('wwan', {}).get('IP')}.")
    print(f"Duração: {format_duration(data['wwan'].get('sessDuration'))}.")
    print(f"Início: {format_datetime(data['wwan'].get('sessStartTime'))}.")
    print(f"Dados total: {format_bytes(data['wwan'].get('dataTransferred'))}.")
    print(f"Download total: {format_bytes(data['wwan'].get('dataTransferredRx'))}.")
    print(f"Upload total: {format_bytes(data['wwan'].get('dataTransferredTx'))}.")
    print("----------------------------")

    if prev_data and curr_time and prev_time:
        total_speed, download_speed, upload_speed = calculate_speeds(curr_time, prev_time, data, prev_data)
        print_speeds(total_speed, download_speed, upload_speed)



prev_data = None
prev_time = None

while True:
    data = fetch_data()
    if data:
        curr_time = data['general']['currTime']
        print_data(data, prev_data, curr_time, prev_time)

        if curr_time:
            prev_time = curr_time
        prev_data = data.copy()

        time.sleep(5)
