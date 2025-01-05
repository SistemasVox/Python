# controllers/banco_controller.py
from models.banco_de_dados import BancoDeDados

class BancoController:
    def __init__(self, filename='banco_de_dados.txt'):
        self.banco = BancoDeDados(filename)

    def atualizar_banco(self, status_callback=None, progresso_callback=None):
        self.banco.criar_atualizar_banco_de_dados(
            status_callback=status_callback,
            progresso_callback=progresso_callback
        )

    def obter_info_banco(self):
        total_concursos = self.banco.obter_total_concursos()
        ultimo_concurso = self.banco.obter_ultimo_concurso()
        return total_concursos, ultimo_concurso

