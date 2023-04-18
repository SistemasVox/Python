import mysql.connector

# Configurações de conexão com os servidores MySQL
local_config = {
    "user": "marcelo",
    "password": "q1w2",
    "host": "127.0.0.1",
    "port": "3306",
    "database": "loteria",
    "connect_timeout": 5
}

external_config = {
    "user": "xxxxxxxxxxxxxx",
    "password": "xxxxxxxxxxxxxx",
    "host": "xxxxxxxxxxx",
    "port": "3306",
    "database": "xxxxxxxxxxxxxx",
    "connect_timeout": 5
}

try:
    # Testar a conexão aos servidores MySQL
    local_conn = mysql.connector.connect(**local_config)
    local_conn.ping(reconnect=True)

    external_conn = mysql.connector.connect(**external_config)
    external_conn.ping(reconnect=True)

    if local_conn.is_connected() and external_conn.is_connected():
        print("Conexão estabelecida com sucesso em ambos os servidores MySQL.")

    # Obter a lista de concursos existentes em cada servidor
    local_cursor = local_conn.cursor()
    local_cursor.execute('SELECT concurso FROM loto ORDER BY concurso')
    local_concursos = [row[0] for row in local_cursor.fetchall()]

    external_cursor = external_conn.cursor()
    external_cursor.execute('SELECT concurso FROM loto ORDER BY concurso')
    external_concursos = [row[0] for row in external_cursor.fetchall()]

    # Identificar os concursos ausentes no servidor local
    ausentes_no_local = [concurso for concurso in external_concursos if concurso not in local_concursos]

    # Copiar os dados ausentes do servidor externo para o servidor local
    if(len(ausentes_no_local) > 0):
        num_rows_inserted = 0
        for concurso in ausentes_no_local:
            try:
                external_cursor.execute(f'SELECT * FROM loto WHERE concurso = {concurso}')
                data = external_cursor.fetchone()
                insert_query = f"INSERT INTO loto (concurso, data_concurso, dezenas) VALUES (%s, %s, %s)"
                local_cursor.execute(insert_query, data)
                num_rows_inserted += local_cursor.rowcount
                print(f"Concurso {concurso} inserido no servidor local.")
            except Exception as e:
                print(f"Ocorreu um erro ao inserir os dados do concurso {concurso}: {e}")

        # Commit e fechar as conexões com os servidores MySQL
        local_conn.commit()
        local_cursor.close()
        local_conn.close()
        external_cursor.close()
        external_conn.close()

        print(f"Total de {num_rows_inserted} linhas inseridas no servidor local.")
    else:
        print("Não há necessidade de atualizar, ambos atualizados.")

except mysql.connector.Error as e:
    print(f"Ocorreu um erro ao conectar aos servidores MySQL: {e}")

except Exception as e:
    print(f"Ocorreu um erro inesperado: {e}")



