# jogo.py
import random

class Jogo:
    def __init__(self, dezenas=None):
        if dezenas:
            self.dezenas = dezenas
        else:
            self.dezenas = self.gerar_jogo()

    def gerar_jogo(self):
        # Gera um novo jogo com 15 dezenas únicas de 01 a 25
        jogo = random.sample(range(1, 26), 15)
        jogo.sort()
        return jogo

    def __str__(self):
        # Representação em string do jogo
        return " ".join(map(str, self.dezenas))

class GeradorDeJogos:
    def gerar_jogo(self):
        # Gera um novo jogo com 15 dezenas únicas de 01 a 25
        jogo = random.sample(range(1, 26), 15)
        jogo.sort()
        return Jogo(jogo)
