# Definir os valores do empréstimo
valor_emprestimo = 4000
parcelas = 12
valor_parcela = 381

# Calcular o valor total pago
valor_total_pago = parcelas * valor_parcela

# Calcular a taxa de juros total
juros_total = valor_total_pago - valor_emprestimo

# Calcular a taxa de juros mensal
juros_mensal = juros_total / parcelas

# Calcular a porcentagem de juros total
porcentagem_juros_total = (juros_total / valor_emprestimo) * 100

# Calcular a porcentagem de juros mensal
porcentagem_juros_mensal = porcentagem_juros_total / parcelas

# Formatar os valores para incluir a pontuação das casas de milhar e usar vírgula para os centavos
valor_total_pago_formatado = f"{valor_total_pago:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
juros_total_formatado = f"{juros_total:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
juros_mensal_formatado = f"{juros_mensal:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
porcentagem_juros_total_formatado = f"{porcentagem_juros_total:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
porcentagem_juros_mensal_formatado = f"{porcentagem_juros_mensal:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

# Exibir os resultados
print(f"Valor total pago: R$ {valor_total_pago_formatado}")
print(f"Taxa de juros total: R$ {juros_total_formatado}")
print(f"Taxa de juros mensal: R$ {juros_mensal_formatado}")
print(f"Porcentagem de juros total: {porcentagem_juros_total_formatado}%")
print(f"Porcentagem de juros mensal: {porcentagem_juros_mensal_formatado}%")
