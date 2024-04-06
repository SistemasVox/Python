import os

def clear():
    os.system('cls' if os.name == 'nt' else 'clear')

def calc(vf):
    cap = 92.5
    cad = 70
    vap = (vf * cad) / 100
    va92 = (vap * 100) / cap
    va = vf - va92
    return va92, va

while True:
    clear()
    op = input("Digite 'v' para volume final ou 'a' para água necessária: ")

    if op.lower() == 'v':
        vf = float(input("Volume final (litros): ").replace(',', '.'))
        va, agua = calc(vf)
        print(f"Para {vf if vf.is_integer() else round(vf, 3)}L a 70%, precisa de:")
        print(f"- {va if va.is_integer() else round(va, 3)}L de álcool 92,5%")
        print(f"- {agua if agua.is_integer() else round(agua, 3)}L de água")
    elif op.lower() == 'a':
        va92 = float(input("Álcool 92,5% disponível (litros): ").replace(',', '.'))
        agua = ((va92 * 92.5) / 70) - va92
        vf = va92 + agua
        print(f"Para obter uma solução a 70% com {va92 if va92.is_integer() else round(va92, 3)}L de álcool 92,5%, precisa adicionar {agua if agua.is_integer() else round(agua, 3)}L de água.")
        print(f"O volume final será de {vf if vf.is_integer() else round(vf, 3)}L.")
    else:
        print("Opção inválida. Digite 'v' ou 'a'.")

    cont = input("Outra operação? (s/n): ")
    if cont.lower() != 's':
        break
