import dns.resolver
import time
from collections import defaultdict
from ping3 import ping, verbose_ping
import os

def limpar_tela():
    """Limpa a tela no terminal."""
    os.system('cls' if os.name == 'nt' else 'clear')

# Use a função para limpar a tela
limpar_tela()

# Lista dos servidores DNS a serem testados
dns_servers = ["8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1", "208.67.222.222", "208.67.220.220", 
               "216.146.35.35", "216.146.36.36", "8.26.56.26", "8.20.247.20", "156.154.70.22", 
               "156.154.71.22", "9.9.9.9", "9.9.9.10"]

# Domínios a serem consultados
domains = ["www.google.com", "www.amazon.com", "www.facebook.com", "www.instagram.com", 
           "www.linkedin.com", "www.microsoft.com", "www.reddit.com", "www.twitter.com", 
           "www.netflix.com", "www.apple.com"]

# Dicionários para armazenar os tempos de resposta e contagens para cada servidor
dns_times = defaultdict(float)
ping_times = defaultdict(float)
counts = defaultdict(int)

# Valor alto para o tempo de ping quando o servidor não responde
unreachable_ping_time = 10.0 * 1000
unresolvable_dns_time = 10.0 * 1000

for server in dns_servers:
    resolver = dns.resolver.Resolver()
    resolver.nameservers = [server]
    
    for domain in domains:
        try:
            # Calcula o tempo de resolução de DNS
            start_time = time.time()
            resolver.resolve(domain)
            end_time = time.time()
            dns_time = (end_time - start_time) * 1000  # converte para milissegundos
            dns_times[server] += dns_time
        except Exception as e:
            print(f"O servidor DNS {server} falhou ao consultar {domain}. Erro: {e}")
            dns_times[server] += unresolvable_dns_time
            
        # Calcula o tempo de ping
        try:
            ping_time = ping(server)
            if ping_time is not None:
                ping_times[server] += ping_time * 1000  # converte para milissegundos
            else:
                print(f"Não foi possível pingar o servidor DNS {server}")
                ping_times[server] += unreachable_ping_time  # adiciona o tempo de "punição"
        except Exception as e:
            print(f"O servidor DNS {server} falhou ao fazer ping. Erro: {e}")
            
        counts[server] += 1

# Calcula e imprime as médias
averages = [(server, dns_times[server] / counts[server], ping_times[server] / counts[server]) 
            for server in dns_servers]
averages.sort(key=lambda x: (x[1] + x[2]) / 2)  # Ordena pela média dos tempos de DNS e ping

averages_dns = [(server, dns_times[server] / counts[server]) for server in dns_servers]
averages_dns.sort(key=lambda x: x[1])  # Ordena pela média dos tempos de DNS

averages_ping = [(server, ping_times[server] / counts[server]) for server in dns_servers]
averages_ping.sort(key=lambda x: x[1])  # Ordena pela média dos tempos de ping

limpar_tela()
print("\nTempo médio de resolução de nomes (ms):")
for server, avg_dns_time in averages_dns:
    print(f"{server} - {avg_dns_time:.4f} ms")

print("\n")
print(",".join(server for server, _ in averages_dns))

print("\nTempo médio de ping (ms):")
for server, avg_ping_time in averages_ping:
    print(f"{server} - {avg_ping_time:.4f} ms")
    
print("\n")
print(",".join(server for server, _ in averages_ping))

print("\nMédia geral (resolução de nomes + ping) (ms):")
for server, avg_dns_time, avg_ping_time in averages:
    print(f"{server} - {(avg_dns_time + avg_ping_time) / 2:.4f} ms")

# Imprime a lista de DNS ordenada por melhor tempo
print("\nLista de servidores DNS ordenada por melhor tempo:")
print(",".join(server for server, _, _ in averages))
