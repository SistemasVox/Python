# C:\Users\Marcelo\Documents\Python\Lotofacil\models\jogo.py
# Nome do arquivo: jogo.py

import random
import statistics

class Jogo:
    def __init__(self, dezenas=None):
        if dezenas:
            self.dezenas = sorted(dezenas)
        else:
            self.dezenas = self.gerar_jogo()

    def gerar_jogo(self):
        jogo = random.sample(range(1, 26), 15)
        jogo.sort()
        return jogo

    def avaliar_distribuicao_avg_gap(self):
        dezenas_ordenadas = sorted(self.dezenas)
        diffs = []
        for i in range(1, len(dezenas_ordenadas)):
            diffs.append(dezenas_ordenadas[i] - dezenas_ordenadas[i-1])
        avg_gap = sum(diffs) / len(diffs)

        if avg_gap < 1.2:
            return 1
        elif avg_gap < 1.4:
            return 2
        elif avg_gap < 1.6:
            return 3
        elif avg_gap < 1.8:
            return 4
        else:
            return 5

    def avaliar_distribuicao_std(self):
        dezenas_ordenadas = sorted(self.dezenas)
        std_dev = statistics.pstdev(dezenas_ordenadas)
        if std_dev < 4.0:
            return 1
        elif std_dev < 5.0:
            return 2
        elif std_dev < 6.0:
            return 3
        elif std_dev < 7.0:
            return 4
        else:
            return 5

    def __str__(self):
        return " ".join(map(str, self.dezenas))

class GeradorDeJogos:
    def gerar_jogo(self):
        jogo = random.sample(range(1, 26), 15)
        jogo.sort()
        return Jogo(jogo)
