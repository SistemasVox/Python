import os
import platform
import schedule
import time
import subprocess
from datetime import datetime, timedelta

def clear_screen():
    os.system('cls' if platform.system().lower() == 'windows' else 'clear')

def is_scheduled_time():
    now = datetime.now()
    return now.weekday() < 6 and now.hour >= 20 and now.hour < 23 and (now.hour != 20 or now.minute >= 30)

def run_script():
    if is_scheduled_time():
        subprocess.run(['python', 'script.py'])

# Agendar a execução do script das 20h30 às 23h de segunda a sábado
schedule.every(1).minutes.do(run_script)

def get_next_scheduled_time():
    now = datetime.now()
    if now.weekday() >= 6 or now.hour >= 23:
        next_day = now + timedelta(days=1)
        return next_day.replace(hour=20, minute=30, second=0, microsecond=0)
    elif now.hour < 20 or (now.hour == 20 and now.minute < 30):
        return now.replace(hour=20, minute=30, second=0, microsecond=0)
    else:
        return now + timedelta(minutes=1)

while True:
    # Executar tarefas agendadas
    schedule.run_pending()

    # Verificar se a tecla "i" foi pressionada
    user_input = input("Pressione 'i' para obter informações ou 's' para sair: ")
    clear_screen()
    
    if user_input == "i":
        if is_scheduled_time():
            print("Executando tarefas agendadas...")
        else:
            print("Não há tarefas agendadas no momento.")

        next_scheduled_time = get_next_scheduled_time()
        time_to_next = int((next_scheduled_time - datetime.now()).total_seconds())
        
        days, seconds = divmod(time_to_next, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        
        # Exibir a mensagem com o horário e o tempo restante
        print(f"Próxima verificação: {next_scheduled_time.strftime('%Y-%m-%d %H:%M:%S')} ({days}d {hours}h {minutes}m {seconds}s)")

    elif user_input == "s":
        print("Saindo do script...")
        break

    time.sleep(1)
