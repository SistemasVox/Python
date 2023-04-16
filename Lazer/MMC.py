def gcd(a, b):
    # retorna o MDC de a e b usando o algoritmo de Euclides
    while b:
        a, b = b, a % b
    return a

def lcm(a, b):
    # retorna o MMC de a e b usando a fórmula acima
    return (a * b) // gcd(a, b)

def multiple_lcm(numbers):
    # retorna o MMC de uma lista de números
    result = numbers[0]
    for i in range(1, len(numbers)):
        result = lcm(result, numbers[i])
    return result

# exemplo de uso
numbers = [10, 20, 30, 40]
print(multiple_lcm(numbers))  # output: 60
