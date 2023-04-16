import io
import time
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

url = "https://loterias.caixa.gov.br/Paginas/Lotofacil.aspx"
driver = webdriver.Chrome()

driver.get(url)

# Espera até que o link com o texto desejado esteja visível na página
link = WebDriverWait(driver, 20).until(EC.visibility_of_element_located((By.LINK_TEXT, "Resultados da Lotofácil por ordem crescente.")))

# Clica no link
link.click()

# Espera 30 segundos para o download do conteúdo da página
time.sleep(30)

# Salva o conteúdo da página em um objeto de memória
page_content = driver.page_source

# Define o caminho do arquivo de saída
file_path = 'lf.html'

# Grava o conteúdo da página em um arquivo com nome e extensão padrão
try:
    with io.open(file_path, 'w', encoding='utf-8') as f:
        f.write(page_content)
    if os.path.isfile(file_path):
        full_path = os.path.abspath(file_path)
        print("Arquivo gravado com sucesso em:", full_path)
    else:
        print("Erro ao gravar o arquivo:", file_path, "não existe")
except Exception as e:
    print("Erro ao gravar o arquivo:", e)

driver.quit()
