def calc_mix(vol_final):
    # Constantes
    conc_alcool_puro = 92.5  # 92,5%
    conc_alcool_desejada = 70  # 70%
    
    # Volume de álcool puro necessário
    vol_alcool_puro = (vol_final * conc_alcool_desejada) / 100
    
    # Volume de álcool 92,5% necessário
    vol_alcool_92_5 = (vol_alcool_puro * 100) / conc_alcool_puro
    
    # Volume de água necessário
    vol_agua = vol_final - vol_alcool_92_5
    
    return vol_alcool_92_5, vol_agua

# Volume final desejado
vol_final = float(input("Volume final desejado em litros: "))

# Calcula os volumes necessários
vol_alcool, vol_agua = calc_mix(vol_final)

# Exibe os resultados
print(f"Para {vol_final}L de solução a 70%, você precisará de:")
print(f"- {vol_alcool:.2f}L de álcool 92,5%")
print(f"- {vol_agua:.2f}L de água")
