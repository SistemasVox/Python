# Criando uma lista
minha_lista = [1, 2, 3, 4, 5]

# Adicionando um elemento ao final da lista
minha_lista.append(6)
print(minha_lista)  # [1, 2, 3, 4, 5, 6]

# Inserindo um elemento em uma posição específica
minha_lista.insert(2, 7)
print(minha_lista)  # [1, 2, 7, 3, 4, 5, 6]

# Removendo um elemento da lista
minha_lista.remove(4)
print(minha_lista)  # [1, 2, 7, 3, 5, 6]

# Removendo e retornando o último elemento da lista
ultimo_elemento = minha_lista.pop()
print(minha_lista)  # [1, 2, 7, 3, 5]
print(ultimo_elemento)  # 6

# Obtendo o índice de um elemento na lista
indice = minha_lista.index(7)
print(indice)  # 2

# Contando o número de ocorrências de um elemento na lista
num_ocorrencias = minha_lista.count(3)
print(num_ocorrencias)  # 1

# Ordenando a lista
minha_lista.sort()
print(minha_lista)  # [1, 2, 3, 5, 7]

# Invertendo a ordem da lista
minha_lista.reverse()
print(minha_lista)  # [7, 5, 3, 2, 1]

# Embaralhando aleatoriamente os elementos da lista
random.shuffle(minha_lista)
print(minha_lista)  # [4, 2, 5, 1, 3]