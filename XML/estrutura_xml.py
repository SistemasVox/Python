import tkinter as tk
from tkinter import filedialog
import xml.dom.minidom

# Cria uma janela principal
root = tk.Tk()
root.withdraw()

# Abre uma janela de seleção de arquivo
file_path = filedialog.askopenfilename()

# Faz o parsing do arquivo XML e armazena a raiz do documento
arvore_xml = xml.dom.minidom.parse(file_path)
raiz = arvore_xml.documentElement

# Função recursiva que percorre a árvore do XML e mostra as tags únicas
def mostrar_tags_unicas(elemento, nivel=0, tags_vistas=set()):
    # Obtém o nome da tag do elemento
    tag = elemento.nodeName

    # Se a tag já foi vista antes, não a mostra novamente
    if tag in tags_vistas:
        return

    # Adiciona a tag ao conjunto de tags vistas
    tags_vistas.add(tag)

    # Mostra a tag com o nível de indentação adequado
    print(' ' * nivel, tag)

    # Percorre os filhos do elemento
    for filho in elemento.childNodes:
        # Se o filho for um elemento, chama a função recursivamente
        if filho.nodeType == xml.dom.minidom.Node.ELEMENT_NODE:
            mostrar_tags_unicas(filho, nivel + 2, tags_vistas)

# Mostra as tags únicas do arquivo XML
print('Tags únicas do arquivo XML:')
print('-' * 30)
mostrar_tags_unicas(raiz)
