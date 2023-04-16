# Classe Pessoa
class Pessoa:
  def __init__(self, nome, idade):
    self.nome = nome
    self.idade = idade

  def __str__(self):
    return f"Nome: {self.nome}, Idade: {self.idade}"

# Exemplo de variáveis
num_inteiro = 10
num_float = 3.14
texto = "Olá, mundo!"
booleano = True
lista = [1, 2, 3]
tupla = (4, 5, 6)
dicionario = {"chave1": "valor1", "chave2": "valor2"}

# Estrutura de decisão if-else
if num_inteiro > 5:
  print("O número inteiro é maior que 5.")
else:
  print("O número inteiro é menor ou igual a 5.")

# Estrutura de repetição for
for i in range(1, 6):
  print(i)

# Estrutura de repetição while
x = 0
while x < 5:
  print(x)
  x += 1

# Instanciando objetos da classe Pessoa
pessoa1 = Pessoa("João", 25)
pessoa2 = Pessoa("Maria", 30)

# Imprimindo objetos da classe Pessoa
print(pessoa1)
print(pessoa2)
