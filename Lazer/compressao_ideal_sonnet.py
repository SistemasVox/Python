import math

def calcular_pressao_cilindro(diametro_mm: float, curso_mm: float, razao_compressao: float) -> float:
    PRESSAO_ATMOSFERICA = 14.7  # PSI ao nível do mar
    fator_eficiencia = 0.90     # Aumente a eficiência volumétrica para 90%
    fator_temperatura = 1.08    # Ajuste o fator de correção para a temperatura do ar
    pressao_base = PRESSAO_ATMOSFERICA * (razao_compressao ** 1.1)
    pressao_compressao = pressao_base * fator_eficiencia * fator_temperatura
    return pressao_compressao

def calcular_volume_cilindro(diametro_mm: float, curso_mm: float) -> float:
    diametro_cm = diametro_mm / 10
    curso_cm = curso_mm / 10
    return math.pi / 4 * diametro_cm ** 2 * curso_cm

def calcular_faixa_pressao(pressao: float, margem: float = 0.1) -> tuple:
    """Calcula a faixa de pressão com uma margem de variação."""
    pressao_min = pressao * (1 - margem)
    pressao_max = pressao * (1 + margem)
    return pressao_min, pressao_max

def obter_dados_usuario():
    print("\n=== Digite os dados do motor ===")
    while True:
        try:
            nome_motor = input("Nome/Modelo do motor: ")
            diametro = float(input("Diâmetro do cilindro (mm): "))
            curso = float(input("Curso do pistão (mm): "))
            razao_compressao = float(input("Razão de compressão (ex: 9.35): "))
            num_cilindros = int(input("Número de cilindros: "))
            return nome_motor, diametro, curso, razao_compressao, num_cilindros
        except ValueError:
            print("Erro nos dados fornecidos. Por favor, tente novamente.")

def main():
    print("=== Calculadora de Pressão de Compressão ===")
    while True:
        nome_motor, diametro, curso, razao_compressao, num_cilindros = obter_dados_usuario()
        volume = calcular_volume_cilindro(diametro, curso)
        pressao = calcular_pressao_cilindro(diametro, curso, razao_compressao)
        pressao_min, pressao_max = calcular_faixa_pressao(pressao)

        print(f"\nMotor: {nome_motor}")
        print(f"Volume unitário: {volume:.2f} cm³")
        print(f"Pressão de compressão esperada: {pressao_min:.1f} - {pressao_max:.1f} PSI")

        for i in range(num_cilindros):
            print(f"Cilindro {i+1}: Faixa esperada = {pressao_min:.1f} - {pressao_max:.1f} PSI")

        if input("\nDeseja calcular para outro motor? (s/n): ").lower() != 's':
            break

if __name__ == "__main__":
    main()
